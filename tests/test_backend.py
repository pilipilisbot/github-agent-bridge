from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from github_agent_bridge import __version__
from github_agent_bridge import feedback
from github_agent_bridge.backend import DashboardConfig, _encode_session, _is_admin, _is_allowed, _session_stream_events, _sign, create_app
from github_agent_bridge.dashboard_data import get_job_detail, job_session, job_session_events, job_session_transcript, list_job_actors, list_jobs, metrics_summary
from github_agent_bridge.monitor import MonitorReport
from github_agent_bridge.models import GitHubContext, Notification
from github_agent_bridge.mcp import create_token
from github_agent_bridge.observability import record_monitor_observation
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


@pytest.fixture(autouse=True)
def no_context_actor_lookup(monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)


def notif(uid=1, mid="<1@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10", from_addr="GitHub <notifications@github.com>"):
    return Notification(
        uid=uid,
        message_id=mid,
        subject="Re: [gisce/erp] thing (PR #1)",
        from_addr=from_addr,
        body=body,
        auth={"spf": True, "dkim": True, "dmarc": True},
    )


def test_dashboard_status_is_read_only_and_lists_recent_jobs(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    event = feedback.pending_events(db)[0]
    feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": event["scope"],
            "type": "operating_rule",
            "rule": "Keep knowledge moderation visible.",
            "confidence": 0.72,
            "reason": "The rule needs human moderation.",
        },
        auto_approve_confidence=0.95,
        model="gpt-test",
    )
    app = create_app(DashboardConfig(db=db, require_auth=False))

    client = TestClient(app)
    response = client.get("/api/status")
    jobs = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.json()["read_only"] is False
    assert response.json()["dashboard_url"] == "http://testserver"
    assert response.json()["admin_actions"] == [
        "retry_job",
        "dismiss_job",
        "approve_knowledge_proposal",
        "reject_knowledge_proposal",
        "update_knowledge_rule_scope",
        "delete_knowledge_rule",
        "create_mcp_token",
        "revoke_mcp_token",
        "view_autoupdate_plan",
        "refresh_autoupdate_plan",
        "apply_autoupdate",
        "complete_autoupdate_reload",
    ]
    assert response.json()["autoupdate"] == {}
    assert response.json()["metrics"]["pending"] == 1
    assert response.json()["metrics"]["knowledge"]["proposed"] == 1
    assert jobs.json()["jobs"][0]["work_key"] == "gisce/erp#1"
    assert jobs.json()["jobs"][0]["trigger_actor"] is None
    assert jobs.json()["jobs"][0]["trigger_actor_avatar_url"] is None
    assert jobs.json()["jobs"][0]["model_route"] == {
        "configured": False,
        "model": None,
        "thinking": None,
        "summary": "n/a",
    }


def test_dashboard_status_reports_configured_public_url(tmp_path):
    app = create_app(DashboardConfig(db=tmp_path / "bridge.sqlite3", require_auth=False, public_url="https://bridge.example.com/"))
    client = TestClient(app)

    response = client.get("/api/status", headers={"host": "127.0.0.1:8765"})

    assert response.status_code == 200
    assert response.json()["dashboard_url"] == "https://bridge.example.com"


def test_dashboard_status_reports_forwarded_public_url(tmp_path):
    app = create_app(DashboardConfig(db=tmp_path / "bridge.sqlite3", require_auth=False))
    client = TestClient(app)

    response = client.get(
        "/api/status",
        headers={
            "host": "127.0.0.1:8765",
            "x-forwarded-proto": "https",
            "x-forwarded-host": "bridge.example.com",
            "x-forwarded-prefix": "/ops",
        },
    )

    assert response.status_code == 200
    assert response.json()["dashboard_url"] == "https://bridge.example.com/ops"


def test_dashboard_autoupdate_state_requires_admin_profile(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.set_state("autoupdate", json.dumps({"decision": "stage_full_reload", "target": {"tag_name": "v0.28.0"}}))
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"admin"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    reader = client.get("/api/status").json()
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Admin"}, is_admin=True)))
    admin = client.get("/api/status").json()

    assert reader["autoupdate"] == {}
    assert "view_autoupdate_plan" not in reader["admin_actions"]
    assert admin["autoupdate"]["target"]["tag_name"] == "v0.28.0"
    assert "view_autoupdate_plan" in admin["admin_actions"]


def test_dashboard_autoupdate_refresh_requires_admin_and_records_plan(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    plan = {
        "installed_version": "0.27.0",
        "installed_tag": "v0.27.0",
        "target": {"tag_name": "v0.28.0"},
        "decision": "stage_full_reload",
        "executor_reload_pending": True,
        "blocked_reason": "",
        "queue": {"active_counts": {}, "active_total": 0},
        "classification": {"risk": "executor_or_shared", "migration_files": [], "risky_files": [], "systemd_files": []},
        "service_plan": {"immediate": [], "deferred": []},
        "warnings": [],
    }
    monkeypatch.setattr("github_agent_bridge.backend._dashboard_autoupdate_plan", lambda db: plan)

    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))
    forbidden = client.post("/api/autoupdate/refresh")
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    response = client.post("/api/autoupdate/refresh")

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.json()["state"]["target"]["tag_name"] == "v0.28.0"
    state = json.loads(JobQueue(db).get_state("autoupdate", ""))
    assert state["executor_reload_pending"] is False
    assert "dashboard_applied_at" not in state


