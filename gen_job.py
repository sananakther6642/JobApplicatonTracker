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
    return _first([
        r"^(.+?)\s*\|",
        r"company[:\s]+([^\n,|]+)",
        r"at\s+([A-Z][A-Za-z0-9 &.,'-]{2,40})\b",
        r"^([A-Z][A-Za-z0-9 &.,'-]{2,40}(?:GmbH|AG|Inc|LLC|Ltd|Corp|SE|BV|NV|SAS|AB)\b)",
    ], text, re.IGNORECASE | re.MULTILINE)


def extract_role(text: str) -> str:
    return _first([
        r"(?:position|role|job title|title)[:\s]+([^\n|]{5,80})",
        r"^(.{5,80}?)\s*[\|—–-]\s*(?:job|position|role|engineer|developer|analyst|manager|lead)",
        r"(?:hiring|seeking|looking for)[:\s]+(?:a\s+)?([^\n,|.]{5,80})",
        r"^([A-Z][^\n]{5,70}(?:Engineer|Developer|Analyst|Manager|Architect|Lead|Consultant|Specialist|Designer))\b",
    ], text, re.IGNORECASE | re.MULTILINE)


def extract_location(text: str) -> str:
    loc = _first([
        r"location[:\s]+([^\n,|]{3,50})",
        r"(?:based in|located in|office in)[:\s]+([^\n,|.]{3,50})",
        r"([A-Z][a-z]+(?:,\s*[A-Z]{2,3})?)\s*\((?:hybrid|remote|onsite|on-site)\)",
        r"\b([A-Z][a-z]+(?:,\s*[A-Z][a-z]+)?),?\s*(Germany|USA|United States|UK|France|Netherlands|Switzerland|Austria|India)\b",
    ], text)
    # pick up hybrid/remote/onsite modifier
    mode = _first([r"\b(hybrid|remote|on-?site|onsite)\b"], text)
    if loc and mode:
        if mode.lower() not in loc.lower():
            return f"{loc} ({mode.title()})"
    return loc


def extract_salary(text: str) -> str:
    return _first([
        r"(\$[\d,]+\s*(?:–|-|to)\s*\$[\d,]+(?:\s*(?:k|K|USD|EUR|€)?)?(?:\s*/\s*(?:year|yr|annual))?)",
        r"(€[\d,.]+\s*(?:–|-|to)\s*€[\d,.]+(?:\s*(?:k|K))?(?:\s*/\s*(?:year|yr|annual))?)",
        r"([\d,]+\s*(?:–|-|to)\s*[\d,]+\s*(?:EUR|USD|\$|€)(?:\s*/\s*(?:year|yr|annual))?)",
        r"salary[:\s]+([^\n,|]{3,50})",
        r"compensation[:\s]+([^\n,|]{3,50})",
        r"(?:gross|brutto)[^\n]{0,20}([\d,.]+\s*(?:EUR|USD|€|\$)[^\n]{0,30})",
    ], text)


def extract_source(text: str) -> str:
    for platform in ["LinkedIn", "Indeed", "Glassdoor", "XING", "StepStone",
                     "Monster", "Seek", "Naukri", "Greenhouse", "Lever", "Workday"]:
        if platform.lower() in text.lower():
            return platform
    return ""


def extract_job_url(text: str) -> str:
    return _first([
        r"(https?://[^\s\"'<>]+(?:job|career|position|vacancy|posting|apply)[^\s\"'<>]*)",
        r"(https?://(?:www\.)?(?:linkedin|indeed|glassdoor|greenhouse|lever|workday)[^\s\"'<>]*)",
        r"apply[^\n]{0,30}(https?://[^\s\"'<>]+)",
    ], text)


def extract_recruiter(text: str) -> tuple:
    name  = _first([r"contact[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)", r"(?:Mrs?|Ms|Dr)\.?\s+([A-Z][a-z]+ [A-Z][a-z]+)"], text)
    email = _first([r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,})"], text)
    return name, email


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


def extract_interest(jd_text: str, cv_text: str) -> str:
    """Rate 1-5 by keyword overlap between JD requirements and CV."""
    if not cv_text:
        return "3"
    jd_words = set(re.findall(r'\b[a-z]{3,}\b', jd_text.lower()))
    cv_words  = set(re.findall(r'\b[a-z]{3,}\b', cv_text.lower()))
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
    source   = extract_source(text)
    job_url  = extract_job_url(text)
    tags     = extract_tags(text)
    interest = extract_interest(text, cv_text)
    rec_name, rec_email = extract_recruiter(text)
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
