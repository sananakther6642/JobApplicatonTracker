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
    )


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
                    follow_up_date, offer_deadline, resume_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                   created_at, updated_at
            FROM jobs ORDER BY applied_date DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Role", "Status", "Applied Date", "Location", "Salary Range",
        "Source", "Job URL", "Notes", "Recruiter Name", "Recruiter Email",
        "Recruiter LinkedIn", "Follow Up Date", "Offer Deadline", "Resume Version",
        "Starred", "Created At", "Updated At",
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

    counts = {row["status"]: row["cnt"] for row in by_status}
    conversion = round(counts.get("offer", 0) / total * 100, 1) if total > 0 else 0

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
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5050)
