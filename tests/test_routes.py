"""Integration tests for Flask routes using test client."""
import pytest


class TestDashboard:
    def test_get_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_dashboard_heading(self, client):
        resp = client.get("/")
        assert b"Dashboard" in resp.data or b"JAT" in resp.data


class TestAllJobs:
    def test_get_200(self, client):
        resp = client.get("/applications")
        assert resp.status_code == 200

    def test_empty_state(self, client):
        resp = client.get("/applications")
        assert b"No applications found" in resp.data or resp.status_code == 200

    def test_search_param_accepted(self, client):
        resp = client.get("/applications?search=google")
        assert resp.status_code == 200

    def test_status_filter(self, client):
        resp = client.get("/applications?status=applied")
        assert resp.status_code == 200

    def test_shows_job(self, client, sample_job):
        resp = client.get("/applications")
        assert b"Acme Corp" in resp.data

    def test_sort_options(self, client):
        for sort in ("applied_date", "company", "status"):
            resp = client.get(f"/applications?sort={sort}")
            assert resp.status_code == 200


class TestAddJob:
    def test_get_form_200(self, client):
        resp = client.get("/add")
        assert resp.status_code == 200

    def test_post_creates_job(self, client, db):
        before = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        resp = client.post("/add", data={
            "company": "New Corp",
            "role": "SWE",
            "status": "applied",
            "applied_date": "2026-07-01",
            "source": "LinkedIn",
            "location": "Remote",
            "salary_range": "",
            "notes": "",
            "tags": "",
        }, follow_redirects=True)
        after = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert resp.status_code == 200
        assert after == before + 1

    def test_post_missing_company_400(self, client):
        # Flask raises 400 when required form field absent
        resp = client.post("/add", data={"role": "SWE"}, follow_redirects=False)
        assert resp.status_code == 400

    def test_post_missing_role_400(self, client):
        resp = client.post("/add", data={"company": "X"}, follow_redirects=False)
        assert resp.status_code == 400