def test_dashboard_autoupdate_apply_runs_safe_plan_and_records_state(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, require_auth=False))
    client = TestClient(app)
    plan = {
        "installed_version": "0.27.0",
        "installed_tag": "v0.27.0",
        "target": {"tag_name": "v0.28.0"},
        "decision": "stage_full_reload",
        "executor_reload_pending": True,
        "blocked_reason": "",
        "queue": {"active_counts": {}, "active_total": 0},
        "classification": {"risk": "executor_or_shared", "migration_files": [], "risky_files": [], "systemd_files": []},
        "service_plan": {"immediate": [], "deferred": []},
        "warnings": [],
    }
    monkeypatch.setattr("github_agent_bridge.backend._dashboard_autoupdate_plan", lambda db: plan)
    monkeypatch.setattr("github_agent_bridge.backend._dashboard_apply_autoupdate", lambda plan, db: {"applied": True, "blocked": [], "commands": []})

    response = client.post("/api/autoupdate/apply")

    assert response.status_code == 200
    assert response.json()["execution"]["applied"] is True
    assert response.json()["state"]["decision"] == "stage_full_reload"
    assert response.json()["state"]["executor_reload_pending"] is True
    assert response.json()["state"]["dashboard_applied_at"]


def test_dashboard_autoupdate_apply_does_not_arm_completion_when_blocked(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, require_auth=False))
    client = TestClient(app)
    plan = {
        "installed_version": "0.27.0",
        "installed_tag": "v0.27.0",
        "target": {"tag_name": "v0.28.0"},
        "decision": "stage_defer_executor_reload",
        "executor_reload_pending": True,
        "blocked_reason": "active_jobs_block_executor_reload",
        "queue": {"active_counts": {"running": 1}, "active_total": 1},
        "classification": {"risk": "executor_or_shared", "migration_files": [], "risky_files": [], "systemd_files": []},
        "service_plan": {"immediate": [], "deferred": []},
        "warnings": [],
    }
    monkeypatch.setattr("github_agent_bridge.backend._dashboard_autoupdate_plan", lambda db: plan)
    monkeypatch.setattr(
        "github_agent_bridge.backend._dashboard_apply_autoupdate",
        lambda plan, db: {"applied": False, "blocked": ["active_jobs_block_executor_reload"], "commands": []},
    )

    response = client.post("/api/autoupdate/apply")

    assert response.status_code == 409
    assert response.json()["state"]["executor_reload_pending"] is False
    assert "dashboard_applied_at" not in response.json()["state"]


def test_dashboard_autoupdate_complete_pending_requires_applied_state(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.set_state(
        "autoupdate",
        json.dumps(
            {
                "executor_reload_pending": True,
                "target": {"tag_name": "v0.28.0"},
                "classification": {"migration_files": []},
                "service_plan": {"deferred": [{"command": "restart", "unit": "github-agent-bridge.service"}]},
            }
        ),
    )
    app = create_app(DashboardConfig(db=db, require_auth=False))
    client = TestClient(app)
    monkeypatch.setattr(
        "github_agent_bridge.backend.complete_pending_reload",
        lambda db, systemctl_bin="systemctl": {"completed": True, "blocked": [], "commands": []},
    )

    response = client.post("/api/autoupdate/complete-pending")

    assert response.status_code == 409
    assert response.json()["completion"]["blocked"] == ["autoupdate_not_applied"]


def test_dashboard_autoupdate_complete_pending_reports_blockers(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.set_state("autoupdate", json.dumps({"dashboard_applied_at": "2026-06-09T19:50:00Z", "executor_reload_pending": True}))
    app = create_app(DashboardConfig(db=db, require_auth=False))
    client = TestClient(app)
    monkeypatch.setattr(
        "github_agent_bridge.backend.complete_pending_reload",
        lambda db, systemctl_bin="systemctl": {"completed": False, "blocked": ["active_jobs_block_executor_reload"]},
    )

    response = client.post("/api/autoupdate/complete-pending")

    assert response.status_code == 409
    assert response.json()["completion"]["blocked"] == ["active_jobs_block_executor_reload"]


def test_dashboard_about_exposes_package_version_and_repository(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    response = client.get("/api/about")

    assert response.status_code == 200
    assert response.json() == {
        "service": "github-agent-bridge-dashboard",
        "version": __version__,
        "repository_url": "https://github.com/pilipilisbot/github-agent-bridge",
    }


def test_dashboard_serves_built_react_ui_with_existing_auth(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_serves_dedicated_job_frontend_route(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db).enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/jobs/1")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_serves_dedicated_knowledge_frontend_route(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/knowledge")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_serves_dedicated_mcp_frontend_route(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/mcp")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_serves_dedicated_system_frontend_route(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/system")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_job_frontend_route_falls_back_for_deep_links(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db).enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, require_auth=False))

    response = TestClient(app).get("/jobs/1/session")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "root" in response.text


def test_dashboard_ui_redirects_to_oauth_login_by_default(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db)
    app = create_app(
        DashboardConfig(
            db=db,
            static_dir=static_dir,
            secret_key="secret",
            oauth_client_id="client",
            oauth_client_secret="client-secret",
            allowed_users={"alice"},
        )
    )

    response = TestClient(app, follow_redirects=False).get("/")

    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_dashboard_ui_reports_missing_build_after_auth(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, static_dir=tmp_path / "missing-static", require_auth=False))

    response = TestClient(app).get("/")

    assert response.status_code == 503
    assert response.json()["detail"] == "dashboard_ui_not_built"


