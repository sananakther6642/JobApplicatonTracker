import sqlite3
import os
import re
import csv
import io
import time
import uuid
import shutil
from flask import (Flask, render_template, request, redirect, url_for,
                   send_from_directory, abort, flash, make_response, jsonify)
from werkzeug.utils import secure_filename
from datetime import date, timedelta, datetime

import sys
import webbrowser
import threading

if getattr(sys, 'frozen', False):
    BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BASE_DIR

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
DB = os.path.join(APP_DIR, "jobs.db")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.after_request
def no_cache_html(response):
    """Prevent browser back/forward cache for HTML pages.
    Without this, pressing Back after a Kanban status change shows
    the old page from browser cache instead of fresh server data."""
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store"
    return response


ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}

# The Generate page lets you attach a CV/cover letter before a job even
# exists yet (to compute the interest score). Those files are held here under
# a pending name until the job is actually pushed, at which point they're
# claimed and renamed into normal documents — see _save_pending_upload_raw() /
# _rename_pending_upload() / _claim_pending_upload(). If the user never
# pushes, they're orphaned; sweep anything older than a day on startup so
# they don't accumulate forever.
PENDING_PREFIX = "_pending_"


def _cleanup_orphaned_pending_uploads(max_age_hours: int = 24):
    cutoff = time.time() - max_age_hours * 3600
    try:
        for name in os.listdir(UPLOAD_DIR):
            if name.startswith(PENDING_PREFIX):
                path = os.path.join(UPLOAD_DIR, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
    except OSError:
        pass


_cleanup_orphaned_pending_uploads()


def _save_pending_upload_raw(f, doc_type):
    """Save an uploaded file exactly ONCE, under a generic pending name (we
    don't know company/role yet at this point). Returns the pending
    filename, or None if the file is missing/has a disallowed extension.

    IMPORTANT: this is the only place that should ever call f.save() on a
    given upload. request.files.get(field) returns the same FileStorage
    object every time it's called within a request, and its underlying
    stream can only be read once — a second .save() call on the same
    FileStorage silently writes a 0-byte (corrupted) file instead of
    raising an error. Once saved here, use _rename_pending_upload() to
    give it a nicer name — that's a plain filesystem rename, not a re-read
    of the upload, so it can't corrupt anything."""
    if not f or not f.filename or "." not in f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None
    candidate = f"{PENDING_PREFIX}{doc_type}_{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(UPLOAD_DIR, candidate))
    return candidate


def _rename_pending_upload(pending_name, company, role, doc_type):
    """Rename an already-saved pending upload (from _save_pending_upload_raw)
    to a human-readable name once company/role are known. Best-effort: falls
    back to the original name unchanged if anything looks wrong, since a
    naming hiccup should never lose the underlying file."""
    if not pending_name or not pending_name.startswith(PENDING_PREFIX):
        return pending_name
    src = os.path.join(UPLOAD_DIR, pending_name)
    if not os.path.isfile(src):
        return pending_name
    ext = pending_name.rsplit(".", 1)[-1].lower()
    base = _doc_base_name(company, role, doc_type)

    def _candidate(suffix=""):
        return f"{PENDING_PREFIX}{base}{suffix}.{ext}"

    candidate = _candidate()
    path = os.path.join(UPLOAD_DIR, candidate)
    if os.path.exists(path) and path != src:
        for v in range(2, 100):
            candidate = _candidate(f"_v{v}")
            path = os.path.join(UPLOAD_DIR, candidate)
            if not os.path.exists(path) or path == src:
                break
    if path != src:
        os.replace(src, path)
    return candidate


def _claim_pending_upload(conn, pending_name, original_name, doc_type, job_id, company, role):
    """Turn a pending upload (saved by _save_pending_upload) into a real
    document attached to job_id. Silently no-ops on anything suspicious
    (missing file, wrong prefix, path traversal) rather than raising, since
    this is best-effort — a job push should never fail because of this."""
    if not pending_name or not pending_name.startswith(PENDING_PREFIX):
        return
    # Validate defensively since pending_name comes from a client-submitted
    # hidden form field: reject anything with a path separator (blocks
    # traversal like "../../etc/passwd") or characters outside a safe
    # allowlist. NOTE: this intentionally does NOT use werkzeug's
    # secure_filename() for equality-checking — it strips leading/trailing
    # "." and "_", which are perfectly safe here and which our own naming
    # legitimately produces (e.g. a leading "_" when company/role couldn't
    # be extracted) — using it as an equality check previously caused this
    # function to silently reject and never claim otherwise-valid files.
    if pending_name != os.path.basename(pending_name):
        return
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", pending_name):
        return
    src = os.path.join(UPLOAD_DIR, pending_name)
    if not os.path.isfile(src):
        return
    ext = pending_name.rsplit(".", 1)[-1].lower()
    final_name = make_doc_filename(company, role, doc_type, ext, job_id)
    os.replace(src, os.path.join(UPLOAD_DIR, final_name))
    conn.execute(
        "INSERT INTO documents (job_id, filename, original_name, doc_type) VALUES (?,?,?,?)",
        (job_id, final_name, original_name or final_name, doc_type),
    )


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    if os.path.isfile(DB) and os.path.getsize(DB) > 0:
        try:
            shutil.copy2(DB, f"{DB}.bak")
        except OSError:
            pass
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                jd TEXT,
                job_url TEXT,
                applied_date TEXT,
                status TEXT DEFAULT 'applied',
                source TEXT,
                salary_range TEXT,
                location TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                event_date TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT,
                doc_type TEXT DEFAULT 'resume',
                uploaded_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS interview_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                round_name TEXT NOT NULL,
                interview_date TEXT,
                notes TEXT,
                questions_asked TEXT,
                outcome TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT DEFAULT '',
                linkedin TEXT DEFAULT '',
                company TEXT DEFAULT '',
                title TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS interview_checklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                round_id INTEGER,
                item TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS salary_negotiations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                initial_offer TEXT,
                counter_offer TEXT,
                final_amount TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
        """)

        # Add new columns to existing jobs table (SQLite has no IF NOT EXISTS for ALTER)
        new_cols = [
            ("recruiter_name",     "TEXT DEFAULT ''"),
            ("recruiter_email",    "TEXT DEFAULT ''"),
            ("recruiter_linkedin", "TEXT DEFAULT ''"),
            ("recruiter_phone",    "TEXT DEFAULT ''"),
            ("follow_up_date",     "TEXT DEFAULT ''"),
            ("offer_deadline",     "TEXT DEFAULT ''"),
            ("starred",            "INTEGER DEFAULT 0"),
            ("resume_version",     "TEXT DEFAULT ''"),
            ("interest_score",     "INTEGER DEFAULT 0"),
            ("next_action",        "TEXT DEFAULT ''"),
            ("rejection_reason",   "TEXT DEFAULT ''"),
            ("archived",           "INTEGER DEFAULT 0"),
            ("quick_note",         "TEXT DEFAULT ''"),
        ]
        for col, typedef in new_cols:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # Column already exists

        # Add company_research column (Feature 7)
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN company_research TEXT DEFAULT ''")
        except Exception:
            pass

        # Add new columns to existing contacts table
        contact_new_cols = [
            ("phone", "TEXT DEFAULT ''"),
        ]
        for col, typedef in contact_new_cols:
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # Column already exists

        # Clean existing timeline entries across all jobs
        try:
            jobs = conn.execute("SELECT id FROM jobs").fetchall()
            for j in jobs:
                clean_timeline_for_job(conn, j["id"])
        except Exception:
            pass



STATUSES = [
    "applied", "screening", "phone_interview",
    "technical_interview", "final_interview",
    "offer", "rejected", "withdrawn", "ghosted",
]

# Statuses visible in the active pipeline (excludes archived)
ACTIVE_STATUSES = [s for s in STATUSES if s != "archived"]

STATUS_LABELS = {
    "applied": "Applied",
    "screening": "Screening",
    "phone_interview": "Phone Interview",
    "technical_interview": "Technical Interview",
    "final_interview": "Final Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
    "ghosted": "Ghosted",
}

STATUS_COLORS = {
    "applied": "#3b82f6",
    "screening": "#8b5cf6",
    "phone_interview": "#f59e0b",
    "technical_interview": "#f97316",
    "final_interview": "#ec4899",
    "offer": "#10b981",
    "rejected": "#ef4444",
    "withdrawn": "#6b7280",
    "ghosted": "#9ca3af",
}

DOC_TYPES = ["resume", "cover_letter", "portfolio", "assignment", "offer_letter", "other"]
DOC_TYPE_LABELS = {
    "resume": "Resume",
    "cover_letter": "Cover Letter",
    "portfolio": "Portfolio",
    "assignment": "Assignment",
    "offer_letter": "Offer Letter",
    "other": "Other",
}

ROUND_OUTCOMES = ["Pending", "Passed", "Failed", "Cancelled", "Unknown"]

REJECTION_REASONS = [
    "No response", "Salary mismatch", "Skills gap",
    "Overqualified", "Position filled", "Culture fit", "Other"
]


def compute_priority(job, today_d=None):
    """Compute a 0-100 priority score for a job card.
    Higher = needs more attention. Factors:
    - Interest score (higher interest → higher priority)
    - Days since applied (older without response → higher priority)
    - Overdue follow-up
    - Active stage (interview stages get a boost)
    """
    if today_d is None:
        today_d = date.today()
    score = 0
    # Interest score contribution (max 30 pts)
    interest = int(job["interest_score"] or 0)
    score += interest * 6  # 0..30
    # Stage boost (interview stages)
    stage_boost = {
        "phone_interview": 20, "technical_interview": 25,
        "final_interview": 30, "screening": 10,
    }
    score += stage_boost.get(job["status"], 0)
    # Days since applied (up to 30 pts for 30+ days without response)
    if job["applied_date"]:
        try:
            applied = date.fromisoformat(job["applied_date"][:10])
            days_old = (today_d - applied).days
            score += min(days_old, 30)
        except Exception:
            pass
    # Overdue follow-up
    try:
        fu = (job["follow_up_date"] or "").strip()
        if fu:
            fu_d = date.fromisoformat(fu)
            if fu_d <= today_d:
                score += 20
    except Exception:
        pass
    return min(score, 100)


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]


def _doc_base_name(company, role, doc_type):
    """Build a filename base from company/role/doc_type, joining only the
    non-empty parts. Naively joining with "_" regardless of whether
    company/role are blank (e.g. when JD extraction failed to find a
    company) produces a leading/doubled underscore like "_resume" instead
    of "resume" — and werkzeug's secure_filename() strips leading
    underscores, which previously made _claim_pending_upload() reject the
    filename outright and silently fail to attach the document."""
    parts = [p for p in (slugify(company), slugify(role), doc_type) if p]
    return "_".join(parts) if parts else f"upload_{doc_type}"


def make_doc_filename(company, role, doc_type, ext, job_id):
    base = _doc_base_name(company, role, doc_type)
    candidate = f"{base}.{ext}"
    path = os.path.join(UPLOAD_DIR, candidate)
    if not os.path.exists(path):
        return candidate
    for v in range(2, 100):
        candidate = f"{base}_v{v}.{ext}"
        path = os.path.join(UPLOAD_DIR, candidate)
        if not os.path.exists(path):
            return candidate
    return f"{base}_{job_id}.{ext}"


def save_tags(conn, job_id, tags_str):
    """Delete and re-insert tags for a job."""
    conn.execute("DELETE FROM tags WHERE job_id=?", (job_id,))
    if tags_str:
        for tag in tags_str.split(","):
            tag = tag.strip().lower()
            if tag:
                conn.execute("INSERT INTO tags (job_id, name) VALUES (?,?)", (job_id, tag))


def sync_recruiter_contact(conn, name, email, linkedin, company, role, phone=""):
    """Auto-add or update contact from recruiter fields. No-op if name is blank."""
    name = (name or "").strip()
    if not name:
        return
    email    = (email    or "").strip()
    linkedin = (linkedin or "").strip()
    company  = (company  or "").strip()
    phone    = (phone    or "").strip()
    title    = ("Recruiter" + (f" — {role}" if role else "")).strip(" —")

    existing = conn.execute(
        "SELECT id, email, linkedin, company, phone FROM contacts WHERE LOWER(name)=LOWER(?)",
        (name,)
    ).fetchone()

    if existing:
        # keep the previous value for any field that isn't being enriched with new data
        new_email    = email    or existing["email"]
        new_linkedin = linkedin or existing["linkedin"]
        new_company  = company  or existing["company"]
        new_phone    = phone    or existing["phone"]
        conn.execute(
            "UPDATE contacts SET email=?, linkedin=?, company=?, phone=?, title=? WHERE id=?",
            (new_email, new_linkedin, new_company, new_phone, title, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO contacts (name, email, linkedin, company, phone, title)
               VALUES (?,?,?,?,?,?)""",
            (name, email, linkedin, company, phone, title),
        )


