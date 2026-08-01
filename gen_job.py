#!/usr/bin/env python3
"""
gen_job.py — Offline job JSON generator
Usage:
  python3 gen_job.py --jd "job description text"
  python3 gen_job.py --jd-file jd.txt
  python3 gen_job.py --jd-file jd.txt --cv cv.pdf --cover cover.pdf
  python3 gen_job.py --jd-file jd.txt -o my_job.json
  python3 gen_job.py --jd-file jd.txt --push          # push directly to tracker
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from io import StringIO


# ── PDF extraction ────────────────────────────────────────────────────────────

def pdf_to_text(path: str) -> str:
    try:
        from pdfminer.high_level import extract_text
        return extract_text(path) or ""
    except Exception as e:
        print(f"[warn] Could not read PDF {path}: {e}", file=sys.stderr)
        return ""


# ── Local AI gap-filler (offline, via Ollama) ──────────────────────────────────
#
# The regex extractors below are the primary, fast, deterministic path and
# handle structured job postings well. For unstructured/prose-style JDs where
# the regexes come up empty, we optionally ask a small local LLM (run entirely
# offline via Ollama, free, no API keys) to fill in ONLY the fields the regex
# missed. The AI is never used to override a value the regex already found —
# see generate() below.

AI_MODEL = "qwen2.5:0.5b"
_OLLAMA_URL = "http://localhost:11434"
_AI_FIELDS = ("company", "role", "location", "salary_range",
              "recruiter_name", "recruiter_email", "recruiter_phone")

# The model is deliberately NOT asked to write field values itself — a small
# model asked to "generate" a company/role/salary will confidently invent one
# when it's unsure (confirmed repeatedly in testing: fabricated phone numbers,
# a salary range for a JD with no numbers at all, "not provided" taken
# literally as a value, etc). Instead it only CLASSIFIES which line number
# (of the JD split into numbered lines) contains each field — a much easier
# and more reliable task for a small model — and the actual value is then
# always the literal, unmodified text of that line, copied by our own code.
# This makes true fabrication structurally impossible: the worst failure mode
# left is picking the wrong existing line, not inventing content that isn't
# in the JD at all — see the per-field shape checks in ai_extract_fields().
_AI_SYSTEM_PROMPT = (
    "You are given a job posting split into numbered lines (it may be in English, German, "
    "or a mix). For each of these fields — company, role, location, salary_range, "
    "recruiter_name, recruiter_email, recruiter_phone — reply with ONLY the line number "
    "that contains that information, or null if no line contains it. "
    "NEVER write the value yourself, ONLY point to a line number. "
    "Only pick a line if it is CLEARLY and DIRECTLY that field (e.g. the line IS a job "
    "title, IS a company name, IS a phone number). If the field is only mentioned in "
    "passing inside a longer sentence about something else, or you are not confident, "
    "use null instead of guessing. "
    'Output ONLY one JSON object, e.g. {"company": 4, "role": 1, "location": null, '
    '"salary_range": 3, "recruiter_name": null, "recruiter_email": null, "recruiter_phone": null}.'
)

# Second pass: a much simpler yes/no check per field than the original
# multi-field line selection — asking "is this line REALLY the company name?"
# in isolation is an easier judgment for a small model than "which of these
# 80 lines is the company", so it catches misplacements the first pass missed
# (e.g. picking a sentence that merely mentions the company in passing).
_AI_VERIFY_PROMPT = (
    "You will be shown field:line pairs from a job posting. For each pair, answer "
    "true ONLY if that exact line clearly and directly IS that field's value (e.g. it "
    "IS a job title, IS a company name, IS a phone number). Answer false if the line "
    "merely mentions the topic in passing, is a sentence about something else, or "
    "doesn't cleanly represent that field on its own. "
    'Output ONLY one JSON object, e.g. {"company": true, "role": false, ...}.'
)

_LABEL_STRIP_RE = re.compile(
    r"(?i:^(?:company|employer|firma|unternehmen|arbeitgeber|position|role|job\s*title|"
    r"title|stelle|jobbezeichnung|stellenbezeichnung|location|standort|salary|gehalt|"
    r"vergütung|compensation|contact(?:\s*person)?|ansprechpartner(?:in)?|phone|telephone|"
    r"tel|mobile|telefon|handy|rufnummer|email|e-mail)\s*:\s*)(.+)$"
)



# Set by generate() after each call so callers (e.g. app.py) can show the user
# whether/why the AI gap-filler ran. One of:
#   "skipped (regex found company and role)" / "ok" / "empty text" /
#   "Ollama not available" / "AI extraction failed: <reason>" / "model returned non-object JSON"
LAST_AI_STATUS = ""


def _ollama_request(path: str, payload: dict = None, method: str = "GET",
                    timeout: float = 5, max_response_bytes: int = 64 * 1024):
    """Make a request to the local Ollama server. Enforces a response size
    cap (default 64 KB) to prevent unbounded memory consumption from a
    malformed or runaway model response."""
    url = _OLLAMA_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise ValueError(f"Ollama response exceeded {max_response_bytes} bytes")
            return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Ollama returned invalid JSON: {e}") from e


def _is_ollama_up(timeout: float = 1.5) -> bool:
    try:
        _ollama_request("/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def _model_available(model: str, timeout: float = 3) -> bool:
    try:
        tags = _ollama_request("/api/tags", timeout=timeout)
        return any(m.get("name") == model for m in tags.get("models", []))
    except Exception:
        return False


def _start_ollama_server() -> bool:
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False  # ollama isn't installed
    for _ in range(12):  # give it ~6s to come up
        time.sleep(0.5)
        if _is_ollama_up():
            return True
    return False


def pull_ai_model_blocking(model: str = AI_MODEL, timeout: float = 600) -> bool:
    """Blocking model download — only meant to be called from start.sh / the CLI
    setup path, NEVER from a live web request (a first-time pull can take
    minutes, which would make the UI look stuck)."""
    try:
        subprocess.run(
            ["ollama", "pull", model], timeout=timeout, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


# Cache expiry: re-verify Ollama is alive every 5 minutes so a mid-session
# crash doesn't silently disable AI for the rest of the process lifetime.
_ollama_ready_at = 0.0
_OLLAMA_CACHE_TTL = 300  # seconds


def ensure_ai_ready(model: str = AI_MODEL) -> bool:
    """Best-effort, bounded check that a local Ollama server is running and
    the model is already pulled. Will start the server if it's stopped (fast,
    ~6s max) but will NOT download the model on demand — that only happens
    via start.sh or pull_ai_model_blocking(), so a request can never block for
    minutes waiting on a download. Re-verifies every 5 minutes."""
    global _ollama_ready_at
    now = time.monotonic()
    if _ollama_ready_at and (now - _ollama_ready_at) < _OLLAMA_CACHE_TTL:
        return True
    if not _is_ollama_up():
        if not _start_ollama_server():
            _ollama_ready_at = 0.0
            return False
    if not _model_available(model):
        _ollama_ready_at = 0.0
        return False  # don't block a request on a multi-minute download
    _ollama_ready_at = now
    return True


def _numbered_lines(text: str, max_lines: int = 80) -> list:
    """Split JD text into non-empty lines for the model to point at. Capped
    so the prompt stays small/fast — company/role/location/salary/recruiter
    info is essentially always within a JD's first ~80 lines."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()][:max_lines]


