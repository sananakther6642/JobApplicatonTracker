import sqlite3
import os
import re
import csv
import io
from flask import (Flask, render_template, request, redirect, url_for,
                   send_from_directory, abort, flash, make_response, jsonify)
from werkzeug.utils import secure_filename
from datetime import date, timedelta

app = Flask(__name__)
app.secret_key = "job-tracker-secret-key-2024"
DB = os.path.join(os.path.dirname(__file__), "jobs.db")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
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
            ("follow_up_date",     "TEXT DEFAULT ''"),
            ("offer_deadline",     "TEXT DEFAULT ''"),
            ("starred",            "INTEGER DEFAULT 0"),
            ("resume_version",     "TEXT DEFAULT ''"),
            ("interest_score",     "INTEGER DEFAULT 0"),
            ("next_action",        "TEXT DEFAULT ''"),
            ("rejection_reason",   "TEXT DEFAULT ''"),
        ]
        for col, typedef in new_cols:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # Column already exists


STATUSES = [
    "applied", "screening", "phone_interview",
    "technical_interview", "final_interview",
    "offer", "rejected", "withdrawn", "ghosted",
]

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


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]


def make_doc_filename(company, role, doc_type, ext, job_id):
    base = f"{slugify(company)}_{slugify(role)}_{doc_type}"
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

        # Follow-ups: explicit follow_up_date <= today  OR  applied 7+ days ago with no follow_up_date
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

        # Deadlines: offer_deadline within next 7 days
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
            SELECT job_id, MIN(interview_date) as next_date
            FROM interview_rounds
            WHERE interview_date >= date('now')
            GROUP BY job_id
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

        # Weekly goal
        monday = date.today() - timedelta(days=date.today().weekday())
        this_week_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE applied_date >= ?",
            (monday.isoformat(),)
        ).fetchone()[0]

        goal_row = conn.execute(
            "SELECT value FROM settings WHERE key='weekly_goal'"
        ).fetchone()
        weekly_goal = int(goal_row['value']) if goal_row and goal_row['value'] else None

        monthly_goal_row = conn.execute(
            "SELECT value FROM settings WHERE key='monthly_goal'"
        ).fetchone()
        monthly_goal = int(monthly_goal_row['value']) if monthly_goal_row and monthly_goal_row['value'] else None

        this_month_start = date.today().replace(day=1).isoformat()
        this_month_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE applied_date >= ?",
            (this_month_start,)
        ).fetchone()[0]

    offer_rate = round(counts.get("offer", 0) / total * 100, 1) if total > 0 else 0
    response_rate = round(
        (total - counts.get("applied", 0) - counts.get("ghosted", 0)) / total * 100, 1
    ) if total > 0 else 0

    return render_template(
        "dashboard.html",
        total=total,
        counts=counts,
        kanban=kanban,
        kanban_statuses=kanban_statuses,
        activity=activity,
        followups=followups,
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
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["POST"])
def save_settings():
    with get_db() as conn:
        for key in ("weekly_goal", "monthly_goal"):
            val = request.form.get(key, "").strip()
            if val:
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (key, str(int(val)))
                    )
                except (ValueError, TypeError):
                    pass
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
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1
    per_page = 20

    join_clause = ""
    where_clause = "WHERE 1=1"
    params = []

    if tag_filter:
        join_clause = " JOIN tags ON tags.job_id = jobs.id"

    if status_filter:
        where_clause += " AND jobs.status = ?"
        params.append(status_filter)

    if starred_only:
        where_clause += " AND jobs.starred = 1"

    if search:
        where_clause += " AND (jobs.company LIKE ? OR jobs.role LIKE ? OR jobs.notes LIKE ? OR jobs.jd LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])

    if tag_filter:
        where_clause += " AND tags.name = ?"
        params.append(tag_filter.lower())

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
                "SELECT COUNT(*) FROM jobs WHERE status=?", (s,)
            ).fetchone()[0]
        counts["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        counts["starred"] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE starred=1"
        ).fetchone()[0]

        job_ids = [j["id"] for j in jobs]
        tags_by_job = get_tags_for_jobs(conn, job_ids)

        all_tags = conn.execute(
            "SELECT DISTINCT name FROM tags ORDER BY name"
        ).fetchall()

    # Compute days ago for each job
    today = date.today()
    days_ago = {}
    for job in jobs:
        if job['applied_date']:
            try:
                d = date.fromisoformat(job['applied_date'])
                days_ago[job['id']] = (today - d).days
            except Exception:
                pass

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
        tags_by_job=tags_by_job,
        all_tags=all_tags,
        days_ago=days_ago,
        today=today.isoformat(),
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
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
                    recruiter_name, recruiter_email, recruiter_linkedin,
                    follow_up_date, offer_deadline, resume_version,
                    interest_score, next_action)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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