def get_tags_for_jobs(conn, job_ids):
    """Return a dict {job_id: [tag_name, ...]} for a list of job ids."""
    if not job_ids:
        return {}
    placeholders = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"SELECT job_id, name FROM tags WHERE job_id IN ({placeholders}) ORDER BY name",
        job_ids
    ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["job_id"], []).append(r["name"])
    return result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    today = date.today().isoformat()
    today_plus7 = (date.today() + timedelta(days=7)).isoformat()

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        counts = {}
        for s in STATUSES:
            counts[s] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (s,)
            ).fetchone()[0]

        kanban_statuses = ["applied", "screening", "phone_interview",
                           "technical_interview", "final_interview", "offer"]
        kanban = {}
        for s in kanban_statuses:
            kanban[s] = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY applied_date DESC", (s,)
            ).fetchall()

        activity = conn.execute("""
            SELECT t.*, j.company, j.role, j.id as job_id
            FROM timeline t JOIN jobs j ON t.job_id = j.id
            ORDER BY t.created_at DESC LIMIT 10
        """).fetchall()

        followups = conn.execute("""
            SELECT * FROM jobs
            WHERE status NOT IN ('offer','rejected','withdrawn')
            AND (
                (follow_up_date != '' AND follow_up_date IS NOT NULL AND follow_up_date <= ?)
                OR (
                    (follow_up_date = '' OR follow_up_date IS NULL)
                    AND status IN ('applied','screening','ghosted')
                    AND applied_date <= date('now', '-7 days')
                )
            )
            ORDER BY applied_date ASC
        """, (today,)).fetchall()

        deadlines = conn.execute("""
            SELECT * FROM jobs
            WHERE offer_deadline != '' AND offer_deadline IS NOT NULL
            AND offer_deadline >= ? AND offer_deadline <= ?
            ORDER BY offer_deadline ASC
        """, (today, today_plus7)).fetchall()

        monthly = conn.execute("""
            SELECT substr(applied_date,1,7) as month, COUNT(*) as cnt
            FROM jobs WHERE applied_date != ''
            GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall()

        funnel = [
            (STATUS_LABELS[s], counts.get(s, 0), STATUS_COLORS[s])
            for s in ["applied", "screening", "phone_interview",
                      "technical_interview", "final_interview", "offer"]
        ]

        # Interview countdowns
        upcoming_interviews = {}
        rows = conn.execute("""
            SELECT ir.job_id, MIN(ir.interview_date) as next_date
            FROM interview_rounds ir JOIN jobs j ON ir.job_id = j.id
            WHERE ir.interview_date >= date('now')
            GROUP BY ir.job_id
        """).fetchall()
        for row in rows:
            try:
                d = date.fromisoformat(row['next_date'])
                upcoming_interviews[row['job_id']] = (d - date.today()).days
            except Exception:
                pass

        # Next action jobs (To Do list)
        next_action_jobs = conn.execute("""
            SELECT * FROM jobs
            WHERE next_action != '' AND next_action IS NOT NULL
            AND status NOT IN ('rejected','withdrawn','ghosted','offer')
            ORDER BY applied_date ASC
        """).fetchall()

        # Weekly & Monthly goals calculation
        monday = date.today() - timedelta(days=date.today().weekday())
        sunday = monday + timedelta(days=6)
        this_month_start = date.today().replace(day=1)
        
        job_dates = conn.execute(
            "SELECT applied_date FROM jobs WHERE applied_date IS NOT NULL AND applied_date != ''"
        ).fetchall()

        this_week_count = 0
        this_month_count = 0

        for row in job_dates:
            raw_date = row['applied_date'].strip()
            d = None
            if len(raw_date) >= 10:
                try:
                    d = date.fromisoformat(raw_date[:10])
                except Exception:
                    pass
            if d is None:
                for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y"):
                    try:
                        d = datetime.strptime(raw_date, fmt).date()
                        break
                    except Exception:
                        pass
            if d:
                if monday <= d <= sunday:
                    this_week_count += 1
                if d >= this_month_start:
                    this_month_count += 1

        goal_row = conn.execute(
            "SELECT value FROM settings WHERE key='weekly_goal'"
        ).fetchone()
        try:
            weekly_goal = int(goal_row['value']) if goal_row and goal_row['value'] else None
        except (ValueError, TypeError):
            weekly_goal = None

        monthly_goal_row = conn.execute(
            "SELECT value FROM settings WHERE key='monthly_goal'"
        ).fetchone()
        try:
            monthly_goal = int(monthly_goal_row['value']) if monthly_goal_row and monthly_goal_row['value'] else None
        except (ValueError, TypeError):
            monthly_goal = None

        # Mini heatmap: last 12 weeks for dashboard widget
        heatmap_start = date.today() - timedelta(days=83)
        heatmap_start = heatmap_start - timedelta(days=heatmap_start.weekday())  # align to Monday
        heatmap_rows = conn.execute("""
            SELECT applied_date, COUNT(*) as cnt
            FROM jobs WHERE applied_date >= ? AND applied_date != ''
            GROUP BY applied_date
        """, (heatmap_start.isoformat(),)).fetchall()
        heatmap_data = {row['applied_date']: row['cnt'] for row in heatmap_rows}
        heatmap_weeks_mini = []
        for w in range(12):
            week = []
            for d in range(7):
                day = heatmap_start + timedelta(days=w * 7 + d)
                week.append(day.isoformat())
            heatmap_weeks_mini.append(week)

    offer_rate = round(counts.get("offer", 0) / total * 100, 1) if total > 0 else 0
    response_rate = round(
        (total - counts.get("applied", 0) - counts.get("ghosted", 0)) / total * 100, 1
    ) if total > 0 else 0

    # Priority score for kanban cards
    today_d = date.today()
    kanban_priority = {}
    for s in kanban_statuses:
        for job in kanban[s]:
            kanban_priority[job["id"]] = compute_priority(job, today_d)

    # Follow-up due today (explicit follow_up_date <= today)
    followup_due = [j for j in followups if (j.get("follow_up_date") or "").strip() and
                    (j.get("follow_up_date") or "").strip() <= today]

    return render_template(
        "dashboard.html",
        total=total,
        counts=counts,
        kanban=kanban,
        kanban_statuses=kanban_statuses,
        activity=activity,
        followups=followups,
        followup_due=followup_due,
        deadlines=deadlines,
        monthly=monthly,
        funnel=funnel,
        offer_rate=offer_rate,
        response_rate=response_rate,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        today=today,
        upcoming_interviews=upcoming_interviews,
        next_action_jobs=next_action_jobs,
        this_week_count=this_week_count,
        weekly_goal=weekly_goal,
        monthly_goal=monthly_goal,
        this_month_count=this_month_count,
        kanban_priority=kanban_priority,
        heatmap_data=heatmap_data,
        heatmap_weeks_mini=heatmap_weeks_mini,
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["POST"])
def save_settings():
    with get_db() as conn:
        for key in ("weekly_goal", "monthly_goal"):
            if key in request.form:
                val = request.form.get(key, "").strip()
                if val:
                    try:
                        ival = int(val)
                        if ival > 0:
                            conn.execute(
                                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                (key, str(ival))
                            )
                            flash(f"{key.replace('_', ' ').title()} updated to {ival}.", "success")
                        else:
                            conn.execute("DELETE FROM settings WHERE key=?", (key,))
                            flash(f"{key.replace('_', ' ').title()} cleared.", "info")
                    except (ValueError, TypeError):
                        flash(f"Invalid value for {key.replace('_', ' ')}.", "error")
                else:
                    conn.execute("DELETE FROM settings WHERE key=?", (key,))
                    flash(f"{key.replace('_', ' ').title()} cleared.", "info")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Applications list
# ---------------------------------------------------------------------------

@app.route("/applications")
def index():
    status_filter = request.args.get("status", "")
    search = request.args.get("search", "")
    sort = request.args.get("sort", "applied_date")
    order = request.args.get("order", "desc")
    tag_filter = request.args.get("tag", "")
    starred_only = request.args.get("starred", "") == "1"
    show_archived = request.args.get("archived", "") == "1"
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1
    per_page = 20

    join_clause = ""
    where_parts = []
    params = []

    if tag_filter:
        join_clause = " JOIN tags ON tags.job_id = jobs.id"

    # Archived filter: show archived OR hide archived based on param
    if show_archived:
        where_parts.append("jobs.archived = 1")
    else:
        where_parts.append("(jobs.archived = 0 OR jobs.archived IS NULL)")

    if status_filter:
        where_parts.append("jobs.status = ?")
        params.append(status_filter)

    if starred_only:
        where_parts.append("jobs.starred = 1")

    if search:
        where_parts.append("(jobs.company LIKE ? OR jobs.role LIKE ? OR jobs.notes LIKE ? OR jobs.jd LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])

    if tag_filter:
        where_parts.append("tags.name = ?")
        params.append(tag_filter.lower())

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    valid_sorts = ["applied_date", "company", "role", "status", "created_at"]
    if sort not in valid_sorts:
        sort = "applied_date"
    order_sql = "DESC" if order == "desc" else "ASC"

    count_query = f"SELECT COUNT(DISTINCT jobs.id) FROM jobs{join_clause} {where_clause}"
    data_query = f"SELECT DISTINCT jobs.* FROM jobs{join_clause} {where_clause} ORDER BY jobs.{sort} {order_sql} LIMIT ? OFFSET ?"
    offset = (page - 1) * per_page

    with get_db() as conn:
        total_count = conn.execute(count_query, params).fetchone()[0]
        total_pages = (total_count + per_page - 1) // per_page
        jobs = conn.execute(data_query, params + [per_page, offset]).fetchall()

        counts = {}
        for s in STATUSES:
            counts[s] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=? AND (archived=0 OR archived IS NULL)", (s,)
            ).fetchone()[0]
        counts["total"] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE (archived=0 OR archived IS NULL)"
        ).fetchone()[0]
        counts["starred"] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE starred=1 AND (archived=0 OR archived IS NULL)"
        ).fetchone()[0]
        counts["archived"] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE archived=1"
        ).fetchone()[0]

        job_ids = [j["id"] for j in jobs]
        tags_by_job = get_tags_for_jobs(conn, job_ids)

        all_tags = conn.execute(
            "SELECT DISTINCT name FROM tags ORDER BY name"
        ).fetchall()

    # Compute days ago + priority score for each job
    today = date.today()
    days_ago = {}
    priority_scores = {}
    for job in jobs:
        if job['applied_date']:
            try:
                d = date.fromisoformat(job['applied_date'])
                days_ago[job['id']] = (today - d).days
            except Exception:
                pass
        priority_scores[job['id']] = compute_priority(job, today)

    return render_template(
        "index.html",
        jobs=jobs,
        counts=counts,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        current_status=status_filter,
        current_search=search,
        current_sort=sort,
        current_order=order,
        current_tag=tag_filter,
        starred_only=starred_only,
        show_archived=show_archived,
        tags_by_job=tags_by_job,
        all_tags=all_tags,
        days_ago=days_ago,
        today=today.isoformat(),
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
        priority_scores=priority_scores,
    )


# ---------------------------------------------------------------------------
# Add / Edit job
# ---------------------------------------------------------------------------

@app.route("/quick-add", methods=["POST"])
def quick_add():
    company = request.form.get("company", "").strip()
    role = request.form.get("role", "").strip()
    if not company or not role:
        flash("Company and Role are required.", "error")
        return redirect(request.referrer or url_for("index"))
    status = request.form.get("status", "applied")
    if status not in STATUSES:
        status = "applied"
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO jobs (company, role, status, applied_date, source)
               VALUES (?,?,?,?,?)""",
            (company, role, status, date.today().isoformat(),
             request.form.get("source", "").strip())
        )
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
            (job_id, "Applied", date.today().isoformat(), "Quick add")
        )
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/add", methods=["GET", "POST"])
def add_job():
    if request.method == "POST":
        company = request.form["company"]
        role = request.form["role"]

        with get_db() as conn:
            # Duplicate check
            existing = conn.execute(
                "SELECT id FROM jobs WHERE LOWER(company)=LOWER(?) AND LOWER(role)=LOWER(?)",
                (company, role)
            ).fetchone()
            if existing:
                flash(
                    f"Heads up: a job at \"{company}\" for \"{role}\" already exists. "
                    "Saved anyway — review for duplicates.",
                    "warning"
                )

            cur = conn.execute(
                """INSERT INTO jobs
                   (company, role, jd, job_url, applied_date, status, source,
                    salary_range, location, notes,
                    recruiter_name, recruiter_email, recruiter_phone, recruiter_linkedin,
                    follow_up_date, offer_deadline, resume_version,
                    interest_score, next_action)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    company,
                    role,
                    request.form.get("jd", ""),
                    request.form.get("job_url", ""),
                    request.form.get("applied_date") or date.today().isoformat(),
                    request.form.get("status", "applied"),
                    request.form.get("source", ""),
                    request.form.get("salary_range", ""),
                    request.form.get("location", ""),
                    request.form.get("notes", ""),
                    request.form.get("recruiter_name", ""),
                    request.form.get("recruiter_email", ""),
                    request.form.get("recruiter_phone", ""),
                    request.form.get("recruiter_linkedin", ""),
                    request.form.get("follow_up_date", ""),
                    request.form.get("offer_deadline", ""),
                    request.form.get("resume_version", ""),
                    (lambda x: int(x) if x and x.isdigit() else 0)(request.form.get("interest_score", "")),
                    request.form.get("next_action", ""),
                ),
            )
            job_id = cur.lastrowid
            conn.execute(
                "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                (job_id, "Applied",
                 request.form.get("applied_date") or date.today().isoformat(),
                 "Initial application"),
            )
            save_tags(conn, job_id, request.form.get("tags", ""))
            sync_recruiter_contact(conn,
                request.form.get("recruiter_name", ""),
                request.form.get("recruiter_email", ""),
                request.form.get("recruiter_linkedin", ""),
                company, role,
                phone=request.form.get("recruiter_phone", ""))

        files = request.files.getlist("documents")
        doc_types = request.form.getlist("doc_types")
        _save_uploads(files, doc_types, job_id, company, role)

        return redirect(url_for("job_detail", job_id=job_id))

    return render_template(
        "add_job.html",
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        doc_types=DOC_TYPES,
        doc_type_labels=DOC_TYPE_LABELS,
        today=date.today().isoformat(),
    )


EMPTY_JOB_TEMPLATE = {
    "company": "", "role": "", "status": "applied",
    "applied_date": "", "source": "", "location": "", "salary_range": "",
    "jd": "", "job_url": "", "tags": "", "interest_score": "3", "notes": "",
    "next_action": "Follow up in 1 week", "follow_up_date": "", "offer_deadline": "",
    "resume_version": "", "recruiter_name": "", "recruiter_email": "",
    "recruiter_phone": "", "recruiter_linkedin": "",
}


@app.route("/generate", methods=["GET", "POST"])
def generate_job():
    error = None
    warnings = []
    generated = False
    job = dict(EMPTY_JOB_TEMPLATE)
    job["applied_date"] = date.today().isoformat()
    cv_info = None
    cover_info = None

    cv_pending = cover_pending = None

    if request.method == "GET":
        get_jd = request.args.get("jd_text", "").strip()
        if get_jd:
            job["jd"] = get_jd

    if request.method == "POST":
        try:
            import gen_job
            from gen_job import generate, pdf_to_text

            jd_text = request.form.get("jd_text", "").strip()

            # Save each uploaded PDF EXACTLY ONCE (as a generically-named
            # pending file — we don't know company/role yet), then extract
            # its text from that saved copy on disk. Re-reading the original
            # FileStorage a second time (e.g. to also save a "nicely named"
            # copy) would silently produce a 0-byte/corrupted file, since its
            # upload stream can only be consumed once — see the warning on
            # _save_pending_upload_raw().
            def _upload_and_extract(field, label, doc_type):
                f = request.files.get(field)
                if not f or not f.filename:
                    return "", "", None
                pending_name = _save_pending_upload_raw(f, doc_type)
                if not pending_name:
                    warnings.append(
                        f"'{f.filename}' isn't a supported file type for {label} "
                        f"(allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))})."
                    )
                    return "", f.filename, None
                text = pdf_to_text(os.path.join(UPLOAD_DIR, pending_name))
                if not text.strip():
                    warnings.append(
                        f"Could not extract any text from the {label} PDF "
                        f"'{f.filename}' — it may be scanned/image-only, "
                        f"corrupt, or pdfminer.six may not be installed."
                    )
                return text, f.filename, pending_name

            cv_text, cv_name, cv_pending          = _upload_and_extract("cv_pdf", "CV/Resume", "resume")
            cover_text, cover_name, cover_pending = _upload_and_extract("cover_pdf", "Cover Letter", "cover_letter")
            if cv_text.strip():
                cv_info = {"filename": cv_name, "chars": len(cv_text)}
            if cover_text.strip():
                cover_info = {"filename": cover_name, "chars": len(cover_text)}

            if not jd_text:
                error = "Job description is required."
            else:
                job = generate(jd_text, cv_text, cover_text, cv_name, "")
                generated = True
                ai_status = gen_job.LAST_AI_STATUS
                if ai_status == "ok":
                    warnings.append(
                        "Some fields were blank after regex extraction — filled in "
                        f"using the local AI model ({gen_job.AI_MODEL}, offline). "
                        "Double-check AI-filled fields before pushing."
                    )
                elif ai_status and ai_status != "skipped (regex found company and role)":
                    warnings.append(
                        f"Some fields were left blank and the local AI model couldn't "
                        f"fill them in ({ai_status}). Run 'ollama serve' to enable "
                        "AI-assisted extraction, or fill those fields in manually."
                    )

            # Now that company/role are known (if extraction succeeded), give
            # the already-saved pending files a human-readable name. This is
            # a plain filesystem rename — it never re-touches the original
            # upload stream, so it can't corrupt the file.
            company = job.get("company", "")
            role    = job.get("role", "")
            if cv_pending:
                cv_pending = _rename_pending_upload(cv_pending, company, role, "resume")
            if cover_pending:
                cover_pending = _rename_pending_upload(cover_pending, company, role, "cover_letter")
        except Exception as e:
            error = str(e)

    return render_template(
        "generate.html", job=job, generated=generated, cv_info=cv_info,
        cover_info=cover_info, cv_pending=cv_pending, cover_pending=cover_pending,
        error=error, warnings=warnings, statuses=STATUSES,
        status_labels=STATUS_LABELS,
    )


@app.route("/api/job", methods=["POST"])
def api_add_job():
    """JSON API — create a job. Returns {id, url} on success."""
    d = request.get_json(force=True, silent=True)
    if not d:
        return {"error": "invalid JSON"}, 400
    company = (d.get("company") or "").strip()
    role = (d.get("role") or "").strip()
    if not company or not role:
        return {"error": "company and role are required"}, 400

    interest_raw = str(d.get("interest_score", ""))
    interest = int(interest_raw) if interest_raw.isdigit() and 1 <= int(interest_raw) <= 5 else 0
    applied = d.get("applied_date") or date.today().isoformat()
    status = d.get("status", "applied")
    if status not in STATUSES:
        status = "applied"

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE LOWER(company)=LOWER(?) AND LOWER(role)=LOWER(?)",
            (company, role)
        ).fetchone()
        duplicate = existing is not None

        cur = conn.execute(
            """INSERT INTO jobs
               (company, role, jd, job_url, applied_date, status, source,
                salary_range, location, notes,
                recruiter_name, recruiter_email, recruiter_phone, recruiter_linkedin,
                follow_up_date, offer_deadline, resume_version,
                interest_score, next_action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                company, role,
                d.get("jd", ""),
                d.get("job_url", ""),
                applied,
                status,
                d.get("source", ""),
                d.get("salary_range", ""),
                d.get("location", ""),
                d.get("notes", ""),
                d.get("recruiter_name", ""),
                d.get("recruiter_email", ""),
                d.get("recruiter_phone", ""),
                d.get("recruiter_linkedin", ""),
                d.get("follow_up_date", ""),
                d.get("offer_deadline", ""),
                d.get("resume_version", ""),
                interest,
                d.get("next_action", ""),
            ),
        )
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
            (job_id, "Applied", applied, "Initial application"),
        )
        save_tags(conn, job_id, d.get("tags", ""))
        sync_recruiter_contact(conn,
            d.get("recruiter_name", ""),
            d.get("recruiter_email", ""),
            d.get("recruiter_linkedin", ""),
            company, role,
            phone=d.get("recruiter_phone", ""))

        # Claim any pending uploads from the Generate page (CV/cover letter)
        # so they become real documents attached to the new job.
        for pending_name, doc_type in [(d.get("cv_pending", ""), "resume"),
                                        (d.get("cover_pending", ""), "cover_letter")]:
            if pending_name:
                _claim_pending_upload(conn, pending_name, "", doc_type, job_id, company, role)

    return {
        "id": job_id,
        "url": url_for("job_detail", job_id=job_id, _external=True),
        "duplicate_warning": duplicate,
    }, 201


