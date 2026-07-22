# Job Tracker

Local job application tracker built with Flask + SQLite. Runs entirely on your machine — no cloud, no accounts.

## Features

### Core
- **Dashboard** — pipeline funnel, kanban board, follow-up alerts, activity feed, To Do section, weekly goal tracker
- **Application list** — search (company, role, JD), filter by status/tag/starred, sort, days-since badge
- **Job detail** — full info, timeline, documents, interview rounds, contact info
- **Stats** — offer rate, monthly volume, source breakdown, response rate by source, avg days to response, activity heatmap

### Tracking
- **9 status stages** — Applied → Screening → Phone/Technical/Final Interview → Offer / Rejected / Withdrawn / Ghosted
- **Auto timeline** — every status change logged with date and note
- **Interview rounds** — per-round notes, questions asked, outcome
- **Contact tracking** — recruiter name, email, LinkedIn per job
- **Tags** — comma-separated, filterable
- **Star / priority** — mark high-interest jobs
- **Interest score** — 1–5 star rating per job
- **Next action** — "what to do next" shown on dashboard and job list
- **Resume version** — track which resume version was sent
- **Deadlines** — follow-up date + offer deadline with dashboard alerts
- **Duplicate warning** — alerts when same company + role already exists

### Documents
- PDF/DOC upload per job
- Auto-renamed: `company_role_type.pdf`
- Multiple roles at same company stay separate
- Version collision: `_v2`, `_v3`

### Actions
- **Bulk status update** — select multiple jobs, update status at once
- **CSV export** — download all applications as spreadsheet
- **Email templates** — 7 pre-written templates (follow-up, thank you, negotiate, decline, feedback request)
- **Offer comparison** — side-by-side table of all active offers

### UX
- **Dark mode** — toggle in nav, persists via localStorage
- **Mobile responsive** — works on phone and tablet
- **Keyboard shortcut** — press `N` anywhere to open Add Job
- **Auto-save drafts** — Add Job form saves to localStorage as you type
- **Unsaved changes warning** — alerts before navigating away from unsaved form
- **Copy JD** — one-click copy job description to clipboard

## Setup

Requires Python 3.8+

```bash
pip3 install flask
```

## Run

```bash
./start.sh
```

Open [http://localhost:5050](http://localhost:5050)

## Structure

```
├── app.py              # Flask backend + SQLite (all routes)
├── start.sh            # Start script (kills port 5050 first)
├── jobs.db             # Auto-created on first run (gitignored)
├── uploads/            # Uploaded documents (gitignored)
└── templates/
    ├── base.html           # Nav, dark mode, shared CSS
    ├── dashboard.html      # Home: KPIs, kanban, alerts, activity
    ├── index.html          # All jobs list with filters
    ├── add_job.html        # Add application form
    ├── edit_job.html       # Edit form with status pill picker
    ├── job_detail.html     # Full job view with timeline + docs
    ├── stats.html          # Stats, heatmap, source breakdown
    ├── salary.html         # Offer salary comparison
    ├── offers.html         # Active offers side-by-side
    ├── email_templates.html # Pre-written email templates
    └── interviews.html     # Interview rounds page
```

## Branches

| Branch | Description |
|---|---|
| `master` | Stable base |
| `features` | All extended features |

## Document naming

| Input | Saved as |
|---|---|
| resume.pdf (Google, SWE) | `google_senior_swe_resume.pdf` |
| cover.pdf (Google, SWE) | `google_senior_swe_cover_letter.pdf` |
| Duplicate upload | `google_senior_swe_resume_v2.pdf` |
