import sqlite3

import pytest

from github_agent_bridge import feedback
from github_agent_bridge.models import GitHubContext, Notification
from github_agent_bridge.policy import Policy, Route
from github_agent_bridge.queue import JobQueue


def notification(body: str | None = None):
    return Notification(
        uid=1,
        message_id="<1@github.com>",
        subject="Re: [gisce/erp] PR",
        from_addr="notifications@github.com",
        body=body or "Aquest comentari critica el comportament de l'agent. https://github.com/gisce/erp/pull/1#issuecomment-10",
    )


def context():
    return GitHubContext(["https://github.com/gisce/erp/pull/1#issuecomment-10"], "gisce/erp", 1, comment_id=10)


def test_capture_feedback_stores_candidate_event_without_synthesizing_rule(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    assert feedback.capture_feedback(
        db,
        notification(),
        context(),
        "reply_comment",
        "auto_trusted",
        "review_only",
        trigger_actor="ecarreras",
        trigger_actor_avatar_url="https://avatars.githubusercontent.com/u/294235?v=4",
    )

    with sqlite3.connect(db) as con:
        event = con.execute("SELECT scope, actor, context_json, classification, confidence, memorable FROM feedback_events").fetchone()
        rule_count = con.execute("SELECT count(*) FROM feedback_rules").fetchone()[0]

    assert event[0] == "repo:gisce/erp"
    assert event[1] == "ecarreras"
    assert '"github_context"' in event[2]
    assert '"source_url": "https://github.com/gisce/erp/pull/1#issuecomment-10"' in event[2]
    assert event[3:] == ("unreviewed", 0.0, 0)
    assert rule_count == 0


def test_list_events_enriches_legacy_feedback_from_job_source(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    n = notification()
    ctx = context()
    feedback.capture_feedback(db, n, ctx, "reply_comment", "auto_trusted", "review_only")
    with sqlite3.connect(db) as con:
        con.execute(
            """INSERT INTO jobs(
                work_key, repo, thread, status, action, decision, work_intent, subject, message_id,
                uid, trigger_actor, trigger_actor_avatar_url, context_json, metadata_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ctx.work_key,
                ctx.repo,
                ctx.issue_number,
                "pending",
                "reply_comment",
                "auto_trusted",
                "review_only",
                n.subject,
                n.message_id,
                n.uid,
                "ecarreras",
                "https://avatars.githubusercontent.com/u/294235?v=4",
                ctx.to_json(),
                "{}",
                "2026-06-04T12:00:00Z",
                "2026-06-04T12:00:00Z",
            ),
        )

    event = feedback.list_events(db, "repo:gisce/erp")[0]

    assert event["actor"] == "ecarreras"
    assert event["trigger_actor"] == "ecarreras"
    assert event["trigger_actor_avatar_url"] == "https://avatars.githubusercontent.com/u/294235?v=4"
    assert event["github_urls"] == ["https://github.com/gisce/erp/pull/1#issuecomment-10"]
    assert event["source_url"] == "https://github.com/gisce/erp/pull/1#issuecomment-10"
    assert event["source_job_id"] == 1
    assert event["source_table"] == "job"
    assert event["github_context"]["comment_id"] == 10


def test_capture_feedback_deduplicates_events(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    n = notification()

    feedback.capture_feedback(db, n, context(), "reply_comment", "auto_trusted", "review_only")
    feedback.capture_feedback(db, n, context(), "reply_comment", "auto_trusted", "review_only")

    assert len(feedback.list_events(db, "repo:gisce/erp")) == 1
    assert feedback.list_rules(db, "repo:gisce/erp") == []


def test_pending_events_retries_error_proposals(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    event = feedback.list_events(db, "repo:gisce/erp")[0]

    feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": False,
            "scope": event["scope"],
            "type": "error",
            "rule": "",
            "confidence": 0.0,
            "reason": "classification failed",
        },
        auto_approve_confidence=0.8,
        model="gpt-5.4-mini",
        error="temporary classifier failure",
    )

    assert [e["id"] for e in feedback.pending_events(db)] == [event["id"]]


def test_pending_events_skips_non_error_proposals(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    event = feedback.list_events(db, "repo:gisce/erp")[0]

    feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": False,
            "scope": event["scope"],
            "type": "domain_context",
            "rule": "",
            "confidence": 0.2,
            "reason": "Only about this PR.",
        },
        auto_approve_confidence=0.8,
    )

    assert feedback.pending_events(db) == []


def test_capture_feedback_ignores_non_actionable_decisions(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    assert feedback.capture_feedback(db, notification(), context(), "archive_notification", "auto", "work_allowed") is False
    assert feedback.list_events(db) == []


def test_add_rule_creates_curated_agent_rule(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    event_id = feedback.list_events(db, "repo:gisce/erp")[0]["id"]

    rule = feedback.add_rule(
        db,
        scope="repo:gisce/erp",
        rule_type="style_preference",
        rule="Avoid generic acknowledgements; answer with concrete repo-specific evidence.",
        confidence=0.8,
        source_events=[event_id],
    )

    assert rule["scope"] == "repo:gisce/erp"
    assert rule["type"] == "style_preference"
    assert rule["confidence"] == 0.8
    assert rule["source_events"] == [event_id]
    assert rule["source_event_details"][0]["id"] == event_id
    assert rule["source_event_details"][0]["source_url"] == "https://github.com/gisce/erp/pull/1#issuecomment-10"
    assert feedback.list_rules(db, "repo:gisce/erp", min_confidence=0.75) == [rule]
    assert feedback.list_rules(db, "repo:gisce/erp", min_confidence=0.95) == []


def test_list_rules_orders_newest_seen_first(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    with sqlite3.connect(db) as con:
        con.executemany(
            """INSERT INTO feedback_rules(
                id, scope, type, confidence, rule, created_at, last_seen, source_events_json, observations
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "older",
                    "repo:gisce/erp",
                    "style_preference",
                    0.8,
                    "Older rule",
                    "2026-06-01T09:00:00Z",
                    "2026-06-01T10:00:00Z",
                    "[]",
                    1,
                ),
                (
                    "newer",
                    "repo:gisce/erp",
                    "style_preference",
                    0.8,
                    "Newer rule",
                    "2026-06-02T09:00:00Z",
                    "2026-06-03T10:00:00Z",
                    "[]",
                    1,
                ),
                (
                    "same-last-seen-newer-created",
                    "repo:gisce/erp",
                    "style_preference",
                    0.8,
                    "Same last seen but newer creation",
                    "2026-06-03T09:00:00Z",
                    "2026-06-03T10:00:00Z",
                    "[]",
                    1,
                ),
            ],
        )

    rules = feedback.list_rules(db, "repo:gisce/erp", min_confidence=0)

    assert [rule["id"] for rule in rules] == ["same-last-seen-newer-created", "newer", "older"]


def test_add_rule_rejects_invalid_confidence(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    with pytest.raises(ValueError, match="confidence"):
        feedback.add_rule(db, "repo:gisce/erp", "style_preference", "Rule", 1.7)


def test_knowledge_moderation_approves_rejects_and_deletes_rules(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    event = feedback.list_events(db, "repo:gisce/erp")[0]
    proposed = feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": "repo:gisce/erp",
            "type": "style_preference",
            "rule": "Prefer compact feedback in GitHub follow-ups.",
            "confidence": 0.7,
            "reason": "Useful recurring style correction.",
        },
        auto_approve_confidence=0.9,
    )

    rejected = feedback.reject_proposal(db, proposed["id"])
    approved = feedback.approve_proposal(db, proposed["id"])
    rules = feedback.list_rules(db, "repo:gisce/erp", min_confidence=0)

    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert approved is not None
    assert approved["status"] == "approved"
    assert len(rules) == 1
    assert feedback.list_repositories(db) == ["gisce/erp"]
    assert feedback.delete_rule(db, rules[0]["id"]) is True
    assert feedback.list_rules(db, "repo:gisce/erp", min_confidence=0) == []
    assert feedback.delete_rule(db, rules[0]["id"]) is False


def test_list_proposals_includes_source_event_details(tmp_path):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only", trigger_actor="ecarreras")
    event = feedback.list_events(db, "repo:gisce/erp")[0]
    proposed = feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": "repo:gisce/erp",
            "type": "technical_criterion",
            "rule": "Preserve backward compatibility for existing synchronous callers when introducing asynchronous flows.",
            "confidence": 0.74,
            "reason": "Useful recurring compatibility criterion.",
        },
        auto_approve_confidence=0.9,
    )

    listed = feedback.list_proposals(db, status="proposed")

    assert listed[0]["id"] == proposed["id"]
    assert listed[0]["source_event"]["id"] == event["id"]
    assert listed[0]["source_event"]["trigger_actor"] == "ecarreras"
    assert listed[0]["source_event"]["github_urls"] == ["https://github.com/gisce/erp/pull/1#issuecomment-10"]