def _save_uploads(files, doc_types, job_id, company, role):
    with get_db() as conn:
        for i, f in enumerate(files):
            if not f or not f.filename:
                continue
            if "." not in f.filename:
                continue
            ext = f.filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            dtype = doc_types[i] if i < len(doc_types) else "resume"
            saved_name = make_doc_filename(company, role, dtype, ext, job_id)
            f.save(os.path.join(UPLOAD_DIR, saved_name))
            conn.execute(
                "INSERT INTO documents (job_id, filename, original_name, doc_type) VALUES (?,?,?,?)",
                (job_id, saved_name, f.filename, dtype),
            )


@app.route("/job/<int:job_id>/edit", methods=["GET", "POST"])
def edit_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))

        tags = conn.execute(
            "SELECT name FROM tags WHERE job_id=? ORDER BY name", (job_id,)
        ).fetchall()
        tag_str = ", ".join(t["name"] for t in tags)

        if request.method == "POST":
            old_status = job["status"]
            new_status = request.form.get("status", old_status)
            conn.execute(
                """UPDATE jobs SET
                   company=?, role=?, jd=?, job_url=?, applied_date=?,
                   status=?, source=?, salary_range=?, location=?, notes=?,
                   recruiter_name=?, recruiter_email=?, recruiter_phone=?, recruiter_linkedin=?,
                   follow_up_date=?, offer_deadline=?, resume_version=?,
                   interest_score=?, next_action=?, rejection_reason=?,
                   updated_at=datetime('now')
                   WHERE id=?""",
                (
                    request.form["company"],
                    request.form["role"],
                    request.form.get("jd", ""),
                    request.form.get("job_url", ""),
                    request.form.get("applied_date", ""),
                    new_status,
                    request.form.get("source", ""),
                    request.form.get("salary_range", ""),
                    request.form.get("location", ""),
                    request.form.get("notes", ""),
                    request.form.get("recruiter_name", ""),
                    request.form.get("recruiter_email", ""),
                    request.form.get("recruiter_phone", ""),
                    request.form.get("recruiter_linkedin", ""),
                    request.form.get("follow_up_date", ""),
                    request.form.get("offer_deadline", ""),
                    request.form.get("resume_version", ""),
                    (lambda x: int(x) if x and x.isdigit() else 0)(request.form.get("interest_score", "")),
                    request.form.get("next_action", ""),
                    request.form.get("rejection_reason", ""),
                    job_id,
                ),
            )
            if old_status != new_status or request.form.get("status_note", "").strip():
                conn.execute(
                    "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                    (
                        job_id,
                        STATUS_LABELS.get(new_status, new_status),
                        date.today().isoformat(),
                        request.form.get("status_note", ""),
                    ),
                )
            save_tags(conn, job_id, request.form.get("tags", ""))
            sync_recruiter_contact(conn,
                request.form.get("recruiter_name", ""),
                request.form.get("recruiter_email", ""),
                request.form.get("recruiter_linkedin", ""),
                request.form.get("company", ""), request.form.get("role", ""),
                phone=request.form.get("recruiter_phone", ""))
            return redirect(url_for("job_detail", job_id=job_id))

    return render_template(
        "edit_job.html",
        job=job,
        tag_str=tag_str,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        rejection_reasons=REJECTION_REASONS,
        today=date.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# Job detail
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>")
def job_detail(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))
        timeline = conn.execute(
            "SELECT * FROM timeline WHERE job_id=? ORDER BY event_date ASC, created_at ASC",
            (job_id,),
        ).fetchall()
        documents = conn.execute(
            "SELECT * FROM documents WHERE job_id=? ORDER BY uploaded_at DESC",
            (job_id,),
        ).fetchall()
        interview_rounds = conn.execute(
            "SELECT * FROM interview_rounds WHERE job_id=? ORDER BY interview_date ASC, created_at ASC",
            (job_id,),
        ).fetchall()
        tags_rows = conn.execute(
            "SELECT name FROM tags WHERE job_id=? ORDER BY name", (job_id,)
        ).fetchall()
        negotiation = conn.execute(
            "SELECT * FROM salary_negotiations WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job_id,)
        ).fetchone()

    tag_names = [t["name"] for t in tags_rows]

    return render_template(
        "job_detail.html",
        job=job,
        timeline=timeline,
        documents=documents,
        interview_rounds=interview_rounds,
        tags=tag_names,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        doc_types=DOC_TYPES,
        doc_type_labels=DOC_TYPE_LABELS,
        round_outcomes=ROUND_OUTCOMES,
        negotiation=negotiation,
    )


