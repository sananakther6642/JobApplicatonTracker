# Job Tracker

Local job application tracker built with Flask + SQLite. Runs entirely on your machine — no cloud, no accounts.

## Features

- **Dashboard** — pipeline funnel, kanban board, follow-up alerts, activity feed
- **Application list** — search, filter by status, sort
- **Job detail** — full info, timeline of events, documents
- **Status tracking** — 9 stages: Applied → Screening → Phone/Technical/Final Interview → Offer / Rejected / Withdrawn / Ghosted
- **Auto timeline** — every status change logged with date and note
- **Document uploads** — PDFs auto-renamed as `company_role_type.pdf`, versioned on collision
- **Stats** — offer rate, monthly volume, source breakdown

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
├── app.py              # Flask backend + SQLite
├── start.sh            # Start script (kills port 5050 first)
├── jobs.db             # Auto-created on first run (gitignored)
├── uploads/            # Uploaded documents (gitignored)
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── index.html
    ├── add_job.html
    ├── edit_job.html
    ├── job_detail.html
    └── stats.html
```

## Document naming

Uploaded files are renamed automatically:

| Input | Saved as |
|---|---|
| `my_resume.pdf` (Google, SWE, Resume) | `google_senior_software_engineer_resume.pdf` |
| `cover.pdf` (Google, SWE, Cover Letter) | `google_senior_software_engineer_cover_letter.pdf` |
| Same file uploaded again | `google_senior_software_engineer_resume_v2.pdf` |