def _strip_known_label(line: str) -> str:
    """If a line is literally 'Label: Value' (e.g. 'Telefon: 0911 123'),
    return just the Value. Otherwise return the line unchanged. Either way
    the result is always literal JD text — this only trims an obvious label
    prefix, it never rewrites or invents anything."""
    m = _LABEL_STRIP_RE.match(line)
    return m.group(1).strip() if m else line


def _line_number(raw) -> int:
    """Tolerate the model returning a line number as an int, a float, or a
    numeric string; anything else (null, a written-out value, garbage) is
    treated as 'no line selected'. Negative numbers are always rejected."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n if n > 0 else 0
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit() and int(s) > 0:
            return int(s)
    return 0


def _sanitize_value(v: str) -> str:
    """Clean up extracted values: strip whitespace, remove stray punctuation
    at boundaries, collapse internal whitespace, and strip HTML entities."""
    if not v:
        return ""
    v = v.strip().strip(",.;:\"'")
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"&(?:amp|lt|gt|quot|apos|nbsp);?", " ", v)
    return v.strip()


# Per-field shape sanity checks. Because values now always come from a real
# line the model pointed to, the only realistic failure mode left is picking
# the WRONG line (e.g. a job reference number mistaken for a phone number) —
# these checks catch a selected line that clearly doesn't look like the kind
# of value the field expects, rather than checking for outright invention.
_MONEY_RE = re.compile(r"(?i:[€$£]|EUR|USD|GBP|\bk\b|\d{2,3}[.,]\d{3})")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$")
_COMPANY_SUFFIX_RE = re.compile(r"(?i:\bGmbH\b|\bAG\b|\bInc\.?\b|\bLLC\b|\bLtd\.?\b|\bCorp\.?\b|\bSE\b|\bPLC\b)")


def _looks_like_salary(v: str) -> bool:
    if not v:
        return False
    # Must contain at least 2 digits (a real salary has numbers)
    digits = re.findall(r"\d", v)
    if len(digits) < 2:
        return False
    # Must match a currency/money pattern
    if not _MONEY_RE.search(v):
        return False
    # Reject noise phrases that aren't actual salary figures
    noise = re.compile(r"(?i:^(?:and|competitive|negotiable|based on)\b)")
    if noise.search(v.strip()):
        return False
    return True


def _looks_like_phone(v: str) -> bool:
    digits = re.sub(r"\D", "", v)
    return 6 <= len(digits) <= 16


def _looks_like_email(v: str) -> bool:
    return bool(_EMAIL_RE.match(v.strip()))


def _looks_like_person_name(v: str) -> bool:
    if not v or "@" in v or _COMPANY_SUFFIX_RE.search(v):
        return False
    # Reject names that are all digits/symbols
    if not re.search(r"[a-zA-ZäöüÄÖÜß]", v):
        return False
    words = v.split()
    return 1 <= len(words) <= 4 and len(v) <= 50


def _looks_like_company(v: str) -> bool:
    """Reject values that clearly aren't company names: too short, all digits,
    noise words, bullet points, salary/requirement text, or too many words."""
    if not v or len(v) < 2 or len(v) > 60:
        return False
    if not re.search(r"[a-zA-ZäöüÄÖÜß]", v):
        return False
    # Max ~6 words for a company name (e.g. "Deutsche Bank Technology Center GmbH")
    if len(v.split()) > 6:
        return False
    # Reject bullet points / list items
    if v.lstrip().startswith(("-", "•", "*", "–", "—")):
        return False
    noise = {"apply", "apply now", "click here", "submit", "job", "position",
             "role", "n/a", "not provided", "none", "null", "tbd", "various",
             "requirements", "responsibilities", "what we offer", "benefits",
             "about us", "about the role", "your profile", "our offer"}
    if v.strip().lower() in noise:
        return False
    # Reject if it contains salary/compensation keywords
    salary_noise = re.compile(r"(?i:\bnegotiable\b|\bcompetitive\b|\bbased on\b|\bbenefits\b|\bexperience\b|\byears\b|\brequirements?\b)")
    if salary_noise.search(v):
        return False
    return True


def _looks_like_role(v: str) -> bool:
    """Reject values that clearly aren't job titles."""
    if not v or len(v) < 3 or len(v) > 80:
        return False
    if not re.search(r"[a-zA-ZäöüÄÖÜß]", v):
        return False
    # Max ~8 words for a role title
    if len(v.split()) > 8:
        return False
    # Reject bullet points / list items
    if v.lstrip().startswith(("-", "•", "*", "–", "—")):
        return False
    noise = {"apply", "apply now", "click here", "submit", "company",
             "n/a", "not provided", "none", "null", "tbd",
             "requirements", "responsibilities", "what we offer", "benefits"}
    if v.strip().lower() in noise:
        return False
    # Reject salary/compensation text mistaken as role
    salary_noise = re.compile(r"(?i:\bnegotiable\b|\bcompetitive\b|\bbased on\b|\bbenefits\b)")
    if salary_noise.search(v):
        return False
    return True


