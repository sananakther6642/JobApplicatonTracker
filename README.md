# Job Tracker

Local job application tracker built with Flask + SQLite. Runs entirely on your machine — no cloud, no accounts.

## Features

### Core
- **Dashboard** — pipeline funnel, kanban board, follow-up alerts, deadline alerts, activity feed, To Do section, weekly + monthly goal tracker
- **Application list** — search (company, role, JD, notes) with result highlighting, filter by status/tag/starred, sort, days-since badge
- **Job detail** — full info, status timeline chart, interview rounds, documents, contact info, salary negotiation tracker, prep checklist
- **Stats** — offer rate, interview funnel, monthly volume, source breakdown, response rate by source, avg days to response, day-of-week chart, rejection breakdown, activity heatmap (52-week)
- **Quick Add** — 2-field modal (press `Q`) to log a job instantly, fill details later

### Tracking
- **9 status stages** — Applied → Screening → Phone/Technical/Final Interview → Offer / Rejected / Withdrawn / Ghosted
- **Inline status edit** — click any status badge in the list to change it without navigating away
- **Auto timeline** — every status change logged with date and note
- **Interview rounds** — per-round notes, questions asked, outcome
- **Interview prep checklist** — per-job checklist with default items, progress bar
- **Salary negotiation tracker** — initial offer, counter, final amount per job
- **Contact book** — shared recruiter/contact directory (searchable, reusable across jobs)
- **Tags** — comma-separated, filterable pills
- **Star / priority** — mark high-interest jobs
- **Interest score** — 1–5 star rating per job
- **Next action** — shown on dashboard and job list
- **Resume version** — track which resume version was sent
- **Rejection reason** — dropdown (No response, Skills gap, Salary mismatch, etc.) + stats breakdown
- **Deadlines** — follow-up date + offer deadline with dashboard alerts
- **Duplicate warning** — alerts when same company + role already exists
- **Clone job** — duplicate an application to re-apply to same role

### Documents
- PDF/DOC upload per job
- Auto-renamed: `company_role_type.pdf`
- Multiple roles at same company stay separate
- Version collision: `_v2`, `_v3`

### Import / Export / Backup
- **CSV export** — download all applications as spreadsheet
- **CSV import** — re-import exported CSV or any CSV with Company + Role columns
- **DB backup** — download full `jobs.db` file
- **DB restore** — upload a backup to replace current database (auto-saves `.bak` first)

### Actions
- **Bulk status update** — select multiple jobs, update at once
- **Bulk delete** — select and delete with 5s undo toast
- **Undo delete** — 5-second window to undo single-job deletes
- **Print view** — clean printable summary per job
- **Email templates** — 7 pre-written (follow-up, thank you, negotiate, decline, feedback request)
- **Offer comparison** — side-by-side table of active offers, highest salary highlighted
- **Salary comparison** — all jobs with salary data, grouped by status

### UX
- **Dark mode** — toggle in nav, persists via localStorage
- **Mobile responsive** — works on phone and tablet
- **Keyboard shortcuts** — `N` Add Job, `Q` Quick Add, `D` Dashboard, `J` All Jobs, `S` Stats, `Esc` close dropdowns
- **Auto-save drafts** — Add Job form saves to localStorage as you type
- **Search history** — last 5 searches shown as datalist suggestions
- **Unsaved changes warning** — alerts before navigating away from unsaved form
- **Copy JD** — one-click copy job description to clipboard
- **Browser notifications** — permission requested on load for follow-up reminders
- **Toast notifications** — star, delete, status changes all confirm via toast

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
├── app.py              # Flask backend + all routes
├── start.sh            # Start script (kills port 5050 first)
├── jobs.db             # Auto-created on first run (gitignored)
├── uploads/            # Uploaded documents (gitignored)
└── templates/
    ├── base.html             # Nav, dark mode, shared CSS, quick-add modal
    ├── dashboard.html        # KPIs, kanban, alerts, goals, activity
    ├── index.html            # All jobs list with filters + bulk actions
    ├── add_job.html          # Add application form with draft autosave
    ├── edit_job.html         # Edit form with status pill picker
    ├── job_detail.html       # Full job view: timeline, docs, checklist
    ├── stats.html            # Stats, charts, heatmap, rejection breakdown
    ├── salary.html           # Salary comparison by status
    ├── offers.html           # Active offers side-by-side
    ├── email_templates.html  # Pre-written email templates
    ├── interviews.html       # Interview rounds standalone page
    ├── checklist.html        # Interview prep checklist
    ├── contacts.html         # Contact book
    ├── edit_contact.html     # Edit contact
    ├── import_csv.html       # CSV import page
    ├── restore_db.html       # Backup & restore page
    └── print_job.html        # Printable job summary
```

## Data persistence

All data stored in `jobs.db` on disk. Survives server restarts and reboots. Only lost if the file or folder is deleted.

`jobs.db` and `uploads/` are gitignored — your job data never goes to GitHub.

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

## Keyboard shortcuts

| Key | Action |
|---|---|
| `N` | Add Job (full form) |
| `Q` | Quick Add (modal) |
| `D` | Dashboard |
| `J` | All Jobs |
| `S` | Stats |
| `Esc` | Close dropdowns / modal |
