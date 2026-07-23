import sqlite3

from github_agent_bridge.models import Notification
from github_agent_bridge.intent_classifier import IntentClassification
from github_agent_bridge.policy import FeedbackLearning, IntentClassifier, Policy
from github_agent_bridge.queue import JobQueue, SCHEMA

BODY1 = "@pilipilisbot one https://github.com/gisce/erp/pull/1#issuecomment-10"
BODY2 = "@pilipilisbot two https://github.com/gisce/erp/pull/1#issuecomment-11"
BODY_OTHER = "@pilipilisbot other https://github.com/gisce/erp/pull/2#issuecomment-12"


def notif(uid, mid, body):
    return Notification(uid=uid, message_id=mid, subject="Re: [gisce/erp] PR", from_addr="Edu <notifications@github.com>", body=body, auth={"spf": True, "dkim": True, "dmarc": True})


def policy():
    return Policy(trusted_orgs={"gisce"}, bot_logins={"pilipilisbot"})


def intent_policy(**kwargs):
    return Policy(
        trusted_orgs={"gisce"},
        bot_logins={"pilipilisbot"},
        intent_classifier=IntentClassifier(enabled=True, model="gpt-5.4-mini", **kwargs),
    )


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

        return TriggerActor(login="ecarreras", avatar_url="https://avatars.githubusercontent.com/u/294235?v=4", user_id=294235)

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
    assert job.metadata["trigger_actor_id"] == 294235


def test_enqueue_accepts_github_app_bot_actor_from_context(tmp_path, monkeypatch):
    def fake_actor(ctx, *, gh_bin="gh"):
        from github_agent_bridge.actors import TriggerActor

        return TriggerActor(
            login="copilot-pull-request-reviewer[bot]",
            avatar_url="https://avatars.githubusercontent.com/in/946600?v=4",
        )

    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", fake_actor)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] PR",
            from_addr="GitHub <notifications@github.com>",
            body="https://github.com/gisce/erp/pull/1#pullrequestreview-99",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        policy(),
    )

    assert state == "enqueued"
    assert job.trigger_actor == "copilot-pull-request-reviewer[bot]"
    assert job.trigger_actor_avatar_url == "https://avatars.githubusercontent.com/in/946600?v=4"


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


def test_claim_can_filter_by_work_intent(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    work_job, _ = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    review_job, _ = q.enqueue(notif(3, "<3@github.com>", BODY_OTHER), policy())
    q.update_work_intent(work_job.id, "work_allowed", "explicit implementation request")
    q.update_work_intent(review_job.id, "review_only", "review-only request")

    claimed = q.claim_next("review-worker", {"review_only"})

    assert claimed.id == review_job.id
    assert claimed.work_intent == "review_only"


def test_claim_fresh_review_retry_records_attempt_session_id(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    job, _ = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.update_work_intent(job.id, "review_only", "review-only request")

    first = q.claim_next("review-worker")
    assert first.metadata["openclaw_session_id"] == f"github-agent-bridge-job-{job.id}"
    assert q.requeue_running(job.id, "compaction failed", fresh_session=True) is True

    retry = q.claim_next("review-worker")

    assert retry.attempts == 2
    assert retry.metadata["openclaw_session_id"] == f"github-agent-bridge-job-{job.id}-attempt-2"


def test_claim_filter_preserves_running_work_key_guard(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())

    running = q.claim_next("all-worker")
    followup_job, state = q.enqueue(notif(2, "<2@github.com>", BODY2), policy())

    assert running.work_key == "gisce/erp#1"
    assert state == "enqueued"
    assert followup_job.work_key == running.work_key
    assert q.claim_next("review-worker", {"review_only"}) is None
    assert q.claim_next("review-worker", set()) is None


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

    def fake_capture(db_path, n, ctx, action, decision, work_intent, **kwargs):
        captured.append((db_path.name, n.message_id, ctx.work_key, action, decision, work_intent, kwargs))
        return True

    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", fake_capture)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())

    assert captured == [
        (
            "q.sqlite3",
            "<1@github.com>",
            "gisce/erp#1",
            "reply_comment",
            "auto_trusted",
            "review_only",
            {"trigger_actor": "Edu", "trigger_actor_avatar_url": "https://github.com/Edu.png?size=80"},
        )
    ]


def test_enqueue_can_apply_enabled_llm_intent_classifier(tmp_path, monkeypatch):
    calls = []

    def fake_classify(n, ctx, parser_result, cfg, **kwargs):
        calls.append((parser_result.action, parser_result.work_intent, cfg.model, kwargs))
        return IntentClassification(
            action="reply_comment",
            work_intent="work_allowed",
            confidence=0.91,
            reason="User asks to create a test.",
            applied=True,
            addressed_to_agent=True,
            write_permission="state_change_allowed",
            scope="Create a test.",
        )

    monkeypatch.setattr("github_agent_bridge.queue.classify_notification_with_llm", fake_classify)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        notif(
            1,
            "<1@github.com>",
            "@pilipilisbot crea un test https://github.com/gisce/erp/pull/1#issuecomment-10",
        ),
        intent_policy(),
    )

    assert state == "enqueued"
    assert job.action == "reply_comment"
    assert job.work_intent == "work_allowed"
    assert calls[0][0:3] == ("reply_comment", "review_only", "gpt-5.4-mini")
    assert job.metadata["intent_classifier"]["llm"]["applied"] is True
    assert job.metadata["intent_classifier"]["parser"] == {"action": "reply_comment", "work_intent": "review_only"}


def test_enqueue_can_apply_llm_intent_classifier_to_review_comments(tmp_path, monkeypatch):
    calls = []

    def fake_classify(n, ctx, parser_result, cfg, **kwargs):
        calls.append(ctx.target_kind)
        return IntentClassification(
            action="reply_comment",
            work_intent="work_allowed",
            confidence=0.91,
            reason="User asks for an implementation change from a review comment.",
            applied=True,
            addressed_to_agent=True,
            write_permission="state_change_allowed",
            scope="Create a test.",
        )

    monkeypatch.setattr("github_agent_bridge.queue.classify_notification_with_llm", fake_classify)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        notif(
            1,
            "<1@github.com>",
            "@pilipilisbot crea un test https://github.com/gisce/erp/pull/1#discussion_r3195891007",
        ),
        intent_policy(),
    )

    assert state == "enqueued"
    assert calls == ["review_comment"]
    assert job.work_intent == "work_allowed"