def _looks_like_location(v: str) -> bool:
    """Reject values that clearly aren't locations."""
    if not v or len(v) < 2 or len(v) > 80:
        return False
    if not re.search(r"[a-zA-ZäöüÄÖÜß]", v):
        return False
    return True


# A real company/role/location heading is a short label, never a narrative
# sentence — reject a picked line that reads like prose instead.
_PROSE_PRONOUNS_RE = re.compile(r"(?i:\b(?:we|we're|we'll|you|you'll|you're|our|us|i'm)\b)")

_DESCRIPTIVE_SENTENCE_RE = re.compile(
    r"(?i:\bis\s+hiring\b|\bare\s+hiring\b|\bis\s+looking\s+for\b|\bis\s+seeking\b|"
    r"\bare\s+seeking\b|\bhas\s+been\b|\bhave\s+been\b|\bis\s+a\s+|\bis\s+an\s+|"
    r"\bwas\s+founded\b|\bis\s+expanding\b)"
)


def _looks_like_a_heading(v: str) -> bool:
    if not v:
        return True  # nothing to check — an empty pick is always fine
    if "@" in v:
        return False
    if _PROSE_PRONOUNS_RE.search(v) or _DESCRIPTIVE_SENTENCE_RE.search(v):
        return False
    if v.rstrip().endswith((".", "!", "?")) and len(v.split()) > 8:
        return False
    return True


def _validate_and_clean(cleaned: dict) -> dict:
    """Apply all shape/sanity checks to AI-extracted fields in one place.
    Deterministic post-processing that catches the model's common mistakes."""

    # Sanitize all values
    for field in _AI_FIELDS:
        cleaned[field] = _sanitize_value(cleaned.get(field, ""))

    # Salary shape check
    if cleaned.get("salary_range") and not _looks_like_salary(cleaned["salary_range"]):
        cleaned["salary_range"] = ""

    # A company/role/location line that IS a salary figure is never legitimate
    for field in ("company", "role", "location"):
        if cleaned.get(field) and _looks_like_salary(cleaned[field]):
            cleaned[field] = ""

    # Phone / email / name shape checks
    if cleaned.get("recruiter_phone") and not _looks_like_phone(cleaned["recruiter_phone"]):
        cleaned["recruiter_phone"] = ""
    if cleaned.get("recruiter_email") and not _looks_like_email(cleaned["recruiter_email"]):
        cleaned["recruiter_email"] = ""
    if cleaned.get("recruiter_name") and not _looks_like_person_name(cleaned["recruiter_name"]):
        cleaned["recruiter_name"] = ""

    # Company / role / location specific shape checks
    if cleaned.get("company") and not _looks_like_company(cleaned["company"]):
        cleaned["company"] = ""
    if cleaned.get("role") and not _looks_like_role(cleaned["role"]):
        cleaned["role"] = ""
    if cleaned.get("location") and not _looks_like_location(cleaned["location"]):
        cleaned["location"] = ""

    # Heading check: company/role/location should be short labels, not prose
    for field in ("company", "role", "location"):
        val = cleaned.get(field, "")
        if val and not _looks_like_a_heading(val):
            cleaned[field] = ""

    # Duplicate line collision: if any two of company/role/location point to the
    # same text, the model likely mis-assigned one of them — keep the more
    # specific field and blank the other.
    for f1, f2, keep in [
        ("company", "location", "location"),
        ("company", "role", "role"),
        ("role", "location", "location"),
    ]:
        if cleaned.get(f1) and cleaned.get(f1) == cleaned.get(f2):
            cleaned[f1 if keep == f2 else f2] = ""

    # Recruiter fields duplicating into company/role/location
    rec_email = cleaned.get("recruiter_email", "")
    rec_phone = cleaned.get("recruiter_phone", "")
    for field in ("company", "role", "location"):
        val = cleaned.get(field, "")
        if val and rec_email and rec_email in val:
            cleaned[field] = ""
        if val and rec_phone and rec_phone in val:
            cleaned[field] = ""

    return cleaned