def _save_uploads(files, doc_types, job_id, company, role):
    with get_db() as conn:
        for i, f in enumerate(files):
            if not f or not f.filename:
                continue
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "pdf"
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
                   recruiter_name=?, recruiter_email=?, recruiter_linkedin=?,
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
            if old_status != new_status:
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

@app.route("/job/<int:job_id>/add_event", methods=["POST"])
def add_event(job_id):
    with get_db() as conn:
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


# ---------------------------------------------------------------------------
# Delete job
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    with get_db() as conn:
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
    with get_db() as conn:
        old = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if old and old["status"] != new_status:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                (new_status, job_id)
            )
            conn.execute(
                "INSERT INTO timeline (job_id, event, event_date) VALUES (?,?,date('now'))",
                (job_id, STATUS_LABELS.get(new_status, new_status))
            )
    return jsonify({"ok": True, "status": new_status, "label": STATUS_LABELS.get(new_status, new_status)})


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
                              recruiter_linkedin, resume_version, interest_score, next_action)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job["company"], job["role"], job["jd"], job["job_url"],
             date.today().isoformat(), "applied", job["source"],
             job["salary_range"], job["location"], job["notes"],
             job["recruiter_name"], job["recruiter_email"], job["recruiter_linkedin"],
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
# Bulk status update
# ---------------------------------------------------------------------------

@app.route("/bulk-update", methods=["POST"])
def bulk_update():
    job_ids = request.form.getlist("job_ids")
    new_status = request.form.get("new_status", "")
    if job_ids and new_status and new_status in STATUSES:
        with get_db() as conn:
            for jid in job_ids:
                try:
                    jid = int(jid)
                    old = conn.execute(
                        "SELECT status FROM jobs WHERE id=?", (jid,)
                    ).fetchone()
                    if old and old["status"] != new_status:
                        conn.execute(
                            "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                            (new_status, jid),
                        )
                        conn.execute(
                            "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                            (jid, STATUS_LABELS.get(new_status, new_status),
                             date.today().isoformat(), "Bulk status update"),
                        )
                except (ValueError, TypeError):
                    pass
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@app.route("/export/csv")
def export_csv():
    with get_db() as conn:
        jobs = conn.execute("""
            SELECT id, company, role, status, applied_date, location, salary_range, source,
                   job_url, notes, recruiter_name, recruiter_email, recruiter_linkedin,
                   follow_up_date, offer_deadline, resume_version, starred,
                   interest_score, next_action, rejection_reason,
                   created_at, updated_at
            FROM jobs ORDER BY applied_date DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Role", "Status", "Applied Date", "Location", "Salary Range",
        "Source", "Job URL", "Notes", "Recruiter Name", "Recruiter Email",
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
            job["recruiter_name"] or "", job["recruiter_email"] or "",
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
                                recruiter_linkedin, follow_up_date, offer_deadline,
                                resume_version, interest_score, next_action, rejection_reason)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                                (row.get("Recruiter LinkedIn") or row.get("recruiter_linkedin") or "").strip(),
                                (row.get("Follow Up Date") or row.get("follow_up_date") or "").strip(),
                                (row.get("Offer Deadline") or row.get("offer_deadline") or "").strip(),
                                (row.get("Resume Version") or row.get("resume_version") or "").strip(),
                                interest,
                                (row.get("Next Action") or row.get("next_action") or "").strip(),
                                (row.get("Rejection Reason") or row.get("rejection_reason") or "").strip(),
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
            import shutil
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

    return render_template(
        "salary.html",
        jobs=jobs,
        by_status=by_status,
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
    return render_template("email_templates.html", templates=EMAIL_TEMPLATES)


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
                """SELECT * FROM contacts WHERE name LIKE ? OR email LIKE ? OR company LIKE ?
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
            """INSERT INTO contacts (name, email, linkedin, company, title, notes)
               VALUES (?,?,?,?,?,?)""",
            (
                name,
                request.form.get("email", "").strip(),
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
                """UPDATE contacts SET name=?, email=?, linkedin=?, company=?, title=?, notes=?
                   WHERE id=?""",
                (
                    name,
                    request.form.get("email", "").strip(),
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
    app.run(debug=True, port=5050)