def test_approve_proposal_can_react_to_origin_comment(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    event = feedback.list_events(db, "repo:gisce/erp")[0]
    proposed = feedback.store_proposal(
        db,
        {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": "repo:gisce/erp",
            "type": "style_preference",
            "rule": "Prefer compact feedback in GitHub follow-ups.",
            "confidence": 0.7,
            "reason": "Useful recurring style correction.",
        },
        auto_approve_confidence=0.9,
    )
    reacted = []
    monkeypatch.setattr(feedback, "react_to_feedback_event", lambda *args, **kwargs: reacted.append((args, kwargs)) or True)

    approved = feedback.approve_proposal(db, proposed["id"], react=True, gh_bin="gh-test")

    assert approved is not None
    assert approved["status"] == "approved"
    assert reacted == [((db, event["id"]), {"gh_bin": "gh-test"})]


def test_openclaw_json_payload_text_is_extracted():
    raw = '{"result":{"payloads":[{"text":"{\\\"is_feedback\\\":false,\\\"scope\\\":\\\"global\\\",\\\"type\\\":\\\"domain_context\\\",\\\"rule\\\":\\\"\\\",\\\"confidence\\\":0,\\\"reason\\\":\\\"shape test\\\"}"}]}}'

    assert feedback._extract_json_object(feedback._openclaw_text_from_json(raw))["reason"] == "shape test"


def test_learning_prompt_uses_packaged_prompt_resource():
    event = {"id": "e1", "scope": "repo:gisce/erp", "comment": "Read AGENTS.md first"}

    prompt = feedback.build_learning_prompt(event)

    assert "# Feedback classifier prompt" in prompt
    assert "Event JSON:" in prompt
    assert "Read AGENTS.md first" in prompt
    assert "You are classifying GitHub agent feedback" in feedback.FEEDBACK_CLASSIFIER_PROMPT
    assert "feature requests, work orders, or product" in feedback.FEEDBACK_CLASSIFIER_PROMPT


def test_learning_prompt_accepts_policy_override_template():
    event = {"id": "e1", "scope": "repo:gisce/erp", "comment": "Read AGENTS.md first"}

    prompt = feedback.build_learning_prompt(event, "CUSTOM CLASSIFIER {event_json}\n")

    assert prompt.startswith("CUSTOM CLASSIFIER ")
    assert "Read AGENTS.md first" in prompt
    assert "# Feedback classifier prompt" not in prompt


def test_route_agent_for_event_uses_policy_route():
    event = {"id": "e1", "scope": "repo:gisce/erp"}
    policy = Policy(org_routes={"gisce": Route(agent="gisce-developer")})

    assert feedback.route_agent_for_event(event, policy) == "gisce-developer"
    assert feedback.route_agent_for_event({"id": "e2", "scope": "global"}, policy) is None


def test_classify_event_passes_route_agent_to_openclaw(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = '{"result":{"payloads":[{"text":"{\\"is_feedback\\":false,\\"scope\\":\\"repo:gisce/erp\\",\\"type\\":\\"domain_context\\",\\"rule\\":\\"\\",\\"confidence\\":0,\\"reason\\":\\"shape test\\"}"}]}}'
            stderr = ""

        return Result()

    monkeypatch.setattr(feedback.subprocess, "run", fake_run)

    feedback.classify_event_with_llm(
        {"id": "e1", "scope": "repo:gisce/erp", "comment": "Prefer repo routing"},
        openclaw_bin="openclaw",
        agent="gisce-developer",
        model="gpt-5.4-mini",
    )

    assert "--agent" in calls[0]
    assert calls[0][calls[0].index("--agent") + 1] == "gisce-developer"


def test_learn_from_events_auto_approves_high_confidence_feedback(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")

    def fake_classify(event, **kwargs):
        return {
            "event_id": event["id"],
            "is_feedback": True,
            "scope": event["scope"],
            "type": "operating_rule",
            "rule": "Read the repository guide before changing project architecture.",
            "confidence": 0.91,
            "reason": "The comment criticizes a process failure that can recur.",
        }

    monkeypatch.setattr(feedback, "classify_event_with_llm", fake_classify)
    monkeypatch.setattr(feedback, "react_to_feedback_comment", lambda *args, **kwargs: True)

    result = feedback.learn_from_events(db, limit=5, auto_approve_confidence=0.8)

    assert result["processed"] == 1
    assert result["approved"] == 1
    assert result["reacted"] == 1
    rules = feedback.list_rules(db, "repo:gisce/erp", min_confidence=0.8)
    assert len(rules) == 1
    assert rules[0]["rule"] == "Read the repository guide before changing project architecture."


def test_react_to_feedback_comment_posts_heart_to_origin_comment(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(feedback.subprocess, "run", fake_run)
    event = {
        "comment": "See https://github.com/gisce/erp/pull/1#issuecomment-10",
    }

    assert feedback.react_to_feedback_comment(event, gh_bin="gh") is True
    assert calls == [
        [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/gisce/erp/issues/comments/10/reactions",
            "-f",
            "content=heart",
            "-H",
            "Accept: application/vnd.github+json",
        ]
    ]


def test_learn_from_events_passes_policy_route_agent(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    captured = {}

    def fake_classify(event, **kwargs):
        captured["agent"] = kwargs.get("agent")
        captured["session_id"] = kwargs.get("session_id")
        return {
            "event_id": event["id"],
            "is_feedback": False,
            "scope": event["scope"],
            "type": "domain_context",
            "rule": "",
            "confidence": 0.2,
            "reason": "Only about this PR.",
        }

    monkeypatch.setattr(feedback, "classify_event_with_llm", fake_classify)
    policy = Policy(org_routes={"gisce": Route(agent="gisce-developer")})

    feedback.learn_from_events(db, policy=policy, limit=5, auto_approve_confidence=0.8)

    assert captured["agent"] == "gisce-developer"
    event = feedback.list_events(db, "repo:gisce/erp")[0]
    assert captured["session_id"] == feedback.session_id_for_event("github-agent-bridge-feedback", "gisce-developer", event["id"])


def test_learn_from_events_falls_back_when_model_override_is_not_allowed(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")
    seen_models = []

    def fake_classify(event, **kwargs):
        seen_models.append(kwargs.get("model"))
        if kwargs.get("model"):
            raise RuntimeError('GatewayClientRequestError: Error: Model override "openai/gpt-5.4-mini" is not allowed for agent "main".')
        return {
            "event_id": event["id"],
            "is_feedback": False,
            "scope": event["scope"],
            "type": "domain_context",
            "rule": "",
            "confidence": 0.2,
            "reason": "Only about this PR.",
        }

    monkeypatch.setattr(feedback, "classify_event_with_llm", fake_classify)

    result = feedback.learn_from_events(db, model="gpt-5.4-mini", limit=5, auto_approve_confidence=0.8)

    assert seen_models == ["gpt-5.4-mini", None]
    assert result["rejected"] == 1
    assert result["errors"] == 0
    assert feedback.list_proposals(db, status="rejected")[0]["model"] == ""


def test_learn_from_events_rejects_task_specific_comments(tmp_path, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    feedback.capture_feedback(db, notification(), context(), "reply_comment", "auto_trusted", "review_only")

    def fake_classify(event, **kwargs):
        return {
            "event_id": event["id"],
            "is_feedback": False,
            "scope": event["scope"],
            "type": "domain_context",
            "rule": "",
            "confidence": 0.2,
            "reason": "Only about this PR.",
        }

    monkeypatch.setattr(feedback, "classify_event_with_llm", fake_classify)

    result = feedback.learn_from_events(db, limit=5, auto_approve_confidence=0.8)

    assert result["processed"] == 1
    assert result["rejected"] == 1
    assert feedback.list_rules(db) == []
    assert feedback.list_proposals(db, status="rejected")[0]["reason"] == "Only about this PR."