def ai_extract_fields(text: str, model: str = AI_MODEL, timeout: float = 60) -> tuple:
    """Ask the local model WHICH LINE of the JD contains each field (never to
    write the value itself), then extract that field's value as the literal
    text of that line. Returns (dict|None, status_str). Never raises — any
    failure (Ollama not installed/running, model missing, timeout, malformed
    response) is reported via the status string so callers can fall back
    gracefully. Retries once on malformed JSON."""
    if not text or not text.strip():
        return None, "empty text"

    lines = _numbered_lines(text)
    if not lines:
        return None, "no usable lines"

    if not ensure_ai_ready(model):
        return None, "Ollama not available (not installed, not running, or model could not be pulled)"

    numbered_text = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines))
    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 256},
        "messages": [
            {"role": "system", "content": _AI_SYSTEM_PROMPT},
            {"role": "user", "content": numbered_text[:6000]},
        ],
    }

    max_attempts = 2
    last_error = ""
    for attempt in range(max_attempts):
        try:
            resp = _ollama_request("/api/chat", payload, method="POST", timeout=timeout)
            content = (resp.get("message") or {}).get("content", "")
            if not content.strip():
                last_error = "model returned empty response"
                continue

            # Try to extract JSON even if model wraps it in markdown fences
            json_text = content.strip()
            if json_text.startswith("```"):
                json_text = re.sub(r"^```\w*\s*", "", json_text)
                json_text = re.sub(r"\s*```$", "", json_text)

            data = json.loads(json_text)
            if not isinstance(data, dict):
                last_error = "model returned non-object JSON"
                continue

            # Successfully parsed — build the cleaned dict
            cleaned = {}
            for field in _AI_FIELDS:
                n = _line_number(data.get(field))
                cleaned[field] = _strip_known_label(lines[n - 1]) if 1 <= n <= len(lines) else ""

            cleaned = _validate_and_clean(cleaned)

            # Second pass: re-confirm each surviving candidate
            cleaned = _verify_selections(cleaned, model, timeout)

            return cleaned, "ok"

        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"JSON parse error (attempt {attempt + 1}): {e}"
            if attempt < max_attempts - 1:
                time.sleep(0.5)  # brief pause before retry
            continue
        except urllib.error.URLError as e:
            global _ollama_ready_at
            _ollama_ready_at = 0.0  # server may have gone away; re-check next call
            return None, f"Ollama request failed: {e}"
        except Exception as e:
            return None, f"AI extraction failed: {e}"

    return None, f"AI extraction failed after {max_attempts} attempts: {last_error}"


def _verify_selections(cleaned: dict, model: str, timeout: float) -> dict:
    """Ask the model to confirm each candidate value actually IS the field it
    was assigned to. Best-effort: if this second call fails for any reason
    (timeout, Ollama hiccup, bad JSON), the original picks are kept as-is
    rather than losing otherwise-good results to a flaky follow-up request."""
    candidates = {f: v for f, v in cleaned.items() if v}
    if not candidates:
        return cleaned
    prompt = "\n".join(f"{f}: {v}" for f, v in candidates.items())
    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 128},
        "messages": [
            {"role": "system", "content": _AI_VERIFY_PROMPT},
            {"role": "user", "content": prompt[:4000]},
        ],
    }
    try:
        resp = _ollama_request("/api/chat", payload, method="POST", timeout=timeout)
        content = (resp.get("message") or {}).get("content", "")
        if not content.strip():
            return cleaned

        json_text = content.strip()
        if json_text.startswith("```"):
            json_text = re.sub(r"^```\w*\s*", "", json_text)
            json_text = re.sub(r"\s*```$", "", json_text)

        data = json.loads(json_text)
        if not isinstance(data, dict):
            return cleaned
        for field in candidates:
            verdict = data.get(field)
            rejected = verdict is False or (
                isinstance(verdict, str) and verdict.strip().lower() in ("false", "no", "0")
            )
            if rejected:
                cleaned[field] = ""
    except Exception:
        pass  # verification is best-effort; keep the first pass's picks
    return cleaned


# ── Field extractors ──────────────────────────────────────────────────────────

def _first(patterns, text, flags=re.IGNORECASE):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip()
    return ""


