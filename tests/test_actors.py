from __future__ import annotations

import json
import sqlite3
import subprocess

from github_agent_bridge.actors import (
    actor_details_from_github_payload,
    actor_endpoint,
    backfill_trigger_actors,
    default_gh_bin,
    normalize_github_login,
    trigger_actor_from_notification,
)
from github_agent_bridge.models import GitHubContext, Notification
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


def test_trigger_actor_from_notification_uses_github_sender_login():
    n = Notification(
        uid=1,
        message_id="<1@github.com>",
        subject="Re: [gisce/erp] issue",
        from_addr="ecarreras <notifications@github.com>",
        body="https://github.com/gisce/erp/issues/1",
        auth={"spf": True, "dkim": True, "dmarc": True},
    )

    assert trigger_actor_from_notification(n) == "ecarreras"


def test_normalize_github_login_accepts_github_app_bot_suffix():
    assert normalize_github_login("copilot-pull-request-reviewer[bot]") == "copilot-pull-request-reviewer[bot]"


def test_default_gh_bin_uses_service_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_GH_BIN", "/opt/bin/gh")

    assert default_gh_bin() == "/opt/bin/gh"


def test_actor_details_from_github_payload_accepts_github_app_bot_login():
    actor = actor_details_from_github_payload(
        {
            "user": {
                "login": "copilot-pull-request-reviewer[bot]",
                "id": 946600,
                "avatar_url": "https://avatars.githubusercontent.com/in/946600?v=4",
            }
        }
    )

    assert actor is not None
    assert actor.login == "copilot-pull-request-reviewer[bot]"
    assert actor.avatar_url == "https://avatars.githubusercontent.com/in/946600?v=4"
    assert actor.user_id == 946600


def test_actor_details_from_assignment_event_uses_assigner():
    actor = actor_details_from_github_payload(
        {
            "event": "assigned",
            "actor": {"login": "giscebot", "id": 286264155},
            "assignee": {"login": "giscebot", "id": 286264155},
            "assigner": {
                "login": "ecarreras",
                "id": 294235,
                "avatar_url": "https://avatars.githubusercontent.com/u/294235?v=4",
            },
        }
    )

    assert actor is not None
    assert actor.login == "ecarreras"
    assert actor.avatar_url == "https://avatars.githubusercontent.com/u/294235?v=4"
    assert actor.user_id == 294235


def test_actor_endpoint_prefers_exact_trigger_resource():
    assert actor_endpoint(GitHubContext(urls=[], repo="gisce/erp", issue_number=1, comment_id=99)) == "repos/gisce/erp/issues/comments/99"
    assert (
        actor_endpoint(
            GitHubContext(
                urls=["https://github.com/gisce/erp/issues/1#event-28540136634"],
                repo="gisce/erp",
                issue_number=1,
            )
        )
        == "repos/gisce/erp/issues/events/28540136634"
    )
    assert actor_endpoint(GitHubContext(urls=[], repo="gisce/erp", issue_number=1)) == "repos/gisce/erp/issues/1"


def test_backfill_trigger_actors_uses_stored_context(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    monkeypatch.setattr("github_agent_bridge.queue.trigger_actor_details_for_enqueue", lambda notification, ctx: None)
    q = JobQueue(db)
    job, _ = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] issue",
            from_addr="GitHub <notifications@github.com>",
            body="https://github.com/gisce/erp/issues/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        Policy(trusted_orgs={"gisce"}),
    )

    calls = []

    def fake_run(args, check=False, stdout=None, stderr=None, text=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"user": {"login": "ecarreras", "id": 294235, "avatar_url": "https://avatars.githubusercontent.com/u/294235?v=4"}}), "")

    monkeypatch.setattr("github_agent_bridge.actors.subprocess.run", fake_run)

    result = backfill_trigger_actors(db)

    assert calls == [["gh", "api", "repos/gisce/erp/issues/comments/99"]]
    assert result["updated"] == 1
    assert q.get(job.id).trigger_actor == "ecarreras"
    assert q.get(job.id).trigger_actor_avatar_url == "https://avatars.githubusercontent.com/u/294235?v=4"


def test_backfill_dry_run_does_not_migrate_legacy_schema(tmp_path, monkeypatch):
    db = tmp_path / "legacy.sqlite3"
    ctx = GitHubContext(urls=["https://github.com/gisce/erp/issues/1"], repo="gisce/erp", issue_number=1)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, context_json TEXT NOT NULL)")
    con.execute("INSERT INTO jobs(id, context_json) VALUES(1, ?)", (ctx.to_json(),))
    con.commit()
    con.close()

    def fake_run(args, check=False, stdout=None, stderr=None, text=False):
        return subprocess.CompletedProcess(args, 0, json.dumps({"user": {"login": "ecarreras"}}), "")

    monkeypatch.setattr("github_agent_bridge.actors.subprocess.run", fake_run)

    result = backfill_trigger_actors(db, dry_run=True)

    con = sqlite3.connect(db)
    columns = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    con.close()
    assert result["updated"] == 1
    assert result["updates"][0]["trigger_actor_avatar_url"] == "https://github.com/ecarreras.png?size=80"
    assert "trigger_actor" not in columns
    assert "trigger_actor_avatar_url" not in columns


def test_backfill_trigger_actors_fills_missing_avatar_without_api(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    q = JobQueue(db)
    job, _ = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] issue",
            from_addr="ecarreras <notifications@github.com>",
            body="https://github.com/gisce/erp/issues/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        Policy(trusted_orgs={"gisce"}),
    )
    with q.connect() as con:
        con.execute("UPDATE jobs SET trigger_actor_avatar_url=NULL WHERE id=?", (job.id,))

    def fail_run(*args, **kwargs):
        raise AssertionError("existing trigger_actor should not need a GitHub API lookup")

    monkeypatch.setattr("github_agent_bridge.actors.subprocess.run", fail_run)

    result = backfill_trigger_actors(db)

    assert result["updated"] == 1
    assert q.get(job.id).trigger_actor == "ecarreras"
    assert q.get(job.id).trigger_actor_avatar_url == "https://github.com/ecarreras.png?size=80"
