import sqlite3

from github_agent_bridge.models import Notification
from github_agent_bridge.policy import FeedbackLearning, Policy
from github_agent_bridge.queue import JobQueue

BODY1 = "@pilipilisbot one https://github.com/gisce/erp/pull/1#issuecomment-10"
BODY2 = "@pilipilisbot two https://github.com/gisce/erp/pull/1#issuecomment-11"
BODY_OTHER = "@pilipilisbot other https://github.com/gisce/erp/pull/2#issuecomment-12"


def notif(uid, mid, body):
    return Notification(uid=uid, message_id=mid, subject="Re: [gisce/erp] PR", from_addr="Edu <notifications@github.com>", body=body, auth={"spf": True, "dkim": True, "dmarc": True})


def policy():
    return Policy(trusted_orgs={"gisce"})


def test_queue_expands_user_in_db_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    JobQueue("~/state/q.sqlite3")

    assert (home / "state" / "q.sqlite3").exists()
    assert not (tmp_path / "~").exists()


def test_connect_recreates_missing_parent_directory(tmp_path):
    q = JobQueue(tmp_path / "missing" / "q.sqlite3")
    for child in q.path.parent.iterdir():
        child.unlink()
    q.path.parent.rmdir()

    assert q.claim_next("worker") is None
    with sqlite3.connect(q.path) as con:
        assert con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()[0] == 1


def test_enqueue_and_coalesce_same_work_key(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    q = JobQueue(tmp_path / "q.sqlite3")
    job1, state1 = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    job2, state2 = q.enqueue(notif(2, "<2@github.com>", BODY2), policy())
    assert state1 == "enqueued"
    assert state2 == "coalesced"
    assert job1.id == job2.id
    assert q.stats()["pending"] == 1
    contexts = q.coalesced_contexts(job1.id)
    assert len(contexts) == 1
    assert contexts[0].comment_id == 11
    assert job1.trigger_actor == "Edu"
    assert job1.trigger_actor_avatar_url == "https://github.com/Edu.png?size=80"


def test_enqueue_stores_trigger_actor_and_coalesced_actor(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    q = JobQueue(tmp_path / "q.sqlite3")
    job, state = q.enqueue(Notification(uid=1, message_id="<1@github.com>", subject="Re: [gisce/erp] PR", from_addr="ecarreras <notifications@github.com>", body=BODY1, auth={"spf": True, "dkim": True, "dmarc": True}), policy())
    q.enqueue(Notification(uid=2, message_id="<2@github.com>", subject="Re: [gisce/erp] PR", from_addr="marc <notifications@github.com>", body=BODY2, auth={"spf": True, "dkim": True, "dmarc": True}), policy())

    assert state == "enqueued"
    assert job.trigger_actor == "ecarreras"
    assert job.trigger_actor_avatar_url == "https://github.com/ecarreras.png?size=80"
    with q.connect() as con:
        row = con.execute("SELECT trigger_actor, trigger_actor_avatar_url FROM coalesced_notifications WHERE job_id=?", (job.id,)).fetchone()
    assert row["trigger_actor"] == "marc"
    assert row["trigger_actor_avatar_url"] == "https://github.com/marc.png?size=80"


def test_enqueue_prefers_context_actor_over_notification_sender(tmp_path, monkeypatch):
    calls = []

    def fake_actor(ctx, *, gh_bin="gh"):
        calls.append((ctx.repo, ctx.issue_number, ctx.comment_id, gh_bin))
        from github_agent_bridge.actors import TriggerActor

        return TriggerActor(login="ecarreras", avatar_url="https://avatars.githubusercontent.com/u/294235?v=4")

    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", fake_actor)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] PR",
            from_addr="GitHub <notifications@github.com>",
            body="https://github.com/gisce/erp/pull/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        policy(),
    )

    assert state == "enqueued"
    assert calls == [("gisce/erp", 1, 99, "gh")]
    assert job.trigger_actor == "ecarreras"
    assert job.trigger_actor_avatar_url == "https://avatars.githubusercontent.com/u/294235?v=4"


def test_enqueue_falls_back_to_context_actor_for_generic_github_sender(tmp_path, monkeypatch):
    calls = []

    def fake_actor(ctx, *, gh_bin="gh"):
        calls.append((ctx.repo, ctx.issue_number, ctx.comment_id, gh_bin))
        from github_agent_bridge.actors import TriggerActor

        return TriggerActor(login="ecarreras", avatar_url="https://avatars.githubusercontent.com/u/294235?v=4")

    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", fake_actor)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] issue",
            from_addr="GitHub <notifications@github.com>",
            body="https://github.com/gisce/erp/issues/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        policy(),
    )

    assert state == "enqueued"
    assert calls == [("gisce/erp", 1, 99, "gh")]
    assert job.trigger_actor == "ecarreras"
    assert job.trigger_actor_avatar_url == "https://avatars.githubusercontent.com/u/294235?v=4"


