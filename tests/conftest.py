import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
from app import app as flask_app, init_db, get_db


@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / "test_jobs.db")
    flask_app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
    )
    old_db = app_module.DB
    app_module.DB = db_path

    with flask_app.app_context():
        init_db()

    yield flask_app

    app_module.DB = old_db


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        yield conn
        conn.close()


@pytest.fixture
def sample_job(db):
    db.execute(
        """INSERT INTO jobs (company, role, status, applied_date, source, location, salary_range)
           VALUES (?,?,?,?,?,?,?)""",
        ("Acme Corp", "Backend Engineer", "applied", "2026-07-01", "LinkedIn",
         "Remote", "$120k-$150k"),
    )
    db.commit()
    return db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
