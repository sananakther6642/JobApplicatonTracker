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

That's it. `start.sh` (Mac/Linux) or `start.bat` (Windows) installs Python dependencies (Flask, pdfminer.six)
automatically, and — best-effort, non-blocking — installs Ollama and pulls
the small local AI model used for the optional JD-generation assist. If
Ollama/Homebrew aren't available it just skips that step and the app runs
fine on regex-only extraction.

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
```

- **9 status stages** with auto-logged timeline on every change
- **Interview rounds** — notes, questions asked, outcome per round
- **Prep checklist** — per-job checklist with progress bar
- **Salary negotiation** — initial offer → counter → final, all tracked
- **Documents** — upload CV, cover letter, assignments per job (auto-named)
- **Contact book** — recruiters (name, email, phone, LinkedIn) auto-added when you save a job
- **Automated DB Auto-Backup** — `jobs.db.bak` is created on server boot to protect data against accidental loss

### Find what you need instantly

- **Live search** across company, role, JD, notes — filters as you type (debounced AJAX, no page reload) with result highlighting
- **Filter** by status, tag, starred, source, date range
- **Sort** by date, company, salary, interest score
- **Tags** — comma-separated, filterable pills on every job
- **Click any job card** to open its full detail page — no need to hunt for a "View" button

### ✨ Generate from JD & 1-Click Capture

- **Clipboard One-Click Paste**: `📋 Paste from Clipboard` button populates raw JDs in under a second
- **Browser Bookmarklet**: Drag the `🔖 Track with JAT` bookmarklet to your browser toolbar to capture highlighted JDs from LinkedIn, Indeed, or StepStone in 1 click
- **Offline Extraction**: Extracts company, role, location, salary, tags, recruiter info, and interest score automatically

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

### ✨ Generate from JD (offline, no paid AI API needed)

Paste a job description → all fields auto-filled:

```
company, role, location, salary, tags, recruiter name/email/phone, interest score, job URL, source
```

Extraction is regex-based and instant for structured postings (LinkedIn, Indeed,
StepStone, German job boards, etc.), and understands both English and German
labels (`Standort`, `Gehalt`, `Ansprechpartner`, `Telefon`, ...).

Upload your CV/cover letter PDFs alongside → interest score calculated from
keyword overlap with the JD, **and** the files themselves are attached to the
job's Documents as soon as you push — no need to re-upload them on the job
detail page afterward.

**Optional local AI assist** — if a JD is unusually unstructured (plain prose,
no clear headings) and the regex extraction can't find a company or role, JAT
will ask a small local model (`qwen2.5:0.5b` via [Ollama](https://ollama.com))
to fill in *only* the missing fields. This is:

- **Free and fully offline** — no API key, no cloud calls, runs entirely on your machine
- **A gap-filler, never a primary source** — the fast regex result is always trusted first; the AI is only consulted when it's genuinely needed, so well-formed JDs never pay any AI latency
- **Designed so it can't fabricate content** — the model is never asked to *write* a field's value (a small model asked to do that will confidently invent one, e.g. a phone number that isn't in the JD at all — this was tested extensively). Instead it's only asked to point at *which existing line* of the JD contains each field, and the actual value is always the literal text of that line, extracted by code — not generated. On top of that, each field is sanity-checked against what it should look like (a salary line must contain a currency/number, a phone must be mostly digits, an email must look like an email, a name can't be a company) before being used.
- **A warning banner** tells you whenever AI-filled fields are present, and the JSON panel always carries a reminder to review before pushing (see disclaimer below) — the worst realistic failure mode left is the AI pointing at the wrong *real* line, never inventing one that doesn't exist.
- **Auto-installed** — on Mac/Linux, `./start.sh` installs Ollama (via Homebrew or the official install script) and pulls the model automatically on first run if they're not already present; nothing to set up by hand. On Windows, `start.bat` installs Python deps and attempts to install Ollama (via winget or the official installer) automatically; if that fails, install Ollama manually from https://ollama.com and run `ollama pull qwen2.5:0.5b`.

The JSON panel is always editable — you don't have to generate from a JD at
all; paste or write JSON directly and push it. Whether it came from regex, AI,
or your own typing, there's a standing disclaimer above the Push button:
**auto-extracted and AI-assisted fields can occasionally be wrong or
hallucinated — always review before pushing.**

One click → pushed to tracker. Press `G` to open.

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
    ├── base.html             # Shared nav, dark mode, CSS
    ├── dashboard.html        # KPIs, pipeline, goals, alerts, activity feed
    ├── index.html            # Job list — search, filter, sort, bulk actions
    ├── add_job.html          # Add form
    ├── edit_job.html         # Edit form with status pill picker
    ├── job_detail.html       # Full view — timeline, docs, checklist
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

## Testing

50 pytest tests covering routes, helpers, and DB logic.

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
