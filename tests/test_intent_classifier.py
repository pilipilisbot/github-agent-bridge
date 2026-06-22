import json
import subprocess

from github_agent_bridge.intent_classifier import (
    ParserResult,
    classify_notification_with_llm,
    intent_session_id,
)
from github_agent_bridge.models import Notification
from github_agent_bridge.parser import extract_github_context
from github_agent_bridge.policy import IntentClassifier


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
                                        "action": "reply_comment",
                                        "work_intent": "work_allowed",
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