def test_dashboard_missing_db_does_not_create_database(tmp_path):
    db = tmp_path / "missing.sqlite3"
    app = create_app(DashboardConfig(db=db, require_auth=False))

    response = TestClient(app).get("/api/health")

    assert response.json()["db_exists"] is False
    assert db.exists() is False


def test_dashboard_jobs_can_filter_by_status_repo_action_intent_and_actor(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(from_addr="ecarreras <notifications@github.com>"), Policy(trusted_orgs=["gisce"]))
    q.enqueue(notif(uid=2, mid="<2@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/2#issuecomment-20", from_addr="marc <notifications@github.com>"), Policy(trusted_orgs=["gisce"]))
    q.finish(job.id, "blocked", "failed", "boom")
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    rows = list_jobs(db, status_filter="blocked", repo="gisce/erp", action="reply_comment", intent="review_only", actor="ECARRERAS")
    actors = list_job_actors(db)
    response = client.get("/api/jobs", params={"actor": "@ecarreras"})
    actor_response = client.get("/api/jobs/actors")

    assert [row["status"] for row in rows] == ["blocked"]
    assert [row["trigger_actor"] for row in rows] == ["ecarreras"]
    assert [actor["login"] for actor in actors] == ["ecarreras", "marc"]
    assert response.json()["jobs"][0]["id"] == job.id
    assert actor_response.json()["actors"][0]["avatar_url"] == "https://github.com/ecarreras.png?size=80"
    assert list_jobs(db, status_filter="pending", actor="ecarreras") == []


