<div align="center">

# JAT — Job Application Tracker

**Your entire job search. One local tool. Zero cloud.**

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-green.svg)](https://flask.palletsprojects.com)
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
# 1. Clone
git clone https://github.com/sananakther6642/JobApplicatonTracker.git
cd JobApplicatonTracker

# 2. Mac / Linux — run the auto-setup script
./start.sh

# 2. Windows (Command Prompt) — run the auto-setup script
start.bat

# Or manually:
python -m venv .venv
.venv\Scripts\Activate
python -m pip install -r requirements.txt
python app.py
```

That's it. `start.sh` (Mac/Linux) or `start.bat` (Windows) installs Python dependencies (Flask, pdfminer.six) automatically, and — best-effort, non-blocking — installs Ollama and pulls the small local AI model used for the optional JD-generation assist. If Ollama/Homebrew aren't available it just skips that step and the app runs fine on regex-only extraction.

### Opening the app

```bash
# Mac / Linux
open http://localhost:5050

# Windows
start http://localhost:5050
```

### Windows Usage & Auto-Setup

- **Double-click `start.bat`**: Auto-creates `.venv`, installs Python requirements, sets up Ollama & model automatically (if missing), launches the server on port 5050, and opens `http://localhost:5050` in your default browser.
- **Double-click `launch_jat.vbs`**: Runs `start.bat` silently in the background without leaving an active CMD window open.
- **Getting Updates**: Simply run `git pull` — every time you pull the latest code, `start.bat` ensures any updated dependencies are installed automatically on launch.

---

## Features

### Track everything about a job

```
Applied → Screening → Phone Interview → Technical → Final → Offer ✓
                                                          ↘ Rejected ✗
                                                          ↘ Ghosted 👻
                                                          ↘ Archived 📦
```

- **9 status stages + Archiving** with auto-logged timeline on every status change
- **Early vs. Post Rejection Breakdown** — interactive prompts classify rejections as `📭 Early Rejection` (no interview) or `❌ Post Rejection` (after interview) to keep conversion funnel metrics accurate
- **Timeline Cleansing & Event Management** — auto-deduplication of drag-and-drop ping-pong events, plus per-event deletion (`🗑`) and one-click timeline cleaning (`🧹 Clean`)
- **Priority Scoring (0–100)** — automatically computes job urgency based on stage, application age, interest, and follow-ups (`🔥 High`, `⚡ Med`, `Low`)
- **Quick Notes** — inline editable notes directly on job list cards with instant auto-save
- **Interview rounds** — notes, questions asked, outcome per round
- **Prep checklist** — per-job checklist with dynamic AJAX toggles, deletion, and progress bars
- **Salary negotiation** — initial offer → counter → final, all tracked
- **Documents & PDF Export** — upload CV, cover letter, assignments per job (auto-named) and export clean PDF snapshots for printing
- **Contact book** — recruiters (name, email, phone, LinkedIn) auto-added when you save a job
- **Automated DB Auto-Backup** — `jobs.db.bak` is created on server boot to protect data against accidental loss

### Find what you need instantly

- **Live search & 100% Dynamic UI** — filters, stars (`★`), archives (`📦`), single/bulk deletes (`🗑`), and quick status dropdowns update in real-time via AJAX without page reloads
- **Filter** by status, tag, starred, archived, source, date range
- **Sort** by date, company, salary, interest score
- **Tags** — comma-separated, filterable pills on every job
- **Click any job card** to open its full detail page — no need to hunt for a "View" button

### 🗂️ Drag-and-Drop Kanban & 📅 Interview Calendar

- **Interactive Kanban Board (`/kanban`)** — drag-and-drop job cards across stage columns with instant status updates, rejection prompts, and return-to-stack actions
- **Interview Calendar (`/calendar`)** — monthly grid and mobile list view displaying all scheduled interview dates
- **Enhanced Dark Mode** — sleek Slate dark theme (`#0b0f19`) with translucent status badges, crisp typography, and system preference auto-detection
- **Mobile-Responsive Layout** — optimized mobile layouts with responsive navigation menu

### ✨ Generate from JD

Paste a full job description into the **✨ Generate** page and all fields are filled automatically:

```
company · role · location · salary · tags · recruiter name/email/phone · interest score · job URL · source
```

- **Clipboard paste**: `📋 Paste from Clipboard` button populates the JD textarea in one click
- **Offline extraction**: Regex-based and instant for structured postings (LinkedIn, Indeed, StepStone, German boards — understands both English and German labels like `Standort`, `Gehalt`, `Ansprechpartner`)
- **Optional local AI gap-filler**: If a JD is unstructured and regex can't find a company or role, JAT asks a small local model (`qwen2.5:0.5b` via [Ollama](https://ollama.com)) to fill only the missing fields — free, fully offline, no API key required
- **CV/Cover Letter scoring**: Upload your PDF alongside the JD → interest score is calculated from keyword overlap and the files are automatically attached to the job
- **Always editable**: The JSON panel is editable before pushing — whether from regex, AI, or your own typing, there's a standing reminder to review before saving

Upload your CV/cover letter PDFs alongside → interest score calculated from keyword overlap with the JD, **and** the files themselves are attached to the job's Documents as soon as you push.

> ⚠️ **Heads up:** Auto-extracted and AI-assisted fields can occasionally be wrong — always review before pushing to the tracker.

### Dashboard that actually tells you something

- **Pipeline kanban** — active applications grouped by stage, sorted by date
- **Follow-up alerts** — jobs you haven't heard from in 7+ days with direct "✉ Draft" email template links
- **Offer deadline countdown** — upcoming deadlines highlighted within 7 days
- **Weekly & monthly goal tracker** — set a target application count, see live progress bars. Goals persist in the database and update correctly when changed
- **Next Actions list** — jobs with a pending action flagged at the top
- **Activity feed** — most recent timeline events across all jobs
- **Monthly chart** — bar chart of application volume per month (last 6 months)
- **Funnel breakdown** — live conversion stages including Early vs Post Rejections

### Stats worth looking at

| Metric | What it shows |
|--------|---------------|
| Application Velocity | Weekly application count bar chart (last 12 weeks) |
| Application Funnel | 1. Applied → Early Rejection (no interview) → 2. Interviewed → Post Rejection |
| Source Conversion Funnel | Applied → Interview → Offer breakdown per source |
| Response rate by source | Which job board actually yields responses |
| Avg days to response | How long companies actually take (calculated from first response date) |
| Interview funnel | Where you're dropping off |
| Rejection breakdown | Early vs Post rejection classification |
| Day-of-week chart | When to apply for best response |
| Salary breakdown | Salary ranges grouped by role and location |

### Email templates with smart fill

7 pre-written templates (follow-up, thank you ×3, negotiate, decline, feedback request).

Click any `[PLACEHOLDER]` → dropdown shows real data from your saved jobs:

- `[COMPANY]` → list of your saved companies
- `[ROLE]` → your actual applied roles
- `[RECRUITER_NAME]` → contacts from your contact book
- `[DATE]` → your application dates

Copy body → **automatically logs to job timeline.** No manual note needed.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `N` | Add job (full form) |
| `G` | Generate from JD |
| `D` | Dashboard |
| `J` | All jobs |
| `S` | Stats |
| `Esc` | Close dropdown |

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
| Export Summary Report | Text digest download via `📄 Summary Report` button |
| Import from CSV | Any CSV with Company + Role columns |
| Automatic Backup | `jobs.db.bak` created automatically on every boot |
| Restore database | Upload `.db` file (auto-saves `.bak` first) |

---

## Project Structure

```
jat/
├── app.py              # Flask backend — all routes, DB schema, helpers
├── gen_job.py          # Offline JD parser — regex extraction + optional local AI gap-filler
├── start.sh            # Mac/Linux — auto-installs deps (incl. Ollama), starts server
├── start.bat           # Windows — auto-installs deps, starts server
├── requirements.txt    # pip dependencies (flask, pdfminer.six)
├── pytest.ini          # Test configuration
├── LICENSE
│
├── jobs.db             # Your data — gitignored
├── uploads/            # Your documents — gitignored
│
├── docs/
│   └── index.html      # GitHub Pages landing page
│
├── tests/
│   ├── conftest.py           # pytest fixtures (in-memory test DB)
│   ├── test_helpers.py       # Unit tests — slugify, save_tags, get_tags_for_jobs
│   └── test_routes.py        # Integration tests — all major routes
│
└── templates/
    ├── base.html             # Shared nav, dark mode, CSS variables
    ├── dashboard.html        # KPIs, pipeline, goals, alerts, activity feed
    ├── index.html            # Job list — search, filter, sort, bulk actions
    ├── add_job.html          # Add form
    ├── edit_job.html         # Edit form with status pill picker
    ├── job_detail.html       # Full view — timeline, docs, checklist
    ├── generate.html         # ✨ JD → auto-fill → push UI
    ├── stats.html            # Charts, funnel, rejection breakdown, source stats
    ├── email_templates.html  # Smart fill templates + timeline logging
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

## Testing

```bash
# Install test dependency (one-time)
pip3 install pytest-flask

# Run all tests
python3 -m pytest

# Verbose
python3 -m pytest -v

# Specific file
python3 -m pytest tests/test_helpers.py
```

Tests use an isolated in-memory SQLite DB — your `jobs.db` is never touched.

---

## Contributing

Open source under the MIT License. Contributions welcome — bug fixes, new features, UI improvements, anything.

**Found a bug?** Open a [GitHub issue](https://github.com/sananakther6642/JobApplicatonTracker/issues).

**Want to contribute code?** Fork → branch → PR:

```bash
# Fork on GitHub, then:
git clone https://github.com/<your-username>/JobApplicatonTracker.git
cd jat
git checkout -b fix/describe-your-fix

# Make changes, test locally
# Mac / Linux
./start.sh
# Windows
start.bat

# Push and open a PR against the features branch
git push origin fix/describe-your-fix
```

**PR guidelines:**
- Target `features` branch, not `master`
- One fix or feature per PR — keep it focused
- Test with `./start.sh` (Mac/Linux) or `start.bat` (Windows) before submitting
- If you only found a bug but can't fix it — open an issue, that's helpful too

---

## License

[MIT](LICENSE) — free to use, fork, modify, and redistribute.

---

<div align="center">

Built with Flask + SQLite · Runs at `localhost:5050` · Your data stays yours

</div>