def extract_company(text: str) -> str:
    # NOTE: intentionally case-sensitive (no IGNORECASE). Case-insensitive matching
    # would make `[A-Z]`/`[a-z]` match any letter, defeating the capitalization
    # checks below and causing them to latch onto random prose (e.g. the word
    # "company" used generically in a sentence rather than as a label).
    patterns = [
        r"\[([A-Z][A-Za-z0-9 &.,'\-]{1,58})\]\(https?://",                     # [Company Name](url) markdown link
        r"^(.{2,60}?)\s*\|",                                                    # "Title | Company | Location" header line
        r"(?i:^\s*(?:company|employer|firma|unternehmen|arbeitgeber)\s*:\s*)([^\n,|]{2,60})",  # explicit "Company:"/"Firma:" label
        r"^\s*([A-Z][A-Za-z0-9&.,'\-]{1,55}\s(?:GmbH|AG|Inc\.?|LLC|Ltd\.?|Corp\.?|SE|BV|NV|SAS|AB|PLC))\.?\s*$",  # standalone legal-entity line
        r"([A-Z][A-Za-z0-9&.,'\- ]{1,55}(?:GmbH|AG|Inc\.?|LLC|Ltd\.?|Corp\.?|SE|BV|NV|SAS|AB|PLC))\s+(?:is|are|has been|was)\s+(?:looking|seeking|hiring|searching|expanding)",  # "Acme GmbH is looking for..."
        r"(?i:\bat\s+)([A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*){0,4})(?=[,.\n])",  # "... at Company,"
    ]
    for p in patterns:
        m = re.search(p, text, re.MULTILINE)
        if m:
            val = m.group(1).strip().strip(",.")
            if val:
                return val
    return ""


def extract_role(text: str) -> str:
    # Explicit label, e.g. "Position: Test Engineer" / "Stelle: ..." / "Jobbezeichnung: ..."
    m = re.search(
        r"(?i:^\s*(?:position|role|job\s*title|title|stelle|jobbezeichnung|stellenbezeichnung)\s*:\s*)([^\n|]{3,90})",
        text, re.MULTILINE,
    )
    if m:
        return m.group(1).strip()

    # "We are hiring/seeking/looking for a <Role>"
    m = re.search(r"(?i:\b(?:hiring|seeking|looking for)\b(?:\s+a|\s+an)?\s+)([A-Z][^\n,.]{3,79})", text)
    if m:
        val = m.group(1).strip()
        val = re.split(r"(?i)\s+(?:to\s+|for\s+|in\s+|at\s+|with\s+|who\s+)", val)[0].strip()
        if _looks_like_role(val):
            return val

    # Fallback: structured postings almost always lead with the title as the very
    # first line. Skip it if that line reads like prose (a sentence opener) rather
    # than a heading, since unstructured JDs often start with "We're a ..." /
    # "Our team is looking for ..." instead of the job title.
    _SENTENCE_OPENERS = re.compile(
        r"(?i:^(?:we'?re|we\s+are|our\s|this\s|as\s+an?\b|the\s|you\s+will\b|join\s+))"
    )
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if (4 <= len(line) <= 100 and not line.endswith((".", ":"))
                and not _SENTENCE_OPENERS.match(line)):
            return line
        break
    return ""


_COUNTRIES = (r"(?:Germany|USA|United States|UK|United Kingdom|France|Netherlands|"
              r"Switzerland|Austria|India|Canada|Spain|Italy|Poland|Sweden|Ireland)")

# Real US state/territory abbreviations only — NOT a generic [A-Z]{2,3}, which would
# also match unrelated tech acronyms like "CI" (from "CI/CD") or "QA" and misread
# something like "Docker, CI/CD" as a "City, State" location.
_US_STATES = (
    r"(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    r"MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
)


def extract_location(text: str) -> str:
    loc = _first([
        r"(?i:^\s*(?:location|standort|place|site)\s*:\s*)([^\n,|]{2,60})",
        r"(?i:\b(?:in|located in)\s+)([A-Z][A-Za-z0-9 \t.,'\-]{2,40}(?:,\s*(?:" + _COUNTRIES + r"|" + _US_STATES + r")))",
        r"(?i:\b(?:location|standort)\s*:\s*)([A-Z][A-Za-z0-9 \t.,'\-]{2,40})",
    ], text)
    if not loc:
        # Fallback line-matching for "City - Remote" or "City - Full-time"
        m = re.search(
            r"^([A-Z][A-Za-z.\-]{2,30})\s*[–—-]\s*(?i:permanent|contract|full[- ]?time|part[- ]?time|remote|hybrid|on-?site)\b",
            text, re.MULTILINE,
        )
        if m:
            loc = m.group(1).strip()
    # append a hybrid/remote/onsite modifier if it's mentioned but not already in loc
    mode = re.search(r"(?i:\b(hybrid|remote|on-?site|onsite)\b)", text)
    if loc and mode and mode.group(1).lower() not in loc.lower():
        loc = f"{loc} ({mode.group(1).title()})"
    return loc