def test_enqueue_falls_back_to_notification_sender_when_context_lookup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] issue",
            from_addr="ecarreras <notifications@github.com>",
            body="https://github.com/gisce/erp/issues/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        policy(),
    )

    assert state == "enqueued"
    assert job.trigger_actor == "ecarreras"
    assert job.trigger_actor_avatar_url == "https://github.com/ecarreras.png?size=80"


def test_enqueue_leaves_actor_null_when_context_lookup_fails_for_generic_sender(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] issue",
            from_addr="GitHub <notifications@github.com>",
            body="https://github.com/gisce/erp/issues/1#issuecomment-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        policy(),
    )

    assert state == "enqueued"
    assert job.trigger_actor is None
    assert job.trigger_actor_avatar_url is None


def test_claim_parallel_different_work_keys_but_not_same(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.enqueue(notif(2, "<2@github.com>", BODY2), policy())
    q.enqueue(notif(3, "<3@github.com>", BODY_OTHER), policy())
    j1 = q.claim_next("w1")
    j2 = q.claim_next("w2")
    assert {j1.work_key, j2.work_key} == {"gisce/erp#1", "gisce/erp#2"}
    assert q.claim_next("w3") is None


def test_enqueue_does_not_coalesce_into_running_job(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    job1, state1 = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    running = q.claim_next("worker")
    job2, state2 = q.enqueue(notif(2, "<2@github.com>", BODY2), policy())

    assert state1 == "enqueued"
    assert running.id == job1.id
    assert running.status == "running"
    assert state2 == "enqueued"
    assert job2.id != job1.id


def test_enqueue_captures_feedback_for_actionable_jobs(tmp_path, monkeypatch):
    captured = []

    def fake_capture(db_path, n, ctx, action, decision, work_intent):
        captured.append((db_path.name, n.message_id, ctx.work_key, action, decision, work_intent))
        return True

    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", fake_capture)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())

    assert captured == [("q.sqlite3", "<1@github.com>", "gisce/erp#1", "reply_comment", "auto_trusted", "review_only")]


def test_enqueue_workflow_run_failed_notification(tmp_path):
    body = "Run failed: https://github.com/gisce/erp/actions/runs/26325244472"
    n = Notification(uid=1, message_id="<run@github.com>", subject="[gisce/erp] Run failed: tests - main", from_addr="Edu <notifications@github.com>", body=body, auth={"spf": True, "dkim": True, "dmarc": True})
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(n, policy())

    assert state == "enqueued"
    assert job is not None
    assert job.action == "workflow_run_failed"
    assert job.work_intent == "work_allowed"
    assert job.work_key == "gisce/erp/actions/runs/26325244472"
    assert job.context.target_kind == "workflow_run"


def test_duplicate_enqueue_does_not_recapture_feedback(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", lambda *args: captured.append(args) or True)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())

    assert len(captured) == 1


def test_enqueue_skips_feedback_when_policy_disables_it(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", lambda *args: captured.append(args) or True)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), Policy(trusted_orgs={"gisce"}, feedback_learning=FeedbackLearning(enabled=False)))

    assert captured == []


def test_dismiss_blocked_job_marks_done(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    job, _ = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.finish(job.id, "blocked", "boom", "details")

    assert q.dismiss(job.id, "already answered") is True
    stored = q.get(job.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.last_error is None


def test_unlock_stale_can_limit_to_selected_running_jobs(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    job1, _ = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.claim_next("worker")
    job2, _ = q.enqueue(notif(2, "<2@github.com>", BODY_OTHER), policy())
    q.claim_next("worker")

    with q.connect() as con:
        con.execute("UPDATE jobs SET started_at='2000-01-01T00:00:00Z', updated_at='2000-01-01T00:00:00Z'")

    assert q.unlock_stale(older_than_seconds=1, job_ids=[job2.id]) == 1

    assert q.get(job1.id).status == "running"
    assert q.get(job2.id).status == "pending"