# ---------------------------------------------------------------------------
# Upload / documents
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/upload", methods=["POST"])
def upload_doc(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            abort(404)
    files = request.files.getlist("documents")
    doc_types = request.form.getlist("doc_types")
    _save_uploads(files, doc_types, job_id, job["company"], job["role"])
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    safe = secure_filename(filename)
    return send_from_directory(UPLOAD_DIR, safe)


@app.route("/doc/<int:doc_id>/delete", methods=["POST"])
def delete_doc(doc_id):
    with get_db() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if doc:
            path = os.path.join(UPLOAD_DIR, doc["filename"])
            if os.path.exists(path):
                os.remove(path)
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            return redirect(url_for("job_detail", job_id=doc["job_id"]))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------

def clean_timeline_for_job(conn, job_id):
    job = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    status = job["status"]
    timeline = conn.execute(
        "SELECT id, event, event_date, notes FROM timeline WHERE job_id=? ORDER BY id ASC",
        (job_id,)
    ).fetchall()
    if not timeline:
        return

    to_delete = set()
    if status == "applied":
        first_applied_id = None
        for item in timeline:
            if item["event"] == "Applied" and first_applied_id is None:
                first_applied_id = item["id"]
            elif first_applied_id is not None:
                to_delete.add(item["id"])
    else:
        first_applied = False
        prev_event = None
        for item in timeline:
            ev = item["event"]
            tid = item["id"]
            if ev == "Applied":
                if first_applied:
                    to_delete.add(tid)
                else:
                    first_applied = True
            elif ev == prev_event:
                to_delete.add(tid)
            prev_event = ev

    for tid in to_delete:
        conn.execute("DELETE FROM timeline WHERE id=?", (tid,))


@app.route("/job/<int:job_id>/add_event", methods=["POST"])
def add_event(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            abort(404)
        conn.execute(
            "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
            (
                job_id,
                request.form["event"],
                request.form.get("event_date") or date.today().isoformat(),
                request.form.get("notes", ""),
            ),
        )
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/job/<int:job_id>/timeline/<int:event_id>/delete", methods=["POST"])
def delete_timeline_event(job_id, event_id):
    with get_db() as conn:
        conn.execute("DELETE FROM timeline WHERE id=? AND job_id=?", (event_id, job_id))
    flash("Timeline entry deleted.", "success")
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/job/<int:job_id>/clean-timeline", methods=["POST"])
def clean_job_timeline(job_id):
    with get_db() as conn:
        clean_timeline_for_job(conn, job_id)
    flash("Timeline cleaned up!", "success")
    return redirect(url_for("job_detail", job_id=job_id))



@app.route("/api/timeline", methods=["POST"])
def api_add_timeline():
    """JSON: {job_ids:[1,2,...], event:'...', notes:'...'}"""
    d = request.get_json(force=True, silent=True) or {}
    job_ids = d.get("job_ids") or []
    event   = (d.get("event") or "").strip()
    notes   = (d.get("notes") or "").strip()
    if not job_ids or not event:
        return {"error": "job_ids and event required"}, 400
    safe_ids = []
    for jid in job_ids:
        try:
            safe_ids.append(int(jid))
        except (TypeError, ValueError):
            pass
    if not safe_ids:
        return {"error": "no valid job_ids"}, 400
    today = date.today().isoformat()
    inserted = []
    with get_db() as conn:
        for jid in safe_ids:
            row = conn.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                    (jid, event, today, notes),
                )
                inserted.append(jid)
    return {"inserted": inserted}, 201


# ---------------------------------------------------------------------------
# Archive / Unarchive job
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/archive", methods=["POST"])
def archive_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT archived FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return jsonify({"ok": False, "error": "Not found"}), 404
        new_val = 0 if job["archived"] else 1
        conn.execute("UPDATE jobs SET archived=? WHERE id=?", (new_val, job_id))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.form.get("ajax"):
        return jsonify({"ok": True, "archived": bool(new_val)})
    return redirect(request.referrer or url_for("index"))


# ---------------------------------------------------------------------------
# Quick Note (inline AJAX save)
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/quick-note", methods=["POST"])
def quick_note(job_id):
    note = (request.form.get("quick_note") or request.get_json(force=True, silent=True) or {}).get("quick_note", "") \
        if request.content_type and "json" in request.content_type \
        else request.form.get("quick_note", "")
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return jsonify({"ok": False}), 404
        conn.execute("UPDATE jobs SET quick_note=? WHERE id=?", (note, job_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Duplicate check API
# ---------------------------------------------------------------------------

@app.route("/api/check-duplicate")
def check_duplicate():
    company = (request.args.get("company") or "").strip()
    role = (request.args.get("role") or "").strip()
    if not company or not role:
        return jsonify({"duplicate": False})
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE LOWER(company)=LOWER(?) AND LOWER(role)=LOWER(?)",
            (company, role)
        ).fetchone()
    return jsonify({"duplicate": row is not None, "id": row["id"] if row else None})


# ---------------------------------------------------------------------------
# Delete job
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            abort(404)
        docs = conn.execute(
            "SELECT filename FROM documents WHERE job_id=?", (job_id,)
        ).fetchall()
        for doc in docs:
            path = os.path.join(UPLOAD_DIR, doc["filename"])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Quick status update (inline, AJAX)
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/quick-status", methods=["POST"])
def quick_status(job_id):
    new_status = request.form.get("new_status")
    if new_status not in STATUSES:
        abort(400)
    skip_timeline   = request.form.get("skip_timeline", "0") == "1"
    rejection_reason = request.form.get("rejection_reason", "").strip()
    rejection_note   = request.form.get("rejection_note", "").strip()
    with get_db() as conn:
        old = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not old:
            return jsonify({"ok": False, "error": "Job not found"}), 404
        if old["status"] != new_status or rejection_note or rejection_reason:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                (new_status, job_id)
            )
            if new_status == "rejected" and rejection_reason:
                conn.execute(
                    "UPDATE jobs SET rejection_reason=? WHERE id=?",
                    (rejection_reason, job_id)
                )
            if not skip_timeline:
                conn.execute(
                    "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,date('now'),?)",
                    (job_id, STATUS_LABELS.get(new_status, new_status), rejection_note)
                )
    return jsonify({"ok": True, "status": new_status, "label": STATUS_LABELS.get(new_status, new_status)})


@app.route("/job/<int:job_id>/reset-to-applied", methods=["POST"])
def reset_to_applied(job_id):
    """Reset a job to Applied status and clean up spurious stage
    entries from the timeline, keeping only the original initial application."""
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return jsonify({"ok": False, "error": "Not found"}), 404
        conn.execute(
            "UPDATE jobs SET status='applied', updated_at=datetime('now') WHERE id=?",
            (job_id,)
        )
        clean_timeline_for_job(conn, job_id)
    return jsonify({"ok": True})





# ---------------------------------------------------------------------------
# Clone job
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/clone", methods=["POST"])
def clone_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))
        cur = conn.execute(
            """INSERT INTO jobs (company, role, jd, job_url, applied_date, status, source,
                              salary_range, location, notes, recruiter_name, recruiter_email,
                              recruiter_phone, recruiter_linkedin, resume_version, interest_score, next_action)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job["company"], job["role"], job["jd"], job["job_url"],
             date.today().isoformat(), "applied", job["source"],
             job["salary_range"], job["location"], job["notes"],
             job["recruiter_name"], job["recruiter_email"], job["recruiter_phone"], job["recruiter_linkedin"],
             job["resume_version"], job["interest_score"], job["next_action"])
        )
        new_id = cur.lastrowid
        conn.execute(
            "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
            (new_id, "Applied", date.today().isoformat(), f"Cloned from job #{job_id}")
        )
        # copy tags
        tags = conn.execute("SELECT name FROM tags WHERE job_id=?", (job_id,)).fetchall()
        for t in tags:
            conn.execute("INSERT INTO tags (job_id, name) VALUES (?,?)", (new_id, t["name"]))
    return redirect(url_for("edit_job", job_id=new_id))


# ---------------------------------------------------------------------------
# Print view
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/print")
def print_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))
        timeline = conn.execute(
            "SELECT * FROM timeline WHERE job_id=? ORDER BY event_date ASC",
            (job_id,)
        ).fetchall()
        documents = conn.execute(
            "SELECT * FROM documents WHERE job_id=?", (job_id,)
        ).fetchall()
        tags = [r["name"] for r in conn.execute(
            "SELECT name FROM tags WHERE job_id=?", (job_id,)
        ).fetchall()]
        rounds = conn.execute(
            "SELECT * FROM interview_rounds WHERE job_id=? ORDER BY interview_date ASC",
            (job_id,)
        ).fetchall()
    return render_template(
        "print_job.html",
        job=job,
        timeline=timeline,
        documents=documents,
        tags=tags,
        rounds=rounds,
        status_labels=STATUS_LABELS,
        doc_type_labels=DOC_TYPE_LABELS,
    )


# ---------------------------------------------------------------------------
# Salary negotiation
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/negotiation", methods=["POST"])
def save_negotiation(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            abort(404)
        existing = conn.execute(
            "SELECT id FROM salary_negotiations WHERE job_id=?", (job_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE salary_negotiations
                   SET initial_offer=?, counter_offer=?, final_amount=?, notes=?
                   WHERE job_id=?""",
                (
                    request.form.get("initial_offer", ""),
                    request.form.get("counter_offer", ""),
                    request.form.get("final_amount", ""),
                    request.form.get("notes", ""),
                    job_id,
                )
            )
        else:
            conn.execute(
                """INSERT INTO salary_negotiations
                   (job_id, initial_offer, counter_offer, final_amount, notes)
                   VALUES (?,?,?,?,?)""",
                (
                    job_id,
                    request.form.get("initial_offer", ""),
                    request.form.get("counter_offer", ""),
                    request.form.get("final_amount", ""),
                    request.form.get("notes", ""),
                )
            )
    return redirect(url_for("job_detail", job_id=job_id))