def extract_salary(text: str) -> str:
    # Prefer an explicit single range if the JD states one directly.
    range_val = _first([
        r"(\$[\d,]+\s*(?:–|-|to)\s*\$[\d,]+(?:\s*(?:k|K|USD|EUR|€)?)?(?:\s*/\s*(?:year|yr|annual))?)",
        r"(€[\d,.]+\s*(?:–|-|to)\s*€[\d,.]+(?:\s*(?:k|K))?(?:\s*/\s*(?:year|yr|annual))?)",
        r"([\d,]+\s*(?:–|-|to)\s*[\d,]+\s*(?:EUR|USD|\$|€)(?:\s*/\s*(?:year|yr|annual))?)",
        r"(?:gross|brutto)[^\n]{0,20}([\d,.]+\s*(?:EUR|USD|€|\$)[^\n]{0,30})",
    ], text)
    if range_val:
        return range_val

    # Some postings list several pay bands instead of a single range, e.g.:
    #   SALARY:
    #   - Mid-Level: €70,000/year
    #   - Senior: €90,000/year
    # Collapse those into one min–max range rather than truncating on a comma.
    m = re.search(r"(?i:(?:salary|compensation|gehalt|vergütung)\s*:?\s*\n)((?:[^\n]*\n?){1,6})", text)
    section = m.group(1) if m else ""
    amounts = re.findall(r"([€$])\s?([\d.,]{4,10})", section)
    if len(amounts) >= 2:
        currency = amounts[0][0]
        nums = [float(a[1].replace(".", "").replace(",", "")) for a in amounts]
        lo, hi = f"{int(min(nums)):,}", f"{int(max(nums)):,}"
        suffix = "/year" if re.search(r"(?i:/\s*year|per\s*year|annual)", section) else ""
        return f"{currency}{lo} - {currency}{hi}{suffix}"

    raw_val = _first([
        r"(?:salary|gehalt|vergütung)[:\s]+([^\n|]{3,60})",
        r"compensation[:\s]+([^\n|]{3,60})",
    ], text)
    if raw_val and _looks_like_salary(raw_val):
        return raw_val
    return ""


def extract_source(text: str, job_url: str = "") -> str:
    for platform in ["LinkedIn", "Indeed", "Glassdoor", "XING", "StepStone",
                     "Monster", "Seek", "Naukri", "Greenhouse", "Lever", "Workday"]:
        if platform.lower() in text.lower():
            return platform
    if job_url:
        m = re.search(r"https?://(?:www\.)?([^/\s]+)", job_url)
        if m:
            return m.group(1)
    return ""


def extract_job_url(text: str) -> str:
    # Exclude "(" / ")" from the URL body so markdown-style links like
    # "[Apply now](https://example.com/job/123)" don't swallow the trailing ")".
    url = _first([
        r"(https?://[^\s\"'<>()]+(?:job|career|position|vacancy|posting|apply)[^\s\"'<>()]*)",
        r"(https?://(?:www\.)?(?:linkedin|indeed|glassdoor|greenhouse|lever|workday)[^\s\"'<>()]*)",
        r"apply[^\n]{0,30}(https?://[^\s\"'<>()]+)",
    ], text)
    return url.rstrip(".,;:'\"") if url else url


_PHONE_CORE = r"\+?[\d][\d\s().\-/]{6,18}\d"


def _clean_phone(raw: str) -> str:
    return re.sub(r"[ \t]+", " ", raw.strip().strip(",.;:"))


def extract_phone(text: str) -> str:
    """Find the recruiter's contact number, if the JD mentions one.
    Many of these postings are in German or mixed EN/DE, so labels include
    the German equivalents (Telefon, Handy, Ansprechpartner) alongside English."""
    # 1. Explicitly labeled, e.g. "Telephone: +49 7946 9194181" / "Telefon: ..."
    m = re.search(
        r"(?i:\b(?:telephone|phone|tel|mobile|contact\s*no\.?|call|telefon|handy|rufnummer)\b"
        r"\s*(?:number|nummer)?\s*[:.]?\s*)"
        r"(" + _PHONE_CORE + r")",
        text,
    )
    if m:
        return _clean_phone(m.group(1))

    # 2. Same line as a "Contact:"/"Ansprechpartner:" header,
    #    e.g. "Contact: Name | email | 0731 96538563"
    m = re.search(r"(?i:^[ \t]*(?:contact\s*(?:person)?|ansprechpartner(?:in)?)\s*:.*)$", text, re.MULTILINE)
    if m:
        line_m = re.search(_PHONE_CORE, m.group(0))
        if line_m:
            return _clean_phone(line_m.group(0))

    # 3. A standalone number with an explicit country code, e.g. "+49 7946 9194181"
    m = re.search(r"(\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4})", text)
    if m:
        return _clean_phone(m.group(1))

    return ""


def extract_applicant_contacts(cv_text: str = "", cover_text: str = "") -> tuple:
    combined = f"{cv_text} {cover_text}"
    if not combined.strip():
        return set(), set()
    emails = set(re.findall(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})", combined.lower()))
    phones = set()
    for m in re.finditer(_PHONE_CORE, combined):
        p = _clean_phone(m.group(0))
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 6:
            phones.add(digits)
    return emails, phones


