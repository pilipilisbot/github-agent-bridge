import sqlite3

from github_agent_bridge.models import Notification
from github_agent_bridge.monitor import MonitorThresholds, monitor
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


def notif(uid=1, mid="<1@github.com>", body="@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10"):
    return Notification(uid=uid, message_id=mid, subject="Re: [gisce/erp] PR", from_addr="GitHub <notifications@github.com>", body=body, auth={"spf": True, "dkim": True, "dmarc": True})


def test_monitor_ok_on_empty_initialized_db(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    report = monitor(db, check_systemd=False)
    assert report.ok is True
    assert "pending=0" in report.text()


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
    con.commit()
    report = monitor(db, thresholds=MonitorThresholds(work_running_warn_seconds=1), check_systemd=False)
    assert report.ok is False
    assert any("running job" in a for a in report.alerts)