def test_enqueue_falls_back_when_llm_intent_confidence_is_low(tmp_path, monkeypatch):
    def fake_classify(n, ctx, parser_result, cfg, **kwargs):
        return IntentClassification(
            action="reply_comment",
            work_intent="work_allowed",
            confidence=0.4,
            reason="Unsure.",
            applied=False,
            addressed_to_agent=True,
            write_permission="state_change_allowed",
        )

    monkeypatch.setattr("github_agent_bridge.queue.classify_notification_with_llm", fake_classify)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        notif(
            1,
            "<1@github.com>",
            "@pilipilisbot crea un test https://github.com/gisce/erp/pull/1#issuecomment-10",
        ),
        intent_policy(min_confidence=0.75),
    )

    assert state == "enqueued"
    assert job.action == "reply_comment"
    assert job.work_intent == "review_only"
    assert job.metadata["intent_classifier"]["llm"]["applied"] is False


def test_enqueue_falls_back_when_llm_intent_classifier_errors(tmp_path, monkeypatch):
    def fake_classify(n, ctx, parser_result, cfg, **kwargs):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr("github_agent_bridge.queue.classify_notification_with_llm", fake_classify)
    q = JobQueue(tmp_path / "q.sqlite3")

    job, state = q.enqueue(
        notif(
            1,
            "<1@github.com>",
            "@pilipilisbot crea un test https://github.com/gisce/erp/pull/1#issuecomment-10",
        ),
        intent_policy(),
    )

    assert state == "enqueued"
    assert job.action == "reply_comment"
    assert job.work_intent == "review_only"
    assert "classifier unavailable" in job.metadata["intent_classifier"]["error"]


def test_enqueue_skips_llm_intent_classifier_when_disabled(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("github_agent_bridge.queue.classify_notification_with_llm", lambda *args, **kwargs: calls.append(args) or None)
    q = JobQueue(tmp_path / "q.sqlite3")

    q.enqueue(
        notif(
            1,
            "<1@github.com>",
            "@pilipilisbot crea un test https://github.com/gisce/erp/pull/1#issuecomment-10",
        ),
        policy(),
    )

    assert calls == []


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
    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", lambda *args, **kwargs: captured.append((args, kwargs)) or True)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.enqueue(notif(1, "<1@github.com>", BODY1), policy())

    assert len(captured) == 1


def test_enqueue_skips_feedback_when_policy_disables_it(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr("github_agent_bridge.feedback.capture_feedback", lambda *args, **kwargs: captured.append((args, kwargs)) or True)

    q = JobQueue(tmp_path / "q.sqlite3")
    q.enqueue(notif(1, "<1@github.com>", BODY1), Policy(trusted_orgs={"gisce"}, feedback_learning=FeedbackLearning(enabled=False)))

    assert captured == []


def test_dismiss_blocked_job_marks_done(tmp_path):
    q = JobQueue(tmp_path / "q.sqlite3")
    job, _ = q.enqueue(notif(1, "<1@github.com>", BODY1), policy())
    q.finish(job.id, "blocked", "boom", "details")
    with q.connect() as con:
        finished_at = con.execute("SELECT finished_at FROM jobs WHERE id=?", (job.id,)).fetchone()["finished_at"]

    assert q.dismiss(job.id, "already answered") is True
    stored = q.get(job.id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.last_error is None
    with q.connect() as con:
        assert con.execute("SELECT finished_at FROM jobs WHERE id=?", (job.id,)).fetchone()["finished_at"] == finished_at


def test_init_migrates_legacy_jobs_constraint_to_failed_status(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    legacy_schema = SCHEMA.replace("'done','failed','blocked'", "'done','blocked'")
    with sqlite3.connect(db) as con:
        con.executescript(legacy_schema)
        con.execute(
            """INSERT INTO jobs(
                id,work_key,repo,thread,status,action,decision,work_intent,subject,
                message_id,context_json,metadata_json,created_at,updated_at
            ) VALUES(1,'gisce/erp#1','gisce/erp',1,'blocked','reply_comment',
                'auto_trusted','work_allowed','subject','<legacy@github.com>',
                '{"urls":[],"repo":"gisce/erp","issue_number":1}','{}',
                '2026-07-23T00:00:00Z','2026-07-23T00:00:00Z')"""
        )
        con.execute(
            """INSERT INTO job_session_events(
                ts,job_id,work_key,session_id,event_type,summary,detail
            ) VALUES('2026-07-23T00:00:00Z',1,'gisce/erp#1','legacy-session',
                'blocked','legacy event',NULL)"""
        )

    q = JobQueue(db)
    q.finish(1, "failed", "dispatch failed after visible result", "403 during compaction")

    stored = q.get(1)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.last_error == "403 during compaction"
    with q.connect() as con:
        table_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()["sql"]
        assert "'failed'" in table_sql
        assert con.execute("SELECT COUNT(*) FROM job_session_events WHERE job_id=1").fetchone()[0] == 2
        assert list(con.execute("PRAGMA foreign_key_check")) == []


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