def extract_recruiter(text: str, applicant_emails: set = None, applicant_phones: set = None) -> tuple:
    if applicant_emails is None:
        applicant_emails = set()
    if applicant_phones is None:
        applicant_phones = set()

    name = ""
    m = re.search(
        r"(?i:\b(?:contact(?:\s+person)?|ansprechpartner(?:in)?|recruiter|hiring\s+manager)\b)[ \t]*:[ \t]*([A-Z][a-zA-Z.'\-]+(?:[ \t]+[A-Z][a-zA-Z.'\-]+){1,2})",
        text,
    )
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r"\b(?:Mrs?\.?|Ms\.?|Dr\.?)[ \t]+([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})", text)
        if m:
            name = m.group(1).strip()

    email_matches = re.findall(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})", text)
    email = ""
    for em in email_matches:
        em_lower = em.lower()
        if em_lower in applicant_emails:
            continue
        if any(em_lower.startswith(prefix) for prefix in ("jobs", "careers", "recruiting", "karriere", "bewerbung", "hr", "contact", "info", "talent")):
            email = em
            break
        m_near = re.search(r"(?i:\b(?:contact|ansprechpartner|apply|bewerbung|recruiter|hr)\b[^\n]{0,80}" + re.escape(em) + r"|" + re.escape(em) + r"[^\n]{0,80}\b(?:contact|ansprechpartner|apply)\b)", text)
        if m_near:
            email = em
            break

    if not email and email_matches:
        for em in email_matches:
            if em.lower() not in applicant_emails:
                email = em
                break

    phone = extract_phone(text)
    if phone:
        digits = re.sub(r"\D", "", phone)
        if digits in applicant_phones:
            phone = ""

    return name, email, phone


def extract_tags(text: str) -> str:
    tag_pool = [
        # languages
        "Python","C++","C#","Java","JavaScript","TypeScript","Go","Rust","Scala","MATLAB","SQL",
        # frameworks/tools
        "PyTest","pytest","Docker","Kubernetes","Jenkins","GitLab","GitHub","Git","Jira","TestRail",
        "REST","FastAPI","Flask","Django","Spring","Node.js","React","Angular",
        # automotive/embedded
        "CAN","UDS","DoIP","XCP","XETK","ODX","AUTOSAR","ECU","HIL","SIL","HiL","MIL",
        "Trace32","TC32","CAPL","CANalyzer","CANoe","DTS","Monaco","ADAS",
        "Adaptive Cruise Control","Automatic Emergency Braking",
        # OS/infra
        "Linux","QNX","RTOS","Embedded Linux","CI/CD","DevOps",
        # domains
        "test automation","automotive","embedded","machine learning","computer vision",
        "radar","lidar","LiDAR","camera","ultrasonic","sensor fusion",
    ]
    found = []
    tl = text.lower()
    for t in tag_pool:
        if t.lower() in tl and t not in found:
            found.append(t)
    return ",".join(found[:12])


def _is_salary_above_50k_euro(salary_text: str, jd_text: str = "") -> bool:
    """Check if salary text (or JD text) mentions a salary of 50k EUR or higher."""
    target_text = f"{salary_text}\n{jd_text}"
    if not target_text.strip():
        return False

    # 1. Matches like 50k, 50K, 60k, 100k near € or EUR or Euro, or in salary_text
    k_matches = re.findall(r"(?i)(?:€|EUR|Euro)?\s*(\d{2,3})\s*k\b\s*(?:€|EUR|Euro)?", target_text)
    for m in k_matches:
        try:
            val = float(m) * 1000
            if val >= 50000:
                return True
        except ValueError:
            pass

    # 2. Matches like 50,000 or 50.000 or 50000 or 65.000
    num_matches = re.findall(r"(?i)(?:€|EUR|Euro)?\s*(\d{2,3}[\.,]\d{3}|\d{5,6})\s*(?:€|EUR|Euro)?", target_text)
    for m in num_matches:
        clean_num = m.replace(".", "").replace(",", "")
        try:
            val = float(clean_num)
            if val >= 50000:
                return True
        except ValueError:
            pass

    return False


def extract_interest(jd_text: str, cv_text: str = "", cover_text: str = "", salary_text: str = "") -> str:
    """Rate 1-5 by keyword overlap between JD requirements and CV/cover letter.
    If salary is above 50k Euro, set interest score to at least 3 (Medium)."""
    applicant_text = f"{cv_text} {cover_text}".strip()
    if not applicant_text:
        base_score = 3
    else:
        jd_words = set(re.findall(r'\b[a-z]{3,}\b', jd_text.lower()))
        cv_words  = set(re.findall(r'\b[a-z]{3,}\b', applicant_text.lower()))
        overlap = len(jd_words & cv_words)
        if overlap > 120: base_score = 5
        elif overlap > 80: base_score = 4
        elif overlap > 50: base_score = 3
        elif overlap > 25: base_score = 2
        else: base_score = 1

    if _is_salary_above_50k_euro(salary_text, jd_text):
        if base_score < 3:
            base_score = 3

    return str(base_score)


def extract_resume_version(cv_path: str) -> str:
    if not cv_path:
        return ""
    base = os.path.basename(cv_path)
    return os.path.splitext(base)[0]


# ── Main ──────────────────────────────────────────────────────────────────────