# ---------------------------------------------------------------------------
# Star / Unstar
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/star", methods=["POST"])
def star_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT starred, company FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job:
            new_val = 0 if job["starred"] else 1
            conn.execute("UPDATE jobs SET starred=? WHERE id=?", (new_val, job_id))
            company = job["company"]
            starred = bool(new_val)
        else:
            company = ""
            starred = False
    # Support both AJAX (returns JSON) and form POST (redirects)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.form.get("ajax") or request.args.get("ajax"):
        return jsonify({"ok": True, "starred": starred, "company": company})
    next_url = request.form.get("next") or request.referrer or url_for("index")
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Interview rounds
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/interviews", methods=["GET", "POST"])
def interviews(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))

        if request.method == "POST":
            conn.execute(
                """INSERT INTO interview_rounds
                   (job_id, round_name, interview_date, notes, questions_asked, outcome)
                   VALUES (?,?,?,?,?,?)""",
                (
                    job_id,
                    request.form.get("round_name", ""),
                    request.form.get("interview_date", ""),
                    request.form.get("notes", ""),
                    request.form.get("questions_asked", ""),
                    request.form.get("outcome", "Pending"),
                ),
            )
            return redirect(url_for("job_detail", job_id=job_id))

        rounds = conn.execute(
            "SELECT * FROM interview_rounds WHERE job_id=? ORDER BY interview_date ASC, created_at ASC",
            (job_id,),
        ).fetchall()

    return render_template(
        "interviews.html",
        job=job,
        rounds=rounds,
        round_outcomes=ROUND_OUTCOMES,
        today=date.today().isoformat(),
    )


