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
import sys
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
        r"(?i:^\s*(?:company|employer)\s*:\s*)([^\n,|]{2,60})",                 # explicit "Company:" label
        r"^\s*([A-Z][A-Za-z0-9&.,'\-]{1,55}\s(?:GmbH|AG|Inc\.?|LLC|Ltd\.?|Corp\.?|SE|BV|NV|SAS|AB|PLC))\.?\s*$",  # standalone legal-entity line
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
    # Explicit label, e.g. "Position: Test Engineer"
    m = re.search(r"(?i:^\s*(?:position|role|job\s*title|title)\s*:\s*)([^\n|]{3,90})", text, re.MULTILINE)
    if m:
        return m.group(1).strip()

    # "We are hiring/seeking/looking for a <Role>"
    m = re.search(r"(?i:\b(?:hiring|seeking|looking for)\b(?:\s+a)?\s+)([A-Z][^\n,.]{3,79})", text)
    if m:
        return m.group(1).strip()

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
    loc = ""
    m = re.search(r"(?i:location\s*:\s*)([^\n,|]{3,50})", text)
    if m:
        loc = m.group(1).strip()
    if not loc:
        m = re.search(r"(?i:\b(?:based in|located in|office in)\b\s*:?\s*)([^\n,|.]{3,50})", text)
        if m:
            loc = m.group(1).strip()
    if not loc:
        # "City, ST" (e.g. "Sunrise, FL") or "City, Country" (e.g. "Stuttgart, Germany")
        # — tried before the "(hybrid)" pattern below so the full city+region is kept
        # rather than only whichever half happens to sit next to the parenthesis.
        m = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*(?:" + _US_STATES + r"\b|" + _COUNTRIES + r"))", text)
        if m:
            loc = m.group(1).strip()
    if not loc:
        m = re.search(r"^([A-Z][a-z]+(?:[A-Za-z.\-]*))\s*\((?:hybrid|remote|onsite|on-site)\)", text, re.MULTILINE)
        if m:
            loc = m.group(1).strip()
    if not loc:
        # "Langenbrettach – permanent" / "City - onsite" style header line
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
    m = re.search(r"(?i:(?:salary|compensation)\s*:?\s*\n)((?:[^\n]*\n?){1,6})", text)
    section = m.group(1) if m else ""
    amounts = re.findall(r"([€$])\s?([\d.,]{4,10})", section)
    if len(amounts) >= 2:
        currency = amounts[0][0]
        nums = [float(a[1].replace(".", "").replace(",", "")) for a in amounts]
        lo, hi = f"{int(min(nums)):,}", f"{int(max(nums)):,}"
        suffix = "/year" if re.search(r"(?i:/\s*year|per\s*year|annual)", section) else ""
        return f"{currency}{lo} - {currency}{hi}{suffix}"

    return _first([
        r"salary[:\s]+([^\n|]{3,60})",
        r"compensation[:\s]+([^\n|]{3,60})",
    ], text)


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
    """Find the recruiter's contact number, if the JD mentions one."""
    # 1. Explicitly labeled, e.g. "Telephone: +49 7946 9194181" / "Phone: ..."
    m = re.search(
        r"(?i:\b(?:telephone|phone|tel|mobile|contact\s*no\.?|call)\b\s*(?:number)?\s*[:.]?\s*)"
        r"(" + _PHONE_CORE + r")",
        text,
    )
    if m:
        return _clean_phone(m.group(1))

    # 2. Same line as a "Contact:" header, e.g. "Contact: Name | email | 0731 96538563"
    m = re.search(r"(?i:^[ \t]*contact\s*(?:person)?\s*:.*)$", text, re.MULTILINE)
    if m:
        line_m = re.search(_PHONE_CORE, m.group(0))
        if line_m:
            return _clean_phone(line_m.group(0))

    # 3. A standalone number with an explicit country code, e.g. "+49 7946 9194181"
    m = re.search(r"(\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4})", text)
    if m:
        return _clean_phone(m.group(1))

    return ""


def extract_recruiter(text: str) -> tuple:
    # Case-sensitive on purpose — see note in extract_company(). Using IGNORECASE
    # here previously let "Ms" match the "ms" inside unrelated words like "systems".
    # [ \t]+ (not \s+) between name parts so the match can't cross a newline
    # and swallow the next line (e.g. "Sarah Simonis\nTelephone: ...").
    name = ""
    m = re.search(
        r"(?i:contact(?:\s+person)?)[ \t]*:[ \t]*([A-Z][a-zA-Z.'\-]+(?:[ \t]+[A-Z][a-zA-Z.'\-]+){1,2})",
        text,
    )
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r"\b(?:Mrs?\.?|Ms\.?|Dr\.?)[ \t]+([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})", text)
        if m:
            name = m.group(1).strip()
    email = _first([r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})"], text)
    phone = extract_phone(text)
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


def extract_interest(jd_text: str, cv_text: str, cover_text: str = "") -> str:
    """Rate 1-5 by keyword overlap between JD requirements and CV/cover letter."""
    applicant_text = f"{cv_text} {cover_text}".strip()
    if not applicant_text:
        return "3"
    jd_words = set(re.findall(r'\b[a-z]{3,}\b', jd_text.lower()))
    cv_words  = set(re.findall(r'\b[a-z]{3,}\b', applicant_text.lower()))
    overlap = len(jd_words & cv_words)
    if overlap > 120: return "5"
    if overlap > 80:  return "4"
    if overlap > 50:  return "3"
    if overlap > 25:  return "2"
    return "1"


def extract_resume_version(cv_path: str) -> str:
    if not cv_path:
        return ""
    base = os.path.basename(cv_path)
    return os.path.splitext(base)[0]


# ── Main ──────────────────────────────────────────────────────────────────────

def generate(jd_text: str, cv_text: str = "", cover_text: str = "",
             cv_path: str = "", cover_path: str = "") -> dict:
    text = jd_text  # primary source for most fields

    company  = extract_company(text)
    role     = extract_role(text)
    location = extract_location(text)
    salary   = extract_salary(text)
    job_url  = extract_job_url(text)
    source   = extract_source(text, job_url)
    tags     = extract_tags(text)
    interest = extract_interest(text, cv_text, cover_text)
    rec_name, rec_email, rec_phone = extract_recruiter(text)
    if not rec_name and not rec_email and not rec_phone:
        # fall back to the cover letter (e.g. "Dear Mr. Smith", signed contact)
        rec_name, rec_email, rec_phone = extract_recruiter(cover_text)
    resume_v = extract_resume_version(cv_path)

    return {
        "company":            company,
        "role":               role,
        "status":             "applied",
        "applied_date":       date.today().isoformat(),
        "source":             source,
        "location":           location,
        "salary_range":       salary,
        "jd":                 jd_text.strip(),
        "job_url":            job_url,
        "tags":               tags,
        "interest_score":     interest,
        "notes":              "",
        "next_action":        "Follow up in 1 week",
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
