<div align="center">

# JAT — Job Application Tracker

**Your entire job search. One local tool. Zero cloud.**

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-2.x-green.svg)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/database-SQLite-lightgrey.svg)](https://sqlite.org)

*Track applications · Log timelines · Compare offers · Generate from JD · No accounts, no sync, no BS*

</div>

---

## What is JAT?

JAT is a local-first job application tracker. Run it on your machine, open it in your browser, and manage your entire job search — from first application to signed offer — without touching a cloud service.

- **No login.** No subscription. No data sent anywhere.
- **Everything local.** SQLite database, uploaded files, all on your disk.
- **Survives restarts.** Your data is in `jobs.db` — it's just a file.

---

## Quick Start

```bash
# 1. Install the only dependency
pip3 install flask

# 2. Run
./start.sh

# 3. Open
open http://localhost:5050
```

That's it.

---

## Features

### Track everything about a job

```
Applied → Screening → Phone Interview → Technical → Final → Offer ✓
                                                          ↘ Rejected ✗
                                                          ↘ Ghosted 👻
```

- **9 status stages** with auto-logged timeline on every change
- **Interview rounds** — notes, questions asked, outcome per round
- **Prep checklist** — per-job checklist with progress bar
- **Salary negotiation** — initial offer → counter → final, all tracked
- **Documents** — upload CV, cover letter, assignments per job (auto-named)
- **Contact book** — recruiters auto-added when you save a job

### Find what you need instantly

- **Search** across company, role, JD, notes — with live result highlighting
- **Filter** by status, tag, starred, source, date range
- **Sort** by date, company, salary, interest score
- **Tags** — comma-separated, filterable pills on every job

### Dashboard that actually tells you something

- Pipeline funnel + kanban board
- Follow-up alerts — jobs you haven't heard from in 7+ days
- Offer deadline countdown
- Weekly + monthly application goal tracker
- Activity feed + 52-week heatmap

### Stats worth looking at

| Metric | What it shows |
|--------|---------------|
| Offer rate | How many applications → offers |
| Response rate by source | Which job board actually works |
| Avg days to response | How long companies actually take |
| Interview funnel | Where you're dropping off |
| Rejection breakdown | Why you're being rejected |
| Day-of-week chart | When to apply for best response |

### ✨ Generate from JD (offline, no AI API needed)

Paste a job description → all fields auto-filled:

```
company, role, location, salary, tags, recruiter name/email, interest score, job URL, source
```

Upload your CV PDF alongside → interest score calculated from keyword overlap.
One click → pushed to tracker. Press `G` to open.

### Email templates with smart fill

7 pre-written templates (follow-up, thank you ×3, negotiate, decline, feedback request).

Click any `[PLACEHOLDER]` → dropdown shows real data from your saved jobs:

- `[COMPANY]` → list of your saved companies
- `[ROLE]` → your actual applied roles
- `[RECRUITER_NAME]` → contacts from your contact book
- `[DATE]` → your application dates

Copy body → **automatically logs to job timeline.** No manual note needed.

### JSON API — log jobs without opening the browser

```bash
# Fill the template
cp job_template.json ford.json
vim ford.json

# Push
./push_job.sh ford.json
# → {"id": 12, "url": "http://localhost:5050/job/12", "duplicate_warning": false}
```

Or generate + push in one step:

```bash
python3 gen_job.py --jd-file jd.txt --cv CV.pdf --push
```

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `N` | Add job (full form) |
| `Q` | Quick add (2-field modal) |
| `G` | Generate from JD |
| `D` | Dashboard |
| `J` | All jobs |
| `S` | Stats |
| `Esc` | Close modal / dropdown |

---

## Document Naming

Files are renamed automatically on upload:

| You upload | Saved as |
|------------|----------|
| `resume.pdf` for Google SWE | `google_senior_swe_resume.pdf` |
| `cover.pdf` for Google SWE | `google_senior_swe_cover_letter.pdf` |
| Same file again | `google_senior_swe_resume_v2.pdf` |

---

## Import / Export / Backup

| Action | How |
|--------|-----|
| Export all jobs | CSV → open in Excel/Sheets |
| Import from CSV | Any CSV with Company + Role columns |
| Backup database | Download `jobs.db` |
| Restore database | Upload `.db` file (auto-saves `.bak` first) |

---

## Project Structure

```
jat/
├── app.py              # Flask backend — all routes, DB schema, helpers
├── gen_job.py          # Offline JD parser — extracts fields from text/PDF
├── start.sh            # ./start.sh — kills port 5050, starts server
├── push_job.sh         # ./push_job.sh job.json — push via API
├── job_template.json   # Blank template to fill per application
├── api.txt             # Full API reference + curl examples
├── LICENSE
│
├── jobs.db             # Your data — gitignored
├── uploads/            # Your documents — gitignored
│
└── templates/
    ├── base.html             # Shared nav, dark mode, quick-add modal, CSS
    ├── dashboard.html        # KPIs, kanban, goals, alerts, activity feed
    ├── index.html            # Job list — search, filter, sort, bulk actions
    ├── add_job.html          # Add form with autosave drafts
    ├── edit_job.html         # Edit form with status pill picker
    ├── job_detail.html       # Full view — timeline chart, docs, checklist
    ├── stats.html            # Charts, heatmap, funnel, rejection breakdown
    ├── email_templates.html  # Smart fill templates + timeline logging
    ├── generate.html         # JD → JSON → push UI
    ├── contacts.html         # Recruiter/contact book
    ├── checklist.html        # Per-job interview prep checklist
    ├── offers.html           # Side-by-side offer comparison
    ├── salary.html           # Salary data grouped by status
    ├── import_csv.html       # CSV import
    ├── restore_db.html       # Backup & restore
    └── print_job.html        # Clean printable summary
```

---

## Data & Privacy

- All data stored in `jobs.db` on your local disk
- `jobs.db` and `uploads/` are gitignored — never committed
- Nothing is sent to any server, ever
- Delete `jobs.db` to wipe everything. That's the whole data model.

---

## Contributing

Open source under the MIT License. Contributions welcome — bug fixes, new features, UI improvements, anything.

**Found a bug?** Open a [GitHub issue](https://github.com/sananakther6642/jat/issues).

**Want to contribute code?** Fork → branch → PR:

```bash
# Fork on GitHub, then:
git clone https://github.com/<your-username>/jat.git
cd jat
git checkout -b fix/describe-your-fix

# Make changes, test locally
./start.sh

# Push and open a PR against the features branch
git push origin fix/describe-your-fix
```

**PR guidelines:**
- Target `features` branch, not `master`
- One fix or feature per PR — keep it focused
- Test with `./start.sh` before submitting
- If you only found a bug but can't fix it — open an issue, that's helpful too

---

## License

[MIT](LICENSE) — free to use, fork, modify, and redistribute.

---

<div align="center">

Built with Flask + SQLite · Runs at `localhost:5050` · Your data stays yours

</div>
