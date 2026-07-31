"""Unit tests for pure helper functions — no HTTP, no DB."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import slugify, save_tags, get_tags_for_jobs


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello_world"

    def test_special_chars_stripped(self):
        assert slugify("C++ Developer!") == "c_developer"

    def test_spaces_become_underscores(self):
        assert slugify("  foo   bar  ") == "foo_bar"

    def test_max_length(self):
        result = slugify("a" * 100)
        assert len(result) <= 40

    def test_empty_string(self):
        assert slugify("") == ""

    def test_hyphens_normalised(self):
        # slugify collapses hyphens/spaces to underscores
        assert slugify("full-stack--developer") == "full_stack_developer"

    def test_unicode_letters_kept(self):
        result = slugify("José García")
        assert len(result) > 0


class TestSaveTags:
    def test_inserts_tags(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        save_tags(db, job_id, "python, flask, sqlite")
        db.commit()
        tags = db.execute("SELECT name FROM tags WHERE job_id=? ORDER BY name", (job_id,)).fetchall()
        names = [t["name"] for t in tags]
        assert names == ["flask", "python", "sqlite"]

    def test_replaces_existing_tags(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        save_tags(db, job_id, "old, tags")
        db.commit()
        save_tags(db, job_id, "new")
        db.commit()
        tags = db.execute("SELECT name FROM tags WHERE job_id=?", (job_id,)).fetchall()
        assert len(tags) == 1
        assert tags[0]["name"] == "new"

    def test_empty_string_clears_tags(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        save_tags(db, job_id, "python")
        db.commit()
        save_tags(db, job_id, "")
        db.commit()
        tags = db.execute("SELECT name FROM tags WHERE job_id=?", (job_id,)).fetchall()
        assert tags == []

    def test_tags_lowercased(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        save_tags(db, job_id, "Python, FLASK")
        db.commit()
        tags = [t["name"] for t in db.execute("SELECT name FROM tags WHERE job_id=?", (job_id,)).fetchall()]
        assert "python" in tags
        assert "flask" in tags

    def test_whitespace_only_tags_ignored(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        save_tags(db, job_id, "  ,  ,  ")
        db.commit()
        tags = db.execute("SELECT name FROM tags WHERE job_id=?", (job_id,)).fetchall()
        assert tags == []


class TestGetTagsForJobs:
    def test_returns_dict_keyed_by_job_id(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('A','B')")
        db.execute("INSERT INTO jobs (company, role) VALUES ('C','D')")
        db.commit()
        ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 2").fetchall()]
        save_tags(db, ids[0], "alpha")
        save_tags(db, ids[1], "beta, gamma")
        db.commit()
        result = get_tags_for_jobs(db, ids)
        assert "alpha" in result[ids[0]]
        assert set(result[ids[1]]) == {"beta", "gamma"}

    def test_empty_list_returns_empty_dict(self, db):
        assert get_tags_for_jobs(db, []) == {}

    def test_job_with_no_tags_not_in_result(self, db):
        db.execute("INSERT INTO jobs (company, role) VALUES ('X','Y')")
        db.commit()
        job_id = db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        result = get_tags_for_jobs(db, [job_id])
        assert result == {}


class TestRecruiterExtraction:
    def test_applicant_info_not_assigned_to_recruiter(self):
        from gen_job import generate
        jd_text = """
        Company: Acme Corp
        Role: Software Engineer
        Location: Berlin
        We are hiring a software engineer.
        """
        cover_text = """
        Dear Hiring Manager,
        My name is John Applicant. You can contact me at john.applicant@example.com or +49 123 456789.
        Sincerely,
        John Applicant
        """
        job = generate(jd_text, cv_text="John Applicant CV john.applicant@example.com", cover_text=cover_text)
        assert job["recruiter_name"] == ""
        assert job["recruiter_email"] == ""
        assert job["recruiter_phone"] == ""

    def test_valid_recruiter_extracted_from_jd(self):
        from gen_job import generate
        jd_text = """
        Company: TechGmbH
        Role: Python Developer
        Contact Person: Sarah Smith
        Email: jobs@techgmbh.de
        Telefon: +49 89 123456
        """
        cover_text = "My email: applicant@me.com"
        job = generate(jd_text, cv_text="applicant@me.com", cover_text=cover_text)
        assert job["recruiter_name"] == "Sarah Smith"
        assert job["recruiter_email"] == "jobs@techgmbh.de"
        assert "+49 89 123456" in job["recruiter_phone"]
