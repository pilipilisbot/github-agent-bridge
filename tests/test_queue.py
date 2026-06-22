from github_agent_bridge.models import Notification
from github_agent_bridge.intent_classifier import IntentClassification
from github_agent_bridge.policy import FeedbackLearning, IntentClassifier, Policy
from github_agent_bridge.queue import JobQueue

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