def test_dashboard_jobs_orders_active_work_before_finished_jobs(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    policy = Policy(trusted_orgs=["gisce"])

    done_old, _ = q.enqueue(notif(uid=1, mid="<done-old@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/1"), policy)
    done_new, _ = q.enqueue(notif(uid=2, mid="<done-new@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/2"), policy)
    pending_old, _ = q.enqueue(notif(uid=3, mid="<pending-old@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/3"), policy)
    pending_new, _ = q.enqueue(notif(uid=4, mid="<pending-new@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/4"), policy)
    running, _ = q.enqueue(notif(uid=5, mid="<running@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/5"), policy)
    waiting, _ = q.enqueue(notif(uid=6, mid="<waiting@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/6"), Policy(trusted_orgs=[]))
    blocked, _ = q.enqueue(notif(uid=7, mid="<blocked@github.com>", body="@pilipilisbot https://github.com/gisce/erp/issues/7"), policy)

    with q.connect() as con:
        con.execute(
            "UPDATE jobs SET status='done', finished_at='2026-06-01T09:00:00Z', updated_at='2026-06-01T09:00:00Z' WHERE id=?",
            (done_old.id,),
        )
        con.execute(
            "UPDATE jobs SET status='done', finished_at='2026-06-04T09:00:00Z', updated_at='2026-06-04T09:00:00Z' WHERE id=?",
            (done_new.id,),
        )
        con.execute(
            "UPDATE jobs SET created_at='2026-06-01T08:00:00Z', updated_at='2026-06-01T08:00:00Z' WHERE id=?",
            (pending_old.id,),
        )
        con.execute(
            "UPDATE jobs SET created_at='2026-06-04T08:00:00Z', updated_at='2026-06-04T08:00:00Z' WHERE id=?",
            (pending_new.id,),
        )
        con.execute(
            "UPDATE jobs SET status='running', started_at='2026-06-03T12:00:00Z', updated_at='2026-06-03T12:00:00Z' WHERE id=?",
            (running.id,),
        )
        con.execute(
            "UPDATE jobs SET status='waiting_approval', created_at='2026-06-05T08:00:00Z', updated_at='2026-06-05T08:00:00Z' WHERE id=?",
            (waiting.id,),
        )
        con.execute(
            "UPDATE jobs SET status='blocked', finished_at='2026-06-05T09:00:00Z', updated_at='2026-06-05T09:00:00Z' WHERE id=?",
            (blocked.id,),
        )

    rows = list_jobs(db, limit=10)

    assert [row["id"] for row in rows] == [
        running.id,
        pending_new.id,
        pending_old.id,
        waiting.id,
        blocked.id,
        done_new.id,
        done_old.id,
    ]
    assert [row["id"] for row in list_jobs(db, status_filter="done", limit=10)] == [done_new.id, done_old.id]


def test_dashboard_exposes_job_detail_logs_and_metrics(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    q.finish(job.id, "done", "completed")

    detail = get_job_detail(db, job.id)
    metrics = metrics_summary(db)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    assert detail is not None
    assert detail["worklog"][0]["phase"] == "queued"
    assert metrics["status_counts"]["done"] == 1
    assert list(metrics["by_created_day"].values()) == [1]
    assert client.get(f"/api/jobs/{job.id}/logs").json()["logs"][-1]["phase"] == "done"
    assert client.get("/api/metrics/summary").json()["metrics"]["by_repo"]["gisce/erp"] == 1


def test_dashboard_metrics_groups_runtime_usage_by_requested_timezone(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    first, _ = q.enqueue(notif(uid=1, mid="<1@github.com>"), Policy(trusted_orgs=["gisce"]))
    second, _ = q.enqueue(notif(uid=2, mid="<2@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/2#issuecomment-20"), Policy(trusted_orgs=["gisce"]))
    missing_finish, _ = q.enqueue(
        notif(uid=3, mid="<3@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/3#issuecomment-30"),
        Policy(trusted_orgs=["gisce"]),
    )
    invalid_finish, _ = q.enqueue(
        notif(uid=4, mid="<4@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/4#issuecomment-40"),
        Policy(trusted_orgs=["gisce"]),
    )
    q.finish(first.id, "done", "completed")
    q.finish(second.id, "done", "completed")
    q.finish(missing_finish.id, "done", "completed")
    q.finish(invalid_finish.id, "done", "completed")
    with q.connect() as con:
        con.execute(
            "UPDATE jobs SET started_at=?, finished_at=? WHERE id=?",
            ("2026-06-01T23:30:00Z", "2026-06-02T00:30:00Z", first.id),
        )
        con.execute(
            "UPDATE jobs SET started_at=?, finished_at=? WHERE id=?",
            ("2026-06-02T10:00:00Z", "2026-06-02T10:30:00Z", second.id),
        )
        con.execute(
            "UPDATE jobs SET started_at=?, finished_at=NULL WHERE id=?",
            ("2026-06-02T11:00:00Z", missing_finish.id),
        )
        con.execute(
            "UPDATE jobs SET started_at=?, finished_at=? WHERE id=?",
            ("2026-06-02T12:00:00Z", "n/a", invalid_finish.id),
        )

    metrics = metrics_summary(db, timezone_name="America/New_York")
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get("/api/metrics/summary", params={"timezone": "America/New_York"}).json()["metrics"]

    assert metrics["runtime_usage"]["day"] == [
        {"bucket": "2026-06-01", "seconds": 3600, "minutes": 60.0, "jobs": 1},
        {"bucket": "2026-06-02", "seconds": 1800, "minutes": 30.0, "jobs": 1},
    ]
    assert metrics["runtime_usage"]["month"] == [
        {"bucket": "2026-06", "seconds": 5400, "minutes": 90.0, "jobs": 2},
    ]
    assert metrics["runtime_seconds"] == {"median": 1800, "p90": 3600, "p99": 3600}
    assert payload["runtime_usage"] == metrics["runtime_usage"]


def test_dashboard_exposes_safe_openclaw_session_correlation(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None

    session = job_session(db, job.id)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get(f"/api/jobs/{job.id}/session").json()["session"]

    assert claimed.metadata["openclaw_session_id"] == f"github-agent-bridge-job-{job.id}"
    assert session is not None
    assert session["id"] == f"github-agent-bridge-job-{job.id}"
    assert session["transcript_exposure"] == "redacted_dashboard"
    assert payload["id"] == session["id"]


def test_dashboard_exposes_selected_model_route_on_jobs(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    q.claim_next("worker-1")
    q.add_session_event(job.id, "model_route_selected", "OpenClaw model route selected", "model=openai/gpt-5.4-mini thinking=medium")
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    list_route = client.get("/api/jobs").json()["jobs"][0]["model_route"]
    detail_route = client.get(f"/api/jobs/{job.id}").json()["job"]["model_route"]

    assert list_route == {
        "configured": True,
        "model": "openai/gpt-5.4-mini",
        "thinking": "medium",
        "summary": "model=openai/gpt-5.4-mini thinking=medium",
    }
    assert detail_route == list_route


def test_dashboard_exposes_redacted_job_session_events(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None
    q.add_session_event(job.id, "dispatch_finished", "OpenClaw agent exited rc=0", "token=secret ghp_abcdefghijklmnopqrstuvwxyz")

    events = job_session_events(db, job.id)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get(f"/api/jobs/{job.id}/session/events").json()["events"]
    detail = client.get(f"/api/jobs/{job.id}").json()["job"]

    assert [event["event_type"] for event in events] == ["claimed", "dispatch_finished"]
    assert payload[-1]["detail"] == "token=[redacted] [redacted]"
    assert [progress["kind"] for progress in detail["progress"]] == ["semantic", "semantic"]


def test_dashboard_exposes_redacted_openclaw_session_transcript(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_file = sessions_dir / f"{claimed.metadata['openclaw_session_id']}.jsonl"
    session_file.write_text(
        "\n".join(
            [
                '{"type":"session","timestamp":"2026-05-23T08:00:00Z","cwd":"/tmp/work"}',
                '{"type":"message","timestamp":"2026-05-23T08:00:01Z","message":{"role":"assistant","content":[{"type":"toolCall","name":"bash","arguments":{"command":"echo ghp_abcdefghijklmnopqrstuvwxyz"}}]}}',
            ]
        ),
        encoding="utf-8",
    )
    store = sessions_dir / "sessions.json"
    store.write_text(
        '{"agent:github:main":{"sessionId":"%s","sessionFile":"%s"}}' % (claimed.metadata["openclaw_session_id"], session_file),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE", str(store))

    entries = job_session_transcript(db, job.id)
    session = job_session(db, job.id)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get(f"/api/jobs/{job.id}/session/transcript").json()["entries"]

    assert session is not None
    assert session["transcript_available"] is True
    assert entries[0]["title"] == "Session started"
    assert payload[1]["title"] == "Tool call: bash"
    assert "ghp_" not in payload[1]["text"]


def test_dashboard_transcript_includes_live_openclaw_output_before_session_file(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None
    q.add_session_event(job.id, "openclaw_stdout", "OpenClaw CLI output", "thinking live")
    q.add_session_event(job.id, "openclaw_stderr", "OpenClaw CLI error output", "token=secret")

    entries = job_session_transcript(db, job.id)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get(f"/api/jobs/{job.id}/session/transcript").json()["entries"]

    assert [entry["kind"] for entry in entries] == ["openclaw_stdout", "openclaw_stderr"]
    assert payload[0]["title"] == "OpenClaw stdout"
    assert payload[0]["text"] == "thinking live"
    assert payload[1]["text"] == "token=[redacted]"


def test_dashboard_transcript_includes_live_openclaw_trajectory_before_session_file(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_id = claimed.metadata["openclaw_session_id"]
    trajectory_file = sessions_dir / f"{session_id}.trajectory.jsonl"
    trajectory_file.write_text(
        "\n".join(
            [
                '{"type":"session.started","ts":"2026-05-23T08:00:00Z","data":{"workspaceDir":"/tmp/work"}}',
                '{"type":"context.compiled","ts":"2026-05-23T08:00:00Z","data":{"systemPrompt":"do not expose"}}',
                '{"type":"tool.call","ts":"2026-05-23T08:00:01Z","data":{"name":"bash","arguments":{"command":"echo ghp_abcdefghijklmnopqrstuvwxyz","cwd":"/tmp/work"}}}',
                '{"type":"tool.result","ts":"2026-05-23T08:00:02Z","data":{"name":"bash","status":"completed","output":"live output"}}',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE", str(sessions_dir / "sessions.json"))

    entries = job_session_transcript(db, job.id)
    session = job_session(db, job.id)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    payload = client.get(f"/api/jobs/{job.id}/session/transcript").json()["entries"]

    assert session is not None
    assert session["transcript_available"] is True
    assert [entry["kind"] for entry in entries] == ["trajectory_session", "tool_call", "tool_result"]
    assert all("do not expose" not in entry["text"] for entry in payload)
    assert payload[1]["title"] == "Tool call: bash"
    assert "ghp_" not in payload[1]["text"]
    assert payload[2]["text"] == "status=completed\nlive output"


def test_dashboard_sse_replays_existing_live_transcript_entries(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None
    q.add_session_event(job.id, "openclaw_stdout", "OpenClaw CLI output", "already live")

    async def first_chunks():
        stream = _session_stream_events(db, job.id, sleep_seconds=0)
        chunks = []
        try:
            for _ in range(6):
                chunks.append(await anext(stream))
                if "event: transcript_entry" in "".join(chunks):
                    break
        finally:
            await stream.aclose()
        return "".join(chunks)

    body = asyncio.run(first_chunks())
    assert "event: transcript_entry" in body
    assert "already live" in body


def test_dashboard_sse_streams_live_trajectory_entries_before_session_file(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_id = claimed.metadata["openclaw_session_id"]
    (sessions_dir / f"{session_id}.trajectory.jsonl").write_text(
        '{"type":"tool.result","ts":"2026-05-23T08:00:02Z","data":{"name":"bash","status":"completed","output":"live trajectory output"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE", str(sessions_dir / "sessions.json"))

    async def first_chunks():
        stream = _session_stream_events(db, job.id, sleep_seconds=0)
        chunks = []
        try:
            for _ in range(6):
                chunks.append(await anext(stream))
                if "live trajectory output" in "".join(chunks):
                    break
        finally:
            await stream.aclose()
        return "".join(chunks)

    body = asyncio.run(first_chunks())
    assert "event: transcript_entry" in body
    assert "live trajectory output" in body


def test_dashboard_requires_auth_by_default(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}))

    response = TestClient(app).get("/api/jobs")

    assert response.status_code == 401


def test_dashboard_session_authorization_allows_configured_user(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, static_dir=static_dir, secret_key="secret", allowed_users={"alice"}))

    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, "alice"))

    assert client.get("/api/jobs").status_code == 200
    assert client.get("/").status_code == 200


def test_dashboard_me_backfills_legacy_session_avatar(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, "alice"))

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["user"] == {
        "login": "alice",
        "avatar_url": "https://github.com/alice.png?size=80",
        "html_url": "https://github.com/alice",
        "is_admin": False,
    }


def test_dashboard_me_exposes_safe_oauth_profile(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}))
    client = TestClient(app)
    client.cookies.set(
        "gab_dashboard_session",
        _sign(
            app.state.dashboard_config,
            _encode_session({"login": "Alice", "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4", "html_url": "https://github.com/alice"}),
        ),
    )

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["user"] == {
        "login": "alice",
        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        "html_url": "https://github.com/alice",
        "is_admin": False,
    }


def test_dashboard_me_exposes_admin_mode_from_signed_session(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set(
        "gab_dashboard_session",
        _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)),
    )

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["user"]["is_admin"] is True


def test_dashboard_knowledge_lists_and_admin_moderates_feedback(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(
        db,
        notif(),
        GitHubContext(["https://github.com/gisce/erp/pull/1#issuecomment-10"], "gisce/erp", 1, comment_id=10),
        "reply_comment",
        "auto_trusted",
        "review_only",
    )
    event = feedback.list_events(db, "repo:gisce/erp")[0]
    proposal = feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": "repo:gisce/erp",
            "type": "operating_rule",
            "rule": "Keep knowledge management auditable.",
            "confidence": 0.7,
            "reason": "Human correction should be reused.",
        },
        auto_approve_confidence=0.9,
    )
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    listing = client.get("/api/knowledge", params={"repo": "gisce/erp"}).json()
    forbidden = client.post(f"/api/knowledge/proposals/{proposal['id']}/approve")
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    approved = client.post(f"/api/knowledge/proposals/{proposal['id']}/approve")
    rules = client.get("/api/knowledge", params={"repo": "gisce/erp"}).json()["rules"]
    deleted = client.delete(f"/api/knowledge/rules/{rules[0]['id']}")

    assert listing["repositories"] == ["gisce/erp"]
    assert listing["proposals"][0]["status"] == "proposed"
    assert listing["rules"] == []
    assert forbidden.status_code == 403
    assert approved.status_code == 200
    assert approved.json()["proposal"]["status"] == "approved"
    assert rules[0]["source_event_details"][0]["source_url"] == "https://github.com/gisce/erp/pull/1#issuecomment-10"
    assert deleted.status_code == 200
    assert client.get("/api/knowledge", params={"repo": "gisce/erp"}).json()["rules"] == []


def test_dashboard_admin_can_change_knowledge_rule_scope(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    rule = feedback.add_rule(db, "repo:gisce/erp", "operating_rule", "Keep knowledge management auditable.", 0.8)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    forbidden = client.patch(f"/api/knowledge/rules/{rule['id']}", json={"scope": "global"})
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    updated = client.patch(f"/api/knowledge/rules/{rule['id']}", json={"scope": "global"})
    invalid = client.patch(f"/api/knowledge/rules/{updated.json()['rule']['id']}", json={"scope": "repo:missing-owner"})

    assert forbidden.status_code == 403
    assert updated.status_code == 200
    assert updated.json()["rule"]["scope"] == "global"
    assert client.get("/api/knowledge").json()["rules"][0]["scope"] == "global"
    assert client.get("/api/knowledge", params={"repo": "gisce/erp"}).json()["rules"][0]["scope"] == "global"
    assert invalid.status_code == 400


def test_dashboard_admin_manages_mcp_tokens(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    forbidden = client.post("/api/mcp/tokens", json={"name": "local agent"})
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    created = client.post("/api/mcp/tokens", json={"name": "local agent"})
    listed = client.get("/api/mcp/tokens")
    revoked = client.delete(f"/api/mcp/tokens/{created.json()['record']['id']}")
    listed_after_revoke = client.get("/api/mcp/tokens")

    assert forbidden.status_code == 403
    assert created.status_code == 200
    assert created.json()["token"].startswith("gab_mcp_")
    assert listed.json()["tokens"][0]["name"] == "local agent"
    assert "token" not in listed.json()["tokens"][0]
    assert revoked.status_code == 200
    assert listed_after_revoke.json()["tokens"] == []


def test_http_mcp_uses_bearer_tokens_and_shared_server(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    feedback.add_rule(db, "repo:gisce/erp", "technical_criterion", "Prefer the HTTP MCP endpoint for remote agents.", 0.9)
    token = create_token(db, "remote agent")["token"]
    app = create_app(DashboardConfig(db=db, require_auth=False))
    client = TestClient(app)

    missing = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    invalid = client.post("/api/mcp", headers={"Authorization": "Bearer bad-token"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    response = client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_knowledge", "arguments": {"repo": "gisce/erp"}},
        },
    )

    assert missing.status_code == 401
    assert missing.json()["detail"] == "mcp_token_required"
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "invalid_mcp_token"
    assert response.status_code == 200
    assert response.json()["jsonrpc"] == "2.0"
    assert response.json()["id"] == 2
    knowledge = json.loads(response.json()["result"]["content"][0]["text"])
    assert knowledge["rules"][0]["rule"] == "Prefer the HTTP MCP endpoint for remote agents."


def test_dashboard_retry_requires_admin_and_requeues_retryable_job(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    q.finish(job.id, "blocked", "failed", "boom")
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    forbidden = client.post(f"/api/jobs/{job.id}/retry")
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    response = client.post(f"/api/jobs/{job.id}/retry")
    retried = client.get(f"/api/jobs/{job.id}").json()["job"]

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "pending"
    assert retried["worklog"][-1]["phase"] == "retry"
    assert retried["worklog"][-1]["summary"] == "job requeued by @alice"


def test_dashboard_retry_rejects_non_retryable_jobs(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))

    response = client.post(f"/api/jobs/{job.id}/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_retryable"


def test_dashboard_dismiss_requires_admin_and_marks_recoverable_job_done(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    q.finish(job.id, "blocked", "failed", "boom")
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"})))

    forbidden = client.post(f"/api/jobs/{job.id}/dismiss")
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))
    response = client.post(f"/api/jobs/{job.id}/dismiss")
    dismissed = client.get(f"/api/jobs/{job.id}").json()["job"]

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "done"
    assert dismissed["last_error"] is None
    assert dismissed["worklog"][-1]["phase"] == "dismissed"
    assert dismissed["worklog"][-1]["detail"] == "dismissed by @alice"


def test_dashboard_dismiss_rejects_non_recoverable_jobs(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    app = create_app(DashboardConfig(db=db, secret_key="secret", allowed_users={"alice"}, admin_users={"alice"}))
    client = TestClient(app)
    client.cookies.set("gab_dashboard_session", _sign(app.state.dashboard_config, _encode_session({"login": "Alice"}, is_admin=True)))

    response = client.post(f"/api/jobs/{job.id}/dismiss")

    assert response.status_code == 409
    assert response.json()["detail"] == "job_not_dismissable"


def test_dashboard_oauth_login_uses_minimal_scope_for_user_allowlist(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(
        DashboardConfig(
            db=db,
            secret_key="secret",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            allowed_users={"alice"},
        )
    )

    response = TestClient(app, follow_redirects=False).get("/auth/login")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["read:user"]


def test_dashboard_oauth_login_requests_org_scope_only_for_org_allowlist(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(
        DashboardConfig(
            db=db,
            secret_key="secret",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            allowed_orgs={"example"},
        )
    )

    response = TestClient(app, follow_redirects=False).get("/auth/login")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["read:user read:org"]


def test_dashboard_oauth_login_requests_org_scope_for_team_allowlist(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(
        DashboardConfig(
            db=db,
            secret_key="secret",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            allowed_teams={"example/platform"},
        )
    )

    response = TestClient(app, follow_redirects=False).get("/auth/login")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["read:user read:org"]


def test_dashboard_oauth_login_requests_org_scope_for_admin_team(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    app = create_app(
        DashboardConfig(
            db=db,
            secret_key="secret",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            allowed_users={"alice"},
            admin_teams={"example/platform"},
        )
    )

    response = TestClient(app, follow_redirects=False).get("/auth/login")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["read:user read:org"]


def test_dashboard_session_authorization_allows_configured_team(monkeypatch):
    def fake_github_json(url, token):
        assert token == "token"
        assert url.endswith("/user/teams")
        return [
            {"slug": "other", "organization": {"login": "example"}},
            {"slug": "platform", "organization": {"login": "Example"}},
        ]

    monkeypatch.setattr("github_agent_bridge.backend._github_json", fake_github_json)
    config = DashboardConfig(
        secret_key="secret",
        allowed_teams={"example/platform"},
    )

    assert _is_allowed(config, "alice", "token") is True
    assert _is_allowed(config, "alice", None) is False


def test_dashboard_session_authorization_allows_admin_user_without_allowed_user():
    config = DashboardConfig(secret_key="secret", admin_users={"alice"})

    assert _is_allowed(config, "alice", None) is True
    assert _is_allowed(config, "bob", None) is False


def test_dashboard_session_authorization_allows_admin_team_without_allowed_team(monkeypatch):
    def fake_github_json(url, token):
        assert token == "token"
        assert url.endswith("/user/teams")
        return [{"slug": "platform", "organization": {"login": "Example"}}]

    monkeypatch.setattr("github_agent_bridge.backend._github_json", fake_github_json)
    config = DashboardConfig(secret_key="secret", admin_teams={"example/platform"})

    assert _is_allowed(config, "bob", "token") is True
    assert _is_allowed(config, "bob", None) is False


def test_dashboard_admin_authorization_allows_configured_user_or_team(monkeypatch):
    def fake_github_json(url, token):
        assert token == "token"
        assert url.endswith("/user/teams")
        return [{"slug": "platform", "organization": {"login": "Example"}}]

    monkeypatch.setattr("github_agent_bridge.backend._github_json", fake_github_json)
    config = DashboardConfig(secret_key="secret", admin_users={"alice"}, admin_teams={"example/platform"})

    assert _is_admin(config, "alice", None) is True
    assert _is_admin(config, "bob", "token") is True
    assert _is_admin(config, "bob", None) is False


def test_dashboard_processes_exposes_live_executor_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    q.claim_next("worker-1")

    def fake_monitor(_db):
        return MonitorReport(
            ok=True,
            metrics={
                "running_jobs": [
                    {
                        "id": 1,
                        "work_key": "gisce/erp#1",
                        "semantic_progress": {"phase": "claimed", "summary": "claimed by worker-1"},
                    }
                ],
                "executor_service": "active",
                "executor_pid": 123,
                "executor_children": [
                    {
                        "pid": 456,
                        "ppid": 123,
                        "state": "S",
                        "cmd": "openclaw agent",
                        "cpu_ticks": 12,
                        "io_bytes": {"read_bytes": 100, "write_bytes": 50},
                        "children": [],
                    }
                ],
            },
        )

    monkeypatch.setattr("github_agent_bridge.backend.monitor", fake_monitor)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    response = client.get("/api/processes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["executor"]["service"] == "active"
    assert payload["executor"]["children"][0]["cpu_ticks"] == 12
    assert payload["signals"]["live_process"]["state"] == "live"
    assert payload["signals"]["semantic_progress"][0]["semantic_progress"]["phase"] == "claimed"
    assert payload["samples"] == []


def test_dashboard_systemd_exposes_configured_bridge_units(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    def fake_systemd_status():
        return {
            "available": True,
            "units": [
                {
                    "role": "executor",
                    "kind": "service",
                    "unit": "github-agent-bridge.service",
                    "active_state": "active",
                    "sub_state": "running",
                    "uptime_seconds": 60,
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr("github_agent_bridge.backend.systemd_status", fake_systemd_status)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    response = client.get("/api/systemd")

    assert response.status_code == 200
    assert response.json()["units"][0]["unit"] == "github-agent-bridge.service"
    assert response.json()["units"][0]["active_state"] == "active"


def test_dashboard_systemd_journal_stream_rejects_unknown_units(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))

    response = client.get("/api/systemd/journal/stream?unit=ssh.service")

    assert response.status_code == 404
    assert response.json()["detail"] == "systemd_unit_not_allowed"


def test_dashboard_exposes_persisted_process_samples_and_alerts(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs=["gisce"]))
    claimed = q.claim_next("worker-1")
    assert claimed is not None
    metrics = {
        "running_jobs": [{"id": job.id, "work_key": job.work_key}],
        "executor_service": "active",
        "executor_pid": 123,
        "executor_children": [
            {
                "pid": 456,
                "ppid": 123,
                "state": "S",
                "cmd": "openclaw agent",
                "cpu_ticks": 12,
                "io_bytes": {"read_bytes": 100, "write_bytes": 50},
                "children": [],
            }
        ],
    }
    record_monitor_observation(db, metrics, ["running job 1 old"])

    def fake_monitor(_db):
        return MonitorReport(ok=True, metrics=metrics)

    monkeypatch.setattr("github_agent_bridge.backend.monitor", fake_monitor)
    client = TestClient(create_app(DashboardConfig(db=db, require_auth=False)))
    processes = client.get("/api/processes").json()
    alerts = client.get("/api/alerts").json()

    assert processes["samples"][0]["cpu_ticks"] == 12
    assert processes["samples"][0]["running_job_ids"] == [job.id]
    assert processes["signals"]["process_activity"]["state"] == "active"
    assert alerts["alerts"][0]["message"] == "running job 1 old"
