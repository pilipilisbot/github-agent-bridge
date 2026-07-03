import json
import subprocess

from github_agent_bridge.intent_classifier import (
    ParserResult,
    build_intent_prompt,
    classify_notification_with_llm,
    intent_session_id,
    normalize_result,
)
from github_agent_bridge.models import Notification
from github_agent_bridge.parser import extract_github_context
from github_agent_bridge.policy import IntentClassifier, Policy


def notification(message_id, body):
    return Notification(
        uid=1,
        message_id=message_id,
        subject="Re: [gisce/erp] PR",
        from_addr="Edu <notifications@github.com>",
        body=body,
        auth={"spf": True, "dkim": True, "dmarc": True},
    )


def test_intent_session_id_keeps_base_and_agent_but_isolates_events():
    first = notification("<1@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10")
    second = notification("<2@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-11")

    first_id = intent_session_id("intent-base", first, extract_github_context(first.body), "gisce developer")
    second_id = intent_session_id("intent-base", second, extract_github_context(second.body), "gisce developer")

    assert first_id.startswith("intent-base-gisce-developer-")
    assert second_id.startswith("intent-base-gisce-developer-")
    assert first_id != second_id


def test_build_intent_prompt_allows_literal_json_braces_in_template():
    notif = notification("<1@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10")

    prompt = build_intent_prompt(
        notif,
        extract_github_context(notif.body),
        ParserResult("reply_comment", "review_only"),
        prompt_template='Return JSON like {"action": "reply_comment"}\nEvent:\n{event_json}\n',
    )

    assert 'Return JSON like {"action": "reply_comment"}' in prompt
    assert "{event_json}" not in prompt
    assert '"message_id": "<1@github.com>"' in prompt


def test_packaged_intent_prompt_builds_without_formatting_json_example():
    notif = notification("<1@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10")

    prompt = build_intent_prompt(
        notif,
        extract_github_context(notif.body),
        ParserResult("reply_comment", "review_only"),
    )

    assert '"action": "reply_comment"' in prompt
    assert '"message_id": "<1@github.com>"' in prompt


def test_packaged_intent_prompt_requires_semantic_decomposition():
    notif = notification(
        "<1@github.com>",
        "@giscebot fes una pull request per cada notificació (email). "
        "Així serà més fàcil revisar-ho i integrar-ho",
    )

    prompt = build_intent_prompt(
        notif,
        extract_github_context(notif.body),
        ParserResult("reply_comment", "review_only"),
    )

    assert '"main_request"' in prompt
    assert '"subordinate_reason"' in prompt
    assert '"agent_identity"' in prompt
    assert '"github_logins": ["pilipilisbot"]' in prompt
    assert '"addressed_to_agent"' in prompt
    assert '"write_permission"' in prompt
    assert "Classify from the main request" in prompt
    assert "regardless of language" in prompt
    assert "parser_result" in prompt


def test_build_intent_prompt_uses_configured_agent_identity():
    notif = notification("<1@github.com>", "@acme-agent please review https://github.com/gisce/erp/pull/1#issuecomment-10")

    prompt = build_intent_prompt(
        notif,
        extract_github_context(notif.body),
        ParserResult("reply_comment", "review_only"),
        policy=Policy(bot_logins={"acme-agent"}),
        agent="erp-reviewer",
    )

    assert '"github_logins": ["acme-agent"]' in prompt
    assert '"openclaw_agent": "erp-reviewer"' in prompt
    assert "giscebot" not in prompt


def test_normalize_result_preserves_semantic_decomposition_metadata():
    result = normalize_result(
        {
            "addressed_to_agent": True,
            "action": "reply_comment",
            "work_intent": "work_allowed",
            "write_permission": "state_change_allowed",
            "scope": "Create one PR per notification.",
            "main_request": "Fer una pull request separada per cada notificació.",
            "subordinate_reason": "Així serà més fàcil revisar-ho i integrar-ho.",
            "confidence": 0.98,
            "reason": "The main request asks for repository work.",
        },
        0.75,
    )

    assert result.applied is True
    assert result.to_metadata()["addressed_to_agent"] is True
    assert result.to_metadata()["write_permission"] == "state_change_allowed"
    assert result.to_metadata()["scope"] == "Create one PR per notification."
    assert result.to_metadata()["main_request"] == "Fer una pull request separada per cada notificació."
    assert result.to_metadata()["subordinate_reason"] == "Així serà més fàcil revisar-ho i integrar-ho."


def test_normalize_result_downgrades_unaddressed_events_to_archive():
    result = normalize_result(
        {
            "addressed_to_agent": "false",
            "action": "reply_comment",
            "work_intent": "work_allowed",
            "write_permission": "state_change_allowed",
            "confidence": 0.95,
            "reason": "Copilot suggested a fix but did not address the configured agent.",
        },
        0.75,
    )

    assert result.applied is True
    assert result.action == "archive_notification"
    assert result.work_intent == "review_only"
    assert result.write_permission == "none"


def test_normalize_result_requires_write_permission_for_work_allowed():
    result = normalize_result(
        {
            "addressed_to_agent": True,
            "action": "reply_comment",
            "work_intent": "work_allowed",
            "write_permission": "none",
            "confidence": 0.95,
            "reason": "The request is addressed to the agent but only asks for review.",
        },
        0.75,
    )

    assert result.applied is True
    assert result.action == "reply_comment"
    assert result.work_intent == "review_only"


def test_classify_notification_with_llm_uses_isolated_session_id(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "result": {
                        "payloads": [
                            {
                                "text": json.dumps(
                                    {
                                        "addressed_to_agent": True,
                                        "action": "reply_comment",
                                        "work_intent": "work_allowed",
                                        "write_permission": "state_change_allowed",
                                        "confidence": 0.92,
                                        "reason": "User asked for implementation.",
                                    }
                                )
                            }
                        ]
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = IntentClassifier(openclaw_bin="/tmp/openclaw", session_id="intent-base", timeout=5)
    first = notification("<1@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10")
    second = notification("<2@github.com>", "@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-11")

    for notif in (first, second):
        classify_notification_with_llm(
            notif,
            extract_github_context(notif.body),
            ParserResult("reply_comment", "review_only"),
            cfg,
            agent="gisce-developer",
            prompt_template="Event JSON:\n{event_json}\n",
        )

    session_ids = [cmd[cmd.index("--session-id") + 1] for cmd in calls]
    assert session_ids[0].startswith("intent-base-gisce-developer-")
    assert session_ids[1].startswith("intent-base-gisce-developer-")
    assert session_ids[0] != session_ids[1]