@app.route("/job/<int:job_id>/interviews/<int:round_id>/delete", methods=["POST"])
def delete_round(job_id, round_id):
    with get_db() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            abort(404)
        conn.execute(
            "DELETE FROM interview_rounds WHERE id=? AND job_id=?", (round_id, job_id)
        )
    return redirect(url_for("job_detail", job_id=job_id))


# ---------------------------------------------------------------------------
# Interview prep checklist
# ---------------------------------------------------------------------------

DEFAULT_CHECKLIST = [
    "Research company background and recent news",
    "Review job description thoroughly",
    "Prepare answers for common behavioral questions (STAR format)",
    "Prepare 3–5 questions to ask the interviewer",
    "Review your resume and be ready to walk through it",
    "Test your tech setup (camera, mic, internet)",
    "Prepare relevant code samples or portfolio links",
]

@app.route("/job/<int:job_id>/checklist", methods=["GET", "POST"])
def checklist(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))

        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                item = request.form.get("item", "").strip()
                if item:
                    conn.execute(
                        "INSERT INTO interview_checklist (job_id, item) VALUES (?,?)",
                        (job_id, item)
                    )
            elif action == "toggle":
                item_id = request.form.get("item_id")
                if item_id:
                    row = conn.execute(
                        "SELECT done FROM interview_checklist WHERE id=? AND job_id=?",
                        (item_id, job_id)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "UPDATE interview_checklist SET done=? WHERE id=?",
                            (0 if row["done"] else 1, item_id)
                        )
            elif action == "delete":
                item_id = request.form.get("item_id")
                if item_id:
                    conn.execute(
                        "DELETE FROM interview_checklist WHERE id=? AND job_id=?",
                        (item_id, job_id)
                    )
            elif action == "seed":
                existing = conn.execute(
                    "SELECT COUNT(*) FROM interview_checklist WHERE job_id=?", (job_id,)
                ).fetchone()[0]
                if existing == 0:
                    for item in DEFAULT_CHECKLIST:
                        conn.execute(
                            "INSERT INTO interview_checklist (job_id, item) VALUES (?,?)",
                            (job_id, item)
                        )
            return redirect(url_for("checklist", job_id=job_id))

        items = conn.execute(
            "SELECT * FROM interview_checklist WHERE job_id=? ORDER BY created_at ASC",
            (job_id,)
        ).fetchall()

    done_count = sum(1 for i in items if i["done"])
    return render_template(
        "checklist.html",
        job=job,
        items=items,
        done_count=done_count,
    )


# ---------------------------------------------------------------------------
# Interview Calendar
# ---------------------------------------------------------------------------

@app.route("/calendar")
def calendar_view():
    with get_db() as conn:
        rounds = conn.execute("""
            SELECT ir.*, j.company, j.role, j.id as job_id, j.status
            FROM interview_rounds ir
            JOIN jobs j ON ir.job_id = j.id
            WHERE ir.interview_date != '' AND ir.interview_date IS NOT NULL
            ORDER BY ir.interview_date ASC
        """).fetchall()
    # Group by date
    by_date = {}
    for r in rounds:
        key = r["interview_date"][:10] if r["interview_date"] else ""
        if key:
            by_date.setdefault(key, []).append(r)
    today = date.today()
    return render_template(
        "calendar.html",
        by_date=by_date,
        today=today.isoformat(),
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
    )


# ---------------------------------------------------------------------------
# Kanban Board
# ---------------------------------------------------------------------------

@app.route("/kanban")
def kanban_view():
    kanban_statuses = ["applied", "screening", "phone_interview",
                       "technical_interview", "final_interview", "offer"]
    with get_db() as conn:
        kanban = {}
        for s in kanban_statuses:
            jobs = conn.execute(
                "SELECT * FROM jobs WHERE status=? AND (archived=0 OR archived IS NULL) ORDER BY applied_date DESC",
                (s,)
            ).fetchall()
            kanban[s] = jobs
        job_ids_all = [j["id"] for jobs_list in kanban.values() for j in jobs_list]
        tags_by_job = get_tags_for_jobs(conn, job_ids_all)
    today_d = date.today()
    priority = {}
    for jobs_list in kanban.values():
        for job in jobs_list:
            priority[job["id"]] = compute_priority(job, today_d)
    return render_template(
        "kanban.html",
        kanban=kanban,
        kanban_statuses=kanban_statuses,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        tags_by_job=tags_by_job,
        priority=priority,
        statuses=STATUSES,
    )


# ---------------------------------------------------------------------------
# Bulk status update
# ---------------------------------------------------------------------------

@app.route("/bulk-update", methods=["POST"])
def bulk_update():
    job_ids = request.form.getlist("job_ids")
    new_status = request.form.get("new_status", "")
    rejection_note = request.form.get("rejection_note", "").strip()
    skip_timeline  = request.form.get("skip_timeline", "0") == "1"
    if job_ids and new_status and new_status in STATUSES:
        timeline_note = rejection_note if new_status == "rejected" and rejection_note else "Bulk status update"
        with get_db() as conn:
            for jid in job_ids:
                try:
                    jid = int(jid)
                    old = conn.execute(
                        "SELECT status FROM jobs WHERE id=?", (jid,)
                    ).fetchone()
                    if old and (old["status"] != new_status or rejection_note):
                        conn.execute(
                            "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                            (new_status, jid),
                        )
                        if not skip_timeline:
                            conn.execute(
                                "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                                (jid, STATUS_LABELS.get(new_status, new_status),
                                 date.today().isoformat(), timeline_note),
                            )
                except (ValueError, TypeError):
                    pass
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.form.get("ajax"):
        return jsonify({"ok": True})
    return redirect(url_for("index"))



# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@app.route("/export/csv")
def export_csv():
    with get_db() as conn:
        jobs = conn.execute("""
            SELECT id, company, role, status, applied_date, location, salary_range, source,
                   job_url, notes, recruiter_name, recruiter_email, recruiter_phone, recruiter_linkedin,
                   follow_up_date, offer_deadline, resume_version, starred,
                   interest_score, next_action, rejection_reason,
                   created_at, updated_at
            FROM jobs ORDER BY applied_date DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Role", "Status", "Applied Date", "Location", "Salary Range",
        "Source", "Job URL", "Notes", "Recruiter Name", "Recruiter Email", "Recruiter Phone",
        "Recruiter LinkedIn", "Follow Up Date", "Offer Deadline", "Resume Version",
        "Starred", "Interest Score", "Next Action", "Rejection Reason",
        "Created At", "Updated At",
    ])
    for job in jobs:
        writer.writerow([
            job["id"], job["company"], job["role"],
            STATUS_LABELS.get(job["status"], job["status"]),
            job["applied_date"] or "",
            job["location"] or "", job["salary_range"] or "", job["source"] or "",
            job["job_url"] or "", job["notes"] or "",
            job["recruiter_name"] or "", job["recruiter_email"] or "", job["recruiter_phone"] or "",
            job["recruiter_linkedin"] or "",
            job["follow_up_date"] or "", job["offer_deadline"] or "",
            job["resume_version"] or "",
            "Yes" if job["starred"] else "No",
            job["interest_score"] or 0,
            job["next_action"] or "",
            job["rejection_reason"] or "",
            job["created_at"] or "", job["updated_at"] or "",
        ])

    response = make_response(output.getvalue())
    fname = f"jobs_export_{date.today().isoformat()}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={fname}"
    response.headers["Content-Type"] = "text/csv"
    return response


@app.route("/export-report")
def export_report():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs = conn.execute("SELECT * FROM jobs ORDER BY applied_date DESC").fetchall()
        counts = {}
        for s in STATUSES:
            counts[s] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (s,)).fetchone()[0]

    lines = [
        "===================================================",
        "        JOB APPLICATION TRACKER (JAT) REPORT       ",
        f"        Generated on {date.today().isoformat()}    ",
        "===================================================",
        "",
        f"Total Applications: {total}",
        f"Offers: {counts.get('offer', 0)}",
        f"Interviews: {counts.get('phone_interview', 0) + counts.get('technical_interview', 0) + counts.get('final_interview', 0)}",
        f"Rejections: {counts.get('rejected', 0)}",
        f"Ghosted: {counts.get('ghosted', 0)}",
        "",
        "---------------------------------------------------",
        "APPLICATION DETAILS",
        "---------------------------------------------------",
    ]

    for j in jobs:
        status_lbl = STATUS_LABELS.get(j['status'], j['status'])
        lines.append(f"• [{j['applied_date'] or 'N/A'}] {j['company']} — {j['role']} ({status_lbl})")
        if j['location'] or j['salary_range']:
            lines.append(f"  Location: {j['location'] or 'N/A'} | Salary: {j['salary_range'] or 'N/A'}")
        if j['next_action']:
            lines.append(f"  Next Action: {j['next_action']}")

    content = "\n".join(lines)
    response = make_response(content)
    fname = f"jat_report_{date.today().isoformat()}.txt"
    response.headers["Content-Disposition"] = f"attachment; filename={fname}"
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    return response


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

@app.route("/import/csv", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        f = request.files.get("csv_file")
        if not f or not f.filename.endswith(".csv"):
            flash("Please upload a valid .csv file.", "error")
            return redirect(url_for("import_csv"))

        imported = 0
        skipped = 0
        errors = []

        try:
            content = f.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))

            STATUS_REVERSE = {v.lower(): k for k, v in STATUS_LABELS.items()}
            STATUS_REVERSE.update({k: k for k in STATUSES})

            with get_db() as conn:
                for i, row in enumerate(reader, start=2):
                    company = (row.get("Company") or row.get("company") or "").strip()
                    role = (row.get("Role") or row.get("role") or "").strip()
                    if not company or not role:
                        skipped += 1
                        continue

                    raw_status = (row.get("Status") or row.get("status") or "applied").strip()
                    status = STATUS_REVERSE.get(raw_status.lower(), "applied")

                    try:
                        interest = int(row.get("Interest Score") or row.get("interest_score") or 0)
                        interest = max(0, min(5, interest))
                    except (ValueError, TypeError):
                        interest = 0

                    try:
                        conn.execute(
                            """INSERT INTO jobs
                               (company, role, status, applied_date, location, salary_range,
                                source, job_url, notes, recruiter_name, recruiter_email,
                                recruiter_phone, recruiter_linkedin, follow_up_date, offer_deadline,
                                resume_version, interest_score, next_action, rejection_reason,
                                starred)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                company, role, status,
                                (row.get("Applied Date") or row.get("applied_date") or "").strip() or date.today().isoformat(),
                                (row.get("Location") or row.get("location") or "").strip(),
                                (row.get("Salary Range") or row.get("salary_range") or "").strip(),
                                (row.get("Source") or row.get("source") or "").strip(),
                                (row.get("Job URL") or row.get("job_url") or "").strip(),
                                (row.get("Notes") or row.get("notes") or "").strip(),
                                (row.get("Recruiter Name") or row.get("recruiter_name") or "").strip(),
                                (row.get("Recruiter Email") or row.get("recruiter_email") or "").strip(),
                                (row.get("Recruiter Phone") or row.get("recruiter_phone") or "").strip(),
                                (row.get("Recruiter LinkedIn") or row.get("recruiter_linkedin") or "").strip(),
                                (row.get("Follow Up Date") or row.get("follow_up_date") or "").strip(),
                                (row.get("Offer Deadline") or row.get("offer_deadline") or "").strip(),
                                (row.get("Resume Version") or row.get("resume_version") or "").strip(),
                                interest,
                                (row.get("Next Action") or row.get("next_action") or "").strip(),
                                (row.get("Rejection Reason") or row.get("rejection_reason") or "").strip(),
                                1 if (row.get("Starred") or row.get("starred") or "").strip().lower() == "yes" else 0,
                            )
                        )
                        imported += 1
                    except Exception as e:
                        errors.append(f"Row {i}: {e}")

            msg = f"Imported {imported} job{'s' if imported != 1 else ''}."
            if skipped:
                msg += f" Skipped {skipped} rows (missing company/role)."
            flash(msg, "success")
            if errors:
                for err in errors[:5]:
                    flash(err, "warning")
        except Exception as e:
            flash(f"Failed to parse CSV: {e}", "error")

        return redirect(url_for("index"))

    return render_template("import_csv.html")