def generate(jd_text: str, cv_text: str = "", cover_text: str = "",
             cv_path: str = "", cover_path: str = "") -> dict:
    global LAST_AI_STATUS
    text = jd_text  # primary source for most fields

    app_emails, app_phones = extract_applicant_contacts(cv_text, cover_text)

    company  = extract_company(text)
    role     = extract_role(text)
    location = extract_location(text)
    salary   = extract_salary(text)
    job_url  = extract_job_url(text)
    source   = extract_source(text, job_url)
    tags     = extract_tags(text)
    rec_name, rec_email, rec_phone = extract_recruiter(text, app_emails, app_phones)

    # Regex extraction is the fast, accurate path for structured postings and is
    # always authoritative — the local AI model is ONLY asked to fill in fields
    # regex left blank (it's small/fast but noticeably less reliable, so it must
    # never override a value regex already found). This also means well-formed
    # JDs never pay the AI latency cost at all.
    if not company or not role:
        ai_result, status = ai_extract_fields(text)
        LAST_AI_STATUS = status
        if ai_result:
            company   = company   or ai_result.get("company", "")
            role      = role      or ai_result.get("role", "")
            location  = location  or ai_result.get("location", "")
            salary    = salary    or ai_result.get("salary_range", "")
            ai_rec_email = ai_result.get("recruiter_email", "")
            if ai_rec_email and ai_rec_email.lower() not in app_emails:
                rec_email = rec_email or ai_rec_email
            ai_rec_phone = ai_result.get("recruiter_phone", "")
            if ai_rec_phone and re.sub(r"\D", "", ai_rec_phone) not in app_phones:
                rec_phone = rec_phone or ai_rec_phone
            rec_name = rec_name or ai_result.get("recruiter_name", "")
    else:
        LAST_AI_STATUS = "skipped (regex found company and role)"

    interest = extract_interest(text, cv_text, cover_text, salary_text=salary)

    if rec_email and rec_email.lower() in app_emails:
        rec_email = ""
    if rec_phone and re.sub(r"\D", "", rec_phone) in app_phones:
        rec_phone = ""

    resume_v = extract_resume_version(cv_path)

    return {
        "company":            company,
        "role":               role,
        "status":             "applied",
        "applied_date":       date.today().isoformat(),
        "source":             source,
        "location":           location,
        "salary_range":       salary,
        "jd":                 jd_text,
        "job_url":            job_url,
        "tags":               tags,
        "interest_score":     interest,
        "notes":              "",
        "next_action":        "Follow up in 2 weeks",
        "follow_up_date":     "",
        "offer_deadline":     "",
        "resume_version":     resume_v,
        "recruiter_name":     rec_name,
        "recruiter_email":    rec_email,
        "recruiter_phone":    rec_phone,
        "recruiter_linkedin": "",
    }


def main():
    ap = argparse.ArgumentParser(description="Generate job JSON from JD text/PDF (offline)")
    jd_grp = ap.add_mutually_exclusive_group(required=True)
    jd_grp.add_argument("--jd",       help="Job description as string")
    jd_grp.add_argument("--jd-file",  help="Path to JD text or PDF file")
    ap.add_argument("--cv",    help="Path to CV/resume PDF (optional, improves interest score)")
    ap.add_argument("--cover", help="Path to cover letter PDF (optional)")
    ap.add_argument("-o", "--output",  help="Output JSON file (default: job_filled.json)")
    ap.add_argument("--push", action="store_true", help="Push to tracker via API after generating")
    ap.add_argument("--print", dest="print_only", action="store_true", help="Print JSON to stdout only")
    args = ap.parse_args()

    # --- read JD ---
    if args.jd:
        jd_text = args.jd
    else:
        p = args.jd_file
        if p.endswith(".pdf"):
            jd_text = pdf_to_text(p)
        else:
            with open(p, encoding="utf-8") as f:
                jd_text = f.read()

    if not jd_text.strip():
        print("Error: JD text is empty.", file=sys.stderr); sys.exit(1)

    # --- read CV / cover ---
    cv_text    = pdf_to_text(args.cv)    if args.cv    else ""
    cover_text = pdf_to_text(args.cover) if args.cover else ""

    result = generate(jd_text, cv_text, cover_text, args.cv or "", args.cover or "")

    out_json = json.dumps(result, indent=2, ensure_ascii=False)

    if args.print_only:
        print(out_json)
        return

    out_path = args.output or "job_filled.json"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_json)
    print(f"Saved → {out_path}")
    print(f"  Company : {result['company'] or '(not detected)'}")
    print(f"  Role    : {result['role'] or '(not detected)'}")
    print(f"  Location: {result['location'] or '(not detected)'}")
    print(f"  Salary  : {result['salary_range'] or '(not detected)'}")
    print(f"  Tags    : {result['tags']}")
    print(f"  Interest: {result['interest_score']}/5")
    if result['recruiter_name'] or result['recruiter_email'] or result['recruiter_phone']:
        print(f"  Recruiter: {result['recruiter_name'] or '(unknown)'}"
              f" | {result['recruiter_email'] or 'no email'}"
              f" | {result['recruiter_phone'] or 'no phone'}")
    if LAST_AI_STATUS and LAST_AI_STATUS != "skipped (regex found company and role)":
        print(f"  AI gap-fill ({AI_MODEL}): {LAST_AI_STATUS}")

    if args.push:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:5050/api/job",
            data=json.dumps(result).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        print(f"\nPushed → {resp['url']}")
        if resp.get("duplicate_warning"):
            print("  [warn] Duplicate detected — same company+role already exists")


if __name__ == "__main__":
    main()
