import sqlite3

from github_agent_bridge.models import Notification
from github_agent_bridge import monitor as monitor_module
from github_agent_bridge.monitor import MonitorThresholds, monitor
from github_agent_bridge.observability import list_alerts, recent_process_samples
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


def notif(uid=1, mid="<1@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10"):
    return Notification(uid=uid, message_id=mid, subject="Re: [gisce/erp] PR", from_addr="ecarreras <notifications@github.com>", body=body, auth={"spf": True, "dkim": True, "dmarc": True})


def test_monitor_ok_on_empty_initialized_db(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    report = monitor(db, check_systemd=False)
    assert report.ok is True
    assert "pending=0" in report.text()


def test_monitor_alerts_when_github_release_is_newer(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    monkeypatch.setattr(monitor_module, "_package_version", lambda: "0.18.1")
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_RELEASE_REPO", "pilipilisbot/github-agent-bridge")
    monkeypatch.setattr(
        monitor_module,
        "_latest_github_release",
        lambda repo: {
            "tag_name": "v0.18.2",
            "name": "v0.18.2",
            "html_url": "https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.18.2",
            "published_at": "2026-05-25T10:00:00Z",
            "body": "Fixes the install drift warning.",
        },
    )

    report = monitor(db, check_systemd=False)

    assert report.ok is False
    assert report.metrics["package_version"] == "0.18.1"
    assert report.metrics["release_repo"] == "pilipilisbot/github-agent-bridge"
    assert report.metrics["latest_release"]["tag_name"] == "v0.18.2"
    assert any("new github-agent-bridge release v0.18.2 available" in a for a in report.alerts)
    assert any("Fixes the install drift warning." in a for a in report.alerts)


def test_monitor_does_not_alert_when_github_release_matches(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    monkeypatch.setattr(monitor_module, "_package_version", lambda: "0.18.2")
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_RELEASE_REPO", "pilipilisbot/github-agent-bridge")
    monkeypatch.setattr(
        monitor_module,
        "_latest_github_release",
        lambda repo: {
            "tag_name": "v0.18.2",
            "name": "v0.18.2",
            "html_url": "https://github.com/pilipilisbot/github-agent-bridge/releases/tag/v0.18.2",
            "published_at": "2026-05-25T10:00:00Z",
            "body": "Current release.",
        },
    )

    report = monitor(db, check_systemd=False)

    assert report.ok is True
    assert report.metrics["latest_release"]["tag_name"] == "v0.18.2"


def test_monitor_release_lookup_failure_is_not_alert(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_RELEASE_REPO", "pilipilisbot/github-agent-bridge")
    monkeypatch.setattr(monitor_module, "_latest_github_release", lambda repo: None)

    report = monitor(db, check_systemd=False)

    assert report.ok is True
    assert report.metrics["latest_release_error"] == "could not fetch latest release for pilipilisbot/github-agent-bridge"


def test_monitor_alerts_on_blocked_job(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    q.finish(job.id, "blocked", "boom", "details")
    report = monitor(db, check_systemd=False)
    assert report.ok is False
    assert any("blocked jobs: 1" in a for a in report.alerts)


def test_monitor_alerts_on_old_pending_job(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    con = sqlite3.connect(db)
    con.execute("UPDATE jobs SET created_at='2000-01-01T00:00:00Z', updated_at='2000-01-01T00:00:00Z'")
    con.commit()
    report = monitor(db, thresholds=MonitorThresholds(pending_warn_seconds=1), check_systemd=False)
    assert report.ok is False
    assert any("pending queue oldest age" in a for a in report.alerts)


def test_monitor_alerts_on_old_running_job(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    job = q.claim_next("worker-1")
    assert job is not None
    con = sqlite3.connect(db)
    con.execute("UPDATE jobs SET started_at='2000-01-01T00:00:00Z', updated_at='2000-01-01T00:00:00Z' WHERE id=?", (job.id,))
    con.execute("UPDATE job_progress SET ts='2000-01-01T00:00:00Z' WHERE job_id=?", (job.id,))
    con.commit()
    report = monitor(db, thresholds=MonitorThresholds(work_running_warn_seconds=1), check_systemd=False)
    assert report.ok is False
    assert any("running job" in a for a in report.alerts)
    assert "running detail: job=" in report.text()


def test_monitor_does_not_alert_on_old_running_job_with_recent_progress(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    job = q.claim_next("worker-1")
    assert job is not None
    con = sqlite3.connect(db)
    con.execute("UPDATE jobs SET started_at='2000-01-01T00:00:00Z' WHERE id=?", (job.id,))
    con.commit()

    report = monitor(db, thresholds=MonitorThresholds(work_running_warn_seconds=1), check_systemd=False)

    assert report.ok is True


def test_monitor_alerts_when_running_job_has_no_executor_child(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    q.claim_next("worker-1")
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_main_pid", lambda unit: 123)
    monkeypatch.setattr(monitor_module, "_direct_children", lambda pid: [])
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))

    report = monitor(db)

    assert report.ok is False
    assert report.metrics["alert_codes"] == ["monitor.running_no_executor_child"]
    assert report.metrics["alert_details"] == [
        {
            "code": "monitor.running_no_executor_child",
            "message": "running jobs exist but executor has no child process",
        }
    ]
    assert any("running jobs exist but executor has no child process" in a for a in report.alerts)
    assert "[monitor.running_no_executor_child]" in report.text()


def test_monitor_reports_executor_child_processes(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    q.claim_next("worker-1")
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_main_pid", lambda unit: 123)
    monkeypatch.setattr(monitor_module, "_direct_children", lambda pid: [{"pid": 456, "cmd": "openclaw agent"}])
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))

    report = monitor(db)

    assert report.metrics["executor_pid"] == 123
    assert report.metrics["executor_children"] == [{"pid": 456, "cmd": "openclaw agent"}]
    assert "executor children: 456:openclaw agent" in report.text()


def test_monitor_alerts_when_registered_job_process_is_dead(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    executor_id = "executor-123-deadbeef"
    worker_id = f"{executor_id}/worker-0"
    job = q.claim_next(worker_id)
    assert job is not None
    q.register_runtime_process(
        job.id,
        worker_id,
        executor_id,
        {"pid": 456, "ppid": 123, "pgid": 456, "sid": 456, "start_time_ticks": 999},
    )
    q.set_state("executor_process_tracking_id", executor_id)
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_main_pid", lambda unit: 123)
    monkeypatch.setattr(monitor_module, "_direct_children", lambda pid: [{"pid": 789, "cmd": "other job"}])
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))
    monkeypatch.setattr(monitor_module, "process_identity_matches", lambda *args, **kwargs: False)

    report = monitor(db)

    assert report.ok is False
    assert "monitor.running_process_mismatch" in report.metrics["alert_codes"]
    assert any("PID 456 is dead, reparented, zombie, or reused" in alert for alert in report.alerts)
    assert "runtime detail: job=1 state=running pid=456" in report.text()


def test_monitor_accepts_registered_job_process_identity(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    executor_id = "executor-123-deadbeef"
    worker_id = f"{executor_id}/worker-0"
    job = q.claim_next(worker_id)
    assert job is not None
    q.register_runtime_process(
        job.id,
        worker_id,
        executor_id,
        {"pid": 456, "ppid": 123, "pgid": 456, "sid": 456, "start_time_ticks": 999},
    )
    q.set_state("executor_process_tracking_id", executor_id)
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_main_pid", lambda unit: 123)
    monkeypatch.setattr(monitor_module, "_direct_children", lambda pid: [{"pid": 456, "cmd": "openclaw agent"}])
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))
    monkeypatch.setattr(monitor_module, "process_identity_matches", lambda *args, **kwargs: True)

    report = monitor(db)

    assert "monitor.running_process_mismatch" not in report.metrics.get("alert_codes", [])


def test_monitor_persists_process_samples_and_alert_state(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    q.claim_next("worker-1")
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_main_pid", lambda unit: 123)
    monkeypatch.setattr(
        monitor_module,
        "_direct_children",
        lambda pid: [
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
    )
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))

    monitor(db, persist_observability=True)
    monitor(db, persist_observability=True)

    samples = recent_process_samples(db)
    assert len(samples) == 2
    assert samples[0]["active_since_last_sample"] is True
    assert samples[1]["active_since_last_sample"] is False
    assert samples[1]["root_pid"] == 456
    assert samples[1]["running_job_ids"] == [1]
    assert list_alerts(db) == []


def test_monitor_persists_and_resolves_alerts(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job, _ = q.enqueue(notif(), Policy(trusted_orgs={"gisce"}))
    q.finish(job.id, "blocked", "boom", "details")

    monitor(db, check_systemd=False, persist_observability=True)
    active = list_alerts(db)
    assert active[0]["message"] == "blocked jobs: 1"

    with sqlite3.connect(db) as con:
        con.execute("UPDATE jobs SET status='done' WHERE id=?", (job.id,))
    monitor(db, check_systemd=False, persist_observability=True)

    assert list_alerts(db) == []
    resolved = list_alerts(db, include_resolved=True)
    assert resolved[0]["resolved_at"] is not None


def test_monitor_reports_recent_reader_from_systemd_age(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 42))

    report = monitor(db, thresholds=MonitorThresholds(reader_recent_seconds=180))

    assert report.ok is True
    assert report.metrics["reader_recent"] is True
    assert report.metrics["reader_last_age_seconds"] == 42
    assert "reader_recent=True" in report.text()


def test_monitor_alerts_on_stale_reader_from_systemd_age(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    monkeypatch.setattr(monitor_module, "_is_active", lambda unit: "active")
    monkeypatch.setattr(monitor_module, "_last_service_result", lambda unit: ("success", "0", 181))

    report = monitor(db, thresholds=MonitorThresholds(reader_recent_seconds=180))

    assert report.ok is False
    assert report.metrics["reader_recent"] is False
    assert any("reader last run age 181s > 180s" in a for a in report.alerts)