# ---------------------------------------------------------------------------
# Backup / Restore
# ---------------------------------------------------------------------------

@app.route("/backup")
def backup_db():
    if not os.path.exists(DB):
        abort(404)
    return send_from_directory(
        os.path.dirname(DB),
        os.path.basename(DB),
        as_attachment=True,
        download_name=f"jobs_backup_{date.today().isoformat()}.db"
    )


@app.route("/restore", methods=["GET", "POST"])
def restore_db():
    if request.method == "POST":
        f = request.files.get("db_file")
        if not f or not f.filename.endswith(".db"):
            flash("Please upload a valid .db file.", "error")
            return redirect(url_for("restore_db"))
        try:
            backup_path = DB + ".bak"
            shutil.copy2(DB, backup_path)
            f.save(DB)
            flash("Database restored successfully. Previous DB saved as jobs.db.bak.", "success")
        except Exception as e:
            flash(f"Restore failed: {e}", "error")
        return redirect(url_for("dashboard"))
    return render_template("restore_db.html")


# ---------------------------------------------------------------------------
# Salary comparison
# ---------------------------------------------------------------------------

@app.route("/salary")
def salary():
    sort = request.args.get("sort", "company")
    order = request.args.get("order", "asc")
    valid_sorts = ["company", "role", "status", "applied_date", "salary_range"]
    if sort not in valid_sorts:
        sort = "company"
    order_sql = "ASC" if order == "asc" else "DESC"

    with get_db() as conn:
        jobs = conn.execute(
            f"""SELECT * FROM jobs
                WHERE salary_range != '' AND salary_range IS NOT NULL
                ORDER BY {sort} {order_sql}"""
        ).fetchall()

        by_status = {}
        for job in jobs:
            by_status.setdefault(job["status"], []).append(job)

        # Group by role (top 10 roles by count with salary info)
        by_role_raw = conn.execute("""
            SELECT role, COUNT(*) as cnt, salary_range
            FROM jobs WHERE salary_range != '' AND salary_range IS NOT NULL
            GROUP BY role ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        by_role = [dict(r) for r in by_role_raw]

        # Group by location (top 10)
        by_location_raw = conn.execute("""
            SELECT location, COUNT(*) as cnt
            FROM jobs WHERE salary_range != '' AND salary_range IS NOT NULL
            AND location != '' AND location IS NOT NULL
            GROUP BY location ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        by_location = [dict(r) for r in by_location_raw]

    return render_template(
        "salary.html",
        jobs=jobs,
        by_status=by_status,
        by_role=by_role,
        by_location=by_location,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        statuses=STATUSES,
        current_sort=sort,
        current_order=order,
    )


# ---------------------------------------------------------------------------
# Offers comparison
# ---------------------------------------------------------------------------

@app.route("/offers")
def offers():
    with get_db() as conn:
        offer_jobs = conn.execute(
            "SELECT * FROM jobs WHERE status='offer' ORDER BY company ASC"
        ).fetchall()

    # Find highest salary (simple string comparison — best effort)
    highest_id = None
    highest_salary = None
    for job in offer_jobs:
        sr = job['salary_range'] or ''
        # Extract first number from salary string
        nums = re.findall(r'\d[\d,]*', sr.replace('k', '000').replace('K', '000'))
        if nums:
            try:
                val = int(nums[0].replace(',', ''))
                if highest_salary is None or val > highest_salary:
                    highest_salary = val
                    highest_id = job['id']
            except ValueError:
                pass

    return render_template(
        "offers.html",
        offer_jobs=offer_jobs,
        highest_id=highest_id,
        status_labels=STATUS_LABELS,
    )


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

EMAIL_TEMPLATES = [
    {
        "title": "Follow-up After Application",
        "subject": "Following Up on [ROLE] Application at [COMPANY]",
        "body": """Hi [RECRUITER_NAME],

I hope this message finds you well. I wanted to follow up on my application for the [ROLE] position at [COMPANY], which I submitted on [DATE].

I remain very excited about this opportunity and believe my background in [YOUR_SKILL/EXPERIENCE] aligns well with what you're looking for. I would love the chance to discuss how I can contribute to your team.

Please let me know if you need any additional information from my side.

Thank you for your time and consideration.

Best regards,
[YOUR_NAME]
[YOUR_EMAIL] | [YOUR_PHONE]
[LINKEDIN_URL]""",
    },
    {
        "title": "Thank You After Phone Screen",
        "subject": "Thank You — [ROLE] Phone Screen",
        "body": """Hi [INTERVIEWER_NAME],

Thank you for taking the time to speak with me today about the [ROLE] position at [COMPANY]. It was great to learn more about the team and the exciting work you're doing with [SPECIFIC_PROJECT_OR_TOPIC_DISCUSSED].

Our conversation reinforced my enthusiasm for this opportunity. I'm particularly excited about [SPECIFIC_ASPECT_OF_ROLE] and believe my experience with [RELEVANT_SKILL] would allow me to contribute quickly.

I look forward to the next steps in the process. Please don't hesitate to reach out if you have any questions.

Thank you again,
[YOUR_NAME]""",
    },
    {
        "title": "Thank You After Technical Interview",
        "subject": "Thank You — [ROLE] Technical Interview",
        "body": """Hi [INTERVIEWER_NAME],

Thank you for the technical interview for the [ROLE] role at [COMPANY]. I really enjoyed the conversation and the problem-solving exercise around [TOPIC_DISCUSSED].

I wanted to share a quick follow-up thought on [SPECIFIC_QUESTION_OR_PROBLEM]: [BRIEF_ADDITIONAL_INSIGHT_OR_ALTERNATIVE_APPROACH].

I remain very interested in this role and am excited about the possibility of joining the team. Please let me know if there's anything further you need from me.

Best,
[YOUR_NAME]""",
    },
    {
        "title": "Thank You After Final Interview",
        "subject": "Thank You — Final Interview for [ROLE] at [COMPANY]",
        "body": """Hi [INTERVIEWER_NAME / HIRING_MANAGER],

Thank you so much for the time you and the team invested in today's final interview for the [ROLE] position. It was a pleasure meeting everyone and getting a deeper understanding of [COMPANY]'s vision and the team's goals.

I left the conversation even more excited about this opportunity. The discussion about [KEY_TOPIC] was especially compelling, and I'm confident my experience in [RELEVANT_AREA] positions me well to make an immediate impact.

I look forward to hearing about the next steps. Thank you again for the consideration.

Warm regards,
[YOUR_NAME]""",
    },
    {
        "title": "Negotiate Offer",
        "subject": "Re: Offer for [ROLE] at [COMPANY]",
        "body": """Hi [RECRUITER_NAME],

Thank you so much for the offer to join [COMPANY] as [ROLE] — I'm genuinely excited about this opportunity and the team.

After careful consideration, I'd like to discuss the compensation package. Based on my research into market rates for this role and my [X years] of experience in [KEY_SKILL], I was hoping we could get closer to [TARGET_SALARY].

I'm very enthusiastic about joining the team and confident we can find a number that works for both sides. Is there any flexibility on the base salary? I'm also open to discussing other elements of the package such as [SIGNING_BONUS / EQUITY / PTO / REMOTE_FLEXIBILITY].

I look forward to your thoughts.

Best,
[YOUR_NAME]""",
    },
    {
        "title": "Decline Offer Politely",
        "subject": "Re: Offer for [ROLE] at [COMPANY]",
        "body": """Hi [RECRUITER_NAME],

Thank you so much for the offer to join [COMPANY] as [ROLE]. I genuinely appreciate the time and effort the team put into the interview process, and I have a lot of respect for the work you're doing.

After considerable thought, I've decided to decline the offer at this time. This was a very difficult decision, as I was impressed by everyone I met. Ultimately, [BRIEF_REASON — e.g., "I've accepted a role that aligns more closely with my long-term career goals" or "the timing isn't right for me"].

I hope our paths will cross again in the future, and I wish you and the team continued success.

Thank you again for this opportunity.

Best regards,
[YOUR_NAME]""",
    },
    {
        "title": "Ask for Feedback After Rejection",
        "subject": "Request for Feedback — [ROLE] at [COMPANY]",
        "body": """Hi [RECRUITER_NAME / INTERVIEWER_NAME],

Thank you for letting me know about your decision regarding the [ROLE] position. While I'm disappointed, I appreciate you taking the time to close the loop.

If you're able to share any feedback on my candidacy — particularly regarding areas where I could improve — I would be very grateful. Constructive feedback is invaluable to me as I continue to grow professionally.

Thank you again for the opportunity. I enjoyed learning about [COMPANY] and wish you and the team all the best.

Best regards,
[YOUR_NAME]""",
    },
]


@app.route("/email-templates")
def email_templates():
    with get_db() as conn:
        jobs = conn.execute(
            """SELECT id, company, role, recruiter_name, recruiter_email,
                      applied_date, status
               FROM jobs ORDER BY applied_date DESC"""
        ).fetchall()
        settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in settings_rows}
    return render_template(
        "email_templates.html",
        templates=EMAIL_TEMPLATES,
        jobs=[dict(j) for j in jobs],
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.route("/stats")
def stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM jobs WHERE source != '' "
            "GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM jobs ORDER BY applied_date DESC LIMIT 5"
        ).fetchall()
        monthly = conn.execute(
            """SELECT substr(applied_date,1,7) as month, COUNT(*) as cnt
               FROM jobs WHERE applied_date != ''
               GROUP BY month ORDER BY month DESC LIMIT 6"""
        ).fetchall()

        # Response rate by source
        source_stats = conn.execute("""
            SELECT source,
                   COUNT(*) as total,
                   SUM(CASE WHEN status NOT IN ('applied','ghosted') THEN 1 ELSE 0 END) as responded,
                   SUM(CASE WHEN status = 'offer' THEN 1 ELSE 0 END) as offers
            FROM jobs WHERE source != '' GROUP BY source ORDER BY total DESC
        """).fetchall()

        # Average days to first response
        avg_row = conn.execute("""
            SELECT AVG(diff) as avg_days FROM (
                SELECT julianday(t.event_date) - julianday(j.applied_date) as diff
                FROM timeline t JOIN jobs j ON t.job_id = j.id
                WHERE t.event NOT IN ('Applied', 'Initial application')
                AND j.applied_date != ''
                AND t.event_date != ''
                AND t.event_date > j.applied_date
                GROUP BY t.job_id
                HAVING MIN(julianday(t.event_date) - julianday(j.applied_date)) >= 0
            )
        """).fetchone()
        avg_to_response = round(avg_row['avg_days'], 1) if avg_row and avg_row['avg_days'] else None

        # Heatmap data
        heatmap_rows = conn.execute("""
            SELECT applied_date, COUNT(*) as cnt
            FROM jobs WHERE applied_date != ''
            GROUP BY applied_date
        """).fetchall()
        heatmap_data = {row['applied_date']: row['cnt'] for row in heatmap_rows}

        # Interview funnel / success rate
        funnel_counts = {}
        for s in STATUSES:
            funnel_counts[s] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (s,)
            ).fetchone()[0]
        reached_interview = (
            funnel_counts.get("phone_interview", 0) +
            funnel_counts.get("technical_interview", 0) +
            funnel_counts.get("final_interview", 0) +
            funnel_counts.get("offer", 0) +
            funnel_counts.get("rejected", 0)
        )
        interview_to_offer = funnel_counts.get("offer", 0)
        interview_rate = round(reached_interview / total * 100, 1) if total else 0
        offer_from_interview = round(
            interview_to_offer / reached_interview * 100, 1
        ) if reached_interview else 0

        # Day-of-week stats
        dow_stats = conn.execute("""
            SELECT CASE strftime('%w', applied_date)
                WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue'
                WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' WHEN '6' THEN 'Sat'
            END as dow,
            strftime('%w', applied_date) as dow_num,
            COUNT(*) as cnt
            FROM jobs WHERE applied_date != ''
            GROUP BY dow_num ORDER BY dow_num
        """).fetchall()

        # Rejection breakdown
        rejection_breakdown = conn.execute("""
            SELECT COALESCE(NULLIF(rejection_reason, ''), 'Not specified') as reason,
                   COUNT(*) as cnt
            FROM jobs WHERE status='rejected'
            GROUP BY rejection_reason
            ORDER BY cnt DESC
        """).fetchall()

        # Weekly velocity: last 12 weeks (Mon-Sun buckets)
        weekly_velocity_raw = conn.execute("""
            SELECT
                strftime('%Y-W%W', applied_date) as week_key,
                date(applied_date, 'weekday 0', '-6 days') as week_start,
                COUNT(*) as cnt
            FROM jobs WHERE applied_date != ''
            GROUP BY week_key
            ORDER BY week_key DESC
            LIMIT 12
        """).fetchall()
        weekly_velocity = list(reversed([
            {"week": r["week_start"] or r["week_key"], "cnt": r["cnt"]}
            for r in weekly_velocity_raw
        ]))

        # Source funnel: per source — applied count, interview count, offer count
        source_funnel_raw = conn.execute("""
            SELECT source,
                   COUNT(*) as applied_cnt,
                   SUM(CASE WHEN status IN ('phone_interview','technical_interview','final_interview','offer') THEN 1 ELSE 0 END) as interview_cnt,
                   SUM(CASE WHEN status = 'offer' THEN 1 ELSE 0 END) as offer_cnt
            FROM jobs WHERE source != '' AND source IS NOT NULL
            GROUP BY source
            ORDER BY applied_cnt DESC
            LIMIT 10
        """).fetchall()
        source_funnel = [dict(r) for r in source_funnel_raw]

        # Response time per source
        resp_per_source_raw = conn.execute("""
            SELECT j.source,
                   AVG(julianday(t.event_date) - julianday(j.applied_date)) as avg_days,
                   COUNT(DISTINCT j.id) as cnt
            FROM jobs j
            JOIN timeline t ON t.job_id = j.id
            WHERE j.source != ''
            AND t.event NOT IN ('Applied', 'Initial application')
            AND j.applied_date != ''
            AND t.event_date > j.applied_date
            GROUP BY j.source
            ORDER BY avg_days ASC
        """).fetchall()
        resp_per_source = [
            {"source": r["source"], "avg_days": round(r["avg_days"], 1) if r["avg_days"] else None, "cnt": r["cnt"]}
            for r in resp_per_source_raw
        ]

    counts = {row["status"]: row["cnt"] for row in by_status}
    conversion = round(counts.get("offer", 0) / total * 100, 1) if total > 0 else 0

    # Generate 52-week grid for heatmap
    today_d = date.today()
    start = today_d - timedelta(days=363)
    # Align to Monday
    start = start - timedelta(days=start.weekday())
    heatmap_weeks = []
    for w in range(52):
        week = []
        for d in range(7):
            day = start + timedelta(days=w * 7 + d)
            week.append(day.isoformat())
        heatmap_weeks.append(week)

    return render_template(
        "stats.html",
        total=total,
        by_status=by_status,
        by_source=by_source,
        recent=recent,
        monthly=monthly,
        counts=counts,
        conversion=conversion,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        source_stats=source_stats,
        avg_to_response=avg_to_response,
        heatmap_data=heatmap_data,
        heatmap_weeks=heatmap_weeks,
        interview_rate=interview_rate,
        offer_from_interview=offer_from_interview,
        reached_interview=reached_interview,
        dow_stats=dow_stats,
        rejection_breakdown=rejection_breakdown,
        weekly_velocity=weekly_velocity,
        source_funnel=source_funnel,
        resp_per_source=resp_per_source,
    )


