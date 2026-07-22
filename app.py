import sqlite3
import os
import re
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, abort
from werkzeug.utils import secure_filename
from datetime import date

app = Flask(__name__)
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
        """)


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


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]


def make_doc_filename(company, role, doc_type, ext, job_id):
    """
    Pattern: {company}_{role}_{doc_type}.ext
    If same company has multiple roles: role disambiguates.
    If exact same company+role+type collision (rare): append _v2, _v3…
    """
    base = f"{slugify(company)}_{slugify(role)}_{doc_type}"
    candidate = f"{base}.{ext}"
    path = os.path.join(UPLOAD_DIR, candidate)
    if not os.path.exists(path):
        return candidate
    # versioned fallback
    for v in range(2, 100):
        candidate = f"{base}_v{v}.{ext}"
        path = os.path.join(UPLOAD_DIR, candidate)
        if not os.path.exists(path):
            return candidate
    return f"{base}_{job_id}.{ext}"


@app.route("/")
def dashboard():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        counts = {}
        for s in STATUSES:
            counts[s] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (s,)
            ).fetchone()[0]

        # Kanban: jobs per status column (active ones)
        kanban_statuses = ["applied", "screening", "phone_interview", "technical_interview", "final_interview", "offer"]
        kanban = {}
        for s in kanban_statuses:
            kanban[s] = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY applied_date DESC", (s,)
            ).fetchall()

        # Recent activity (last 10 timeline events with job info)
        activity = conn.execute("""
            SELECT t.*, j.company, j.role, j.id as job_id
            FROM timeline t JOIN jobs j ON t.job_id = j.id
            ORDER BY t.created_at DESC LIMIT 10
        """).fetchall()

        # Upcoming follow-ups: applied > 7 days ago, still in applied/screening
        followups = conn.execute("""
            SELECT * FROM jobs
            WHERE status IN ('applied','screening','ghosted')
            AND applied_date <= date('now', '-7 days')
            ORDER BY applied_date ASC
        """).fetchall()

        # Monthly applied last 6 months
        monthly = conn.execute("""
            SELECT substr(applied_date,1,7) as month, COUNT(*) as cnt
            FROM jobs WHERE applied_date != ''
            GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall()

        # Pipeline funnel data
        funnel = [
            (STATUS_LABELS[s], counts.get(s, 0), STATUS_COLORS[s])
            for s in ["applied","screening","phone_interview","technical_interview","final_interview","offer"]
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
        monthly=monthly,
        funnel=funnel,
        offer_rate=offer_rate,
        response_rate=response_rate,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
    )


@app.route("/applications")
def index():
    status_filter = request.args.get("status", "")
    search = request.args.get("search", "")
    sort = request.args.get("sort", "applied_date")
    order = request.args.get("order", "desc")

    query = "SELECT * FROM jobs WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    if search:
        query += " AND (company LIKE ? OR role LIKE ? OR notes LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    valid_sorts = ["applied_date", "company", "role", "status", "created_at"]
    if sort not in valid_sorts:
        sort = "applied_date"
    order_sql = "DESC" if order == "desc" else "ASC"
    query += f" ORDER BY {sort} {order_sql}"

    with get_db() as conn:
        jobs = conn.execute(query, params).fetchall()
        counts = {}
        for s in STATUSES:
            counts[s] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (s,)
            ).fetchone()[0]
        counts["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

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
    )


@app.route("/add", methods=["GET", "POST"])
def add_job():
    if request.method == "POST":
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO jobs
                   (company, role, jd, job_url, applied_date, status, source,
                    salary_range, location, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    request.form["company"],
                    request.form["role"],
                    request.form.get("jd", ""),
                    request.form.get("job_url", ""),
                    request.form.get("applied_date") or date.today().isoformat(),
                    request.form.get("status", "applied"),
                    request.form.get("source", ""),
                    request.form.get("salary_range", ""),
                    request.form.get("location", ""),
                    request.form.get("notes", ""),
                ),
            )
            job_id = cur.lastrowid
            conn.execute(
                "INSERT INTO timeline (job_id, event, event_date, notes) VALUES (?,?,?,?)",
                (job_id, "Applied", request.form.get("applied_date") or date.today().isoformat(), "Initial application"),
            )

        # Handle file uploads
        files = request.files.getlist("documents")
        doc_types = request.form.getlist("doc_types")
        _save_uploads(files, doc_types, job_id,
                      request.form["company"], request.form["role"])

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
    return render_template(
        "job_detail.html",
        job=job,
        timeline=timeline,
        documents=documents,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        status_colors=STATUS_COLORS,
        doc_types=DOC_TYPES,
        doc_type_labels=DOC_TYPE_LABELS,
    )


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


@app.route("/job/<int:job_id>/edit", methods=["GET", "POST"])
def edit_job(job_id):
    with get_db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return redirect(url_for("index"))

        if request.method == "POST":
            old_status = job["status"]
            new_status = request.form.get("status", old_status)
            conn.execute(
                """UPDATE jobs SET company=?, role=?, jd=?, job_url=?, applied_date=?,
                   status=?, source=?, salary_range=?, location=?, notes=?,
                   updated_at=datetime('now') WHERE id=?""",
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
            return redirect(url_for("job_detail", job_id=job_id))

    return render_template(
        "edit_job.html",
        job=job,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        today=date.today().isoformat(),
    )


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


@app.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    with get_db() as conn:
        # delete uploaded files first
        docs = conn.execute("SELECT filename FROM documents WHERE job_id=?", (job_id,)).fetchall()
        for doc in docs:
            path = os.path.join(UPLOAD_DIR, doc["filename"])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return redirect(url_for("index"))


@app.route("/stats")
def stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM jobs WHERE source != '' GROUP BY source ORDER BY cnt DESC"
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
