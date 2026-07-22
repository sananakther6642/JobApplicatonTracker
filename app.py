import sqlite3
import os
import re
import csv
import io
from flask import (Flask, render_template, request, redirect, url_for,
                   send_from_directory, abort, flash, make_response)
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
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["POST"])
def save_settings():
    weekly_goal = request.form.get("weekly_goal", "").strip()
    if weekly_goal:
        try:
            weekly_goal = str(int(weekly_goal))
            with get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_goal', ?)",
                    (weekly_goal,)
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

    query = "SELECT DISTINCT jobs.* FROM jobs"
    params = []

    if tag_filter:
        query += " JOIN tags ON tags.job_id = jobs.id"

    query += " WHERE 1=1"

    if status_filter:
        query += " AND jobs.status = ?"
        params.append(status_filter)

    if starred_only:
        query += " AND jobs.starred = 1"

    if search:
        query += " AND (jobs.company LIKE ? OR jobs.role LIKE ? OR jobs.notes LIKE ? OR jobs.jd LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])

    if tag_filter:
        query += " AND tags.name = ?"
        params.append(tag_filter.lower())

    valid_sorts = ["applied_date", "company", "role", "status", "created_at"]
    if sort not in valid_sorts:
        sort = "applied_date"
    order_sql = "DESC" if order == "desc" else "ASC"
    query += f" ORDER BY jobs.{sort} {order_sql}"

    with get_db() as conn:
        jobs = conn.execute(query, params).fetchall()
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
    )


# ---------------------------------------------------------------------------
# Add / Edit job
# ---------------------------------------------------------------------------

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
                    int(request.form.get("interest_score") or 0),
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
                   interest_score=?, next_action=?,
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
                    int(request.form.get("interest_score") or 0),
                    request.form.get("next_action", ""),
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
# Star / Unstar
# ---------------------------------------------------------------------------

@app.route("/job/<int:job_id>/star", methods=["POST"])
def star_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT starred FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job:
            new_val = 0 if job["starred"] else 1
            conn.execute("UPDATE jobs SET starred=? WHERE id=?", (new_val, job_id))
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
                   interest_score, next_action,
                   created_at, updated_at
            FROM jobs ORDER BY applied_date DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Role", "Status", "Applied Date", "Location", "Salary Range",
        "Source", "Job URL", "Notes", "Recruiter Name", "Recruiter Email",
        "Recruiter LinkedIn", "Follow Up Date", "Offer Deadline", "Resume Version",
        "Starred", "Interest Score", "Next Action", "Created At", "Updated At",
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
            job["created_at"] or "", job["updated_at"] or "",
        ])

    response = make_response(output.getvalue())
    fname = f"jobs_export_{date.today().isoformat()}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={fname}"
    response.headers["Content-Type"] = "text/csv"
    return response


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
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