# ---------------------------------------------------------------------------
# Contact book
# ---------------------------------------------------------------------------

@app.route("/contacts")
def contacts():
    search = request.args.get("search", "")
    with get_db() as conn:
        if search:
            contacts_list = conn.execute(
                """SELECT * FROM contacts WHERE (name LIKE ? OR email LIKE ? OR company LIKE ?)
                   ORDER BY name ASC""",
                (f"%{search}%", f"%{search}%", f"%{search}%")
            ).fetchall()
        else:
            contacts_list = conn.execute(
                "SELECT * FROM contacts ORDER BY name ASC"
            ).fetchall()
    return render_template("contacts.html", contacts=contacts_list, search=search)


@app.route("/contacts/add", methods=["POST"])
def add_contact():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("contacts"))
    with get_db() as conn:
        conn.execute(
            """INSERT INTO contacts (name, email, phone, linkedin, company, title, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (
                name,
                request.form.get("email", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("linkedin", "").strip(),
                request.form.get("company", "").strip(),
                request.form.get("title", "").strip(),
                request.form.get("notes", "").strip(),
            )
        )
    flash(f"Contact '{name}' added.", "success")
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
def delete_contact(contact_id):
    with get_db() as conn:
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
def edit_contact(contact_id):
    with get_db() as conn:
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not contact:
            return redirect(url_for("contacts"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Name is required.", "error")
                return redirect(url_for("edit_contact", contact_id=contact_id))
            conn.execute(
                """UPDATE contacts SET name=?, email=?, phone=?, linkedin=?, company=?, title=?, notes=?
                   WHERE id=?""",
                (
                    name,
                    request.form.get("email", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("linkedin", "").strip(),
                    request.form.get("company", "").strip(),
                    request.form.get("title", "").strip(),
                    request.form.get("notes", "").strip(),
                    contact_id,
                )
            )
            flash("Contact updated.", "success")
            return redirect(url_for("contacts"))
    return render_template("edit_contact.html", contact=contact)


init_db()

if __name__ == "__main__":
    if getattr(sys, 'frozen', False) or os.environ.get("AUTO_OPEN_BROWSER", "0") == "1":
        def _open_browser():
            time.sleep(1.2)
            webbrowser.open("http://localhost:5050")
        threading.Thread(target=_open_browser, daemon=True).start()

    is_frozen = getattr(sys, 'frozen', False)
    app.run(debug=not is_frozen, port=5050, threaded=True)
