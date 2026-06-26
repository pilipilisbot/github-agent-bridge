from github_agent_bridge.queue import JobQueue
from github_agent_bridge.web_push import notify_job_completion, save_subscription, subscription_status


def subscription(endpoint: str = "https://push.example/sub/1"):
    return {"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "auth-secret"}}


def test_save_subscription_tracks_github_login(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    saved = save_subscription(db, "Ecarreras", subscription())
    status = subscription_status(db, "ecarreras")

    assert saved["user_login"] == "ecarreras"
    assert status["enabled"] is True
    assert status["subscriptions"][0]["endpoint"] == "https://push.example/sub/1"


def test_notify_job_completion_sends_to_matching_human_subscriptions(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    save_subscription(db, "ecarreras", subscription("https://push.example/sub/ecarreras"))
    save_subscription(db, "marc", subscription("https://push.example/sub/marc"))
    sent = []

    result = notify_job_completion(
        db,
        actors=["ecarreras", "copilot[bot]", "marc", "ecarreras"],
        job_id=42,
        work_key="gisce/erp#27315",
        status="done",
        summary="agent dispatch queued",
        followup_url="https://github.com/gisce/erp/pull/27315#issuecomment-2",
        dashboard_url="https://bridge.example.com",
        sender=lambda sub, payload: sent.append((sub, payload)),
    )

    assert result == {"recipients": ["ecarreras", "marc"], "attempted": 2, "sent": 2, "failed": 0}
    assert {item[0]["endpoint"] for item in sent} == {"https://push.example/sub/ecarreras", "https://push.example/sub/marc"}
    assert sent[0][1]["url"] == "https://bridge.example.com/jobs/42"
    assert sent[0][1]["job_url"] == "https://bridge.example.com/jobs/42"
    assert sent[0][1]["github_url"] == "https://github.com/gisce/erp/pull/27315#issuecomment-2"
    assert sent[0][1]["followup_url"] == "https://github.com/gisce/erp/pull/27315#issuecomment-2"
    assert sent[0][1]["timestamp"].endswith("Z")


def test_notify_job_completion_includes_configured_icon(tmp_path, monkeypatch):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    save_subscription(db, "ecarreras", subscription())
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_WEB_PUSH_ICON_URL", "https://avatars.githubusercontent.com/in/12345?s=192&v=4")
    sent = []

    notify_job_completion(
        db,
        actors=["ecarreras"],
        job_id=42,
        work_key="gisce/erp#27315",
        status="done",
        summary="agent dispatch queued",
        sender=lambda sub, payload: sent.append(payload),
    )

    assert sent[0]["icon"] == "https://avatars.githubusercontent.com/in/12345?s=192&v=4"