class TestEditJob:
    def test_get_edit_form(self, client, sample_job):
        resp = client.get(f"/job/{sample_job['id']}/edit")
        assert resp.status_code == 200
        assert b"Acme Corp" in resp.data

    def test_edit_nonexistent_redirects(self, client):
        # App redirects to /applications when job not found
        resp = client.get("/job/99999/edit")
        assert resp.status_code in (302, 404)

    def test_post_updates_job(self, client, sample_job, db):
        resp = client.post(f"/job/{sample_job['id']}/edit", data={
            "company": "Updated Corp",
            "role": "Senior SWE",
            "status": "screening",
            "applied_date": "2026-07-01",
            "source": "LinkedIn",
            "location": "NYC",
            "salary_range": "$130k",
            "notes": "",
            "tags": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        updated = db.execute("SELECT * FROM jobs WHERE id=?", (sample_job["id"],)).fetchone()
        assert updated["company"] == "Updated Corp"
        assert updated["status"] == "screening"


class TestDeleteJob:
    def test_delete_removes_job(self, client, sample_job, db):
        job_id = sample_job["id"]
        resp = client.post(f"/job/{job_id}/delete", follow_redirects=True)
        assert resp.status_code == 200
        gone = db.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert gone is None

    def test_delete_nonexistent_404(self, client):
        resp = client.post("/job/99999/delete")
        assert resp.status_code == 404


class TestJobDetail:
    def test_get_200(self, client, sample_job):
        resp = client.get(f"/job/{sample_job['id']}")
        assert resp.status_code == 200
        assert b"Acme Corp" in resp.data

    def test_nonexistent_redirects(self, client):
        resp = client.get("/job/99999")
        assert resp.status_code in (302, 404)


class TestQuickStatus:
    def test_changes_status(self, client, sample_job, db):
        resp = client.post(
            f"/job/{sample_job['id']}/quick-status",
            data={"new_status": "screening"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["status"] == "screening"

    def test_invalid_status_rejected(self, client, sample_job):
        resp = client.post(
            f"/job/{sample_job['id']}/quick-status",
            data={"new_status": "not_a_real_status"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # App returns JSON with ok:False or a non-200 status
        if resp.content_type and "json" in resp.content_type:
            data = resp.get_json()
            assert data["ok"] is False
        else:
            assert resp.status_code in (400, 422)

    def test_nonexistent_job_404(self, client):
        resp = client.post("/job/99999/quick-status", data={"new_status": "applied"})
        assert resp.status_code == 404


class TestStarJob:
    def test_star_toggles(self, client, sample_job, db):
        job_id = sample_job["id"]
        # Initially unstarred
        resp = client.post(f"/job/{job_id}/star", data={"ajax": "1"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["starred"] is True

        # Toggle off
        resp2 = client.post(f"/job/{job_id}/star", data={"ajax": "1"})
        data2 = resp2.get_json()
        assert data2["starred"] is False


class TestStats:
    def test_get_200(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_stats_with_data(self, client, sample_job):
        resp = client.get("/stats")
        assert resp.status_code == 200
        assert b"Stats" in resp.data


class TestExportCSV:
    def test_returns_csv(self, client, sample_job):
        resp = client.get("/export/csv")
        assert resp.status_code == 200
        assert b"company" in resp.data.lower() or b"Acme Corp" in resp.data
        assert resp.content_type.startswith("text/csv") or "csv" in resp.headers.get("Content-Disposition", "")


class TestImportCSV:
    def test_get_200(self, client):
        resp = client.get("/import/csv")
        assert resp.status_code == 200


class TestSalaryPage:
    def test_get_200(self, client):
        resp = client.get("/salary")
        assert resp.status_code == 200


class TestOffersPage:
    def test_get_200(self, client):
        resp = client.get("/offers")
        assert resp.status_code == 200


class TestContactsPage:
    def test_get_200(self, client):
        resp = client.get("/contacts")
        assert resp.status_code == 200

    def test_add_contact(self, client, db):
        before = db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        resp = client.post("/contacts/add", data={
            "name": "Jane Doe",
            "email": "jane@example.com",
            "linkedin": "",
            "company": "Acme",
            "title": "Recruiter",
            "notes": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        after = db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        assert after == before + 1


class TestBulkUpdate:
    def test_bulk_status_update(self, client, sample_job, db):
        job_id = sample_job["id"]
        resp = client.post("/bulk-update", data={
            "job_ids": str(job_id),
            "new_status": "screening",
            "action": "status",
        }, follow_redirects=True)
        assert resp.status_code == 200
        updated = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert updated["status"] == "screening"


class TestWeeklyAndMonthlyGoals:
    def test_set_and_clear_goals(self, client, db):
        # Set weekly goal to 5
        resp = client.post("/settings", data={"weekly_goal": "5"}, follow_redirects=True)
        assert resp.status_code == 200
        val = db.execute("SELECT value FROM settings WHERE key='weekly_goal'").fetchone()
        assert val is not None and val["value"] == "5"

        # Dashboard shows weekly goal 5
        resp_dash = client.get("/")
        assert b"5" in resp_dash.data

        # Clear weekly goal (submit empty string)
        resp_clear = client.post("/settings", data={"weekly_goal": ""}, follow_redirects=True)
        assert resp_clear.status_code == 200
        val_cleared = db.execute("SELECT value FROM settings WHERE key='weekly_goal'").fetchone()
        assert val_cleared is None


class TestNewFeatureUpgrades:
    def test_export_report_route(self, client, sample_job):
        resp = client.get("/export-report")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/plain")
        assert b"JOB APPLICATION TRACKER (JAT) REPORT" in resp.data
        assert sample_job["company"].encode() in resp.data

    def test_generate_get_query_param(self, client):
        resp = client.get("/generate?jd_text=Senior+Python+Developer+Role")
        assert resp.status_code == 200
        assert b"Senior Python Developer Role" in resp.data

    def test_generate_bookmarklet_autostart(self, client):
        resp = client.get("/generate?jd_text=Senior+Dev+at+Google&autostart=1")
        assert resp.status_code == 200
        assert b"name=\"autostart\" value=\"1\"" in resp.data
        assert b"1-Click Quick Track" in resp.data


