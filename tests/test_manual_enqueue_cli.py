import json
from email.message import EmailMessage

import pytest

from github_agent_bridge.cli import _parse_github_comment_url, msg_to_notification, notification_from_comment_url, parse_work_intents
from github_agent_bridge.parser import extract_github_context


def test_parse_github_comment_url():
    assert _parse_github_comment_url("https://github.com/gisce/erp/pull/27675#issuecomment-4419572864") == ("gisce/erp", 27675, 4419572864)


def test_parse_work_intents_accepts_repeated_and_comma_values():
    assert parse_work_intents(["review_only,work_allowed"]) == frozenset({"review_only", "work_allowed"})
    assert parse_work_intents(["review_only", "work_allowed"]) == frozenset({"review_only", "work_allowed"})
    assert parse_work_intents(["all"]) is None
    assert parse_work_intents(None) is None


def test_parse_work_intents_rejects_unknown_values():
    with pytest.raises(SystemExit):
        parse_work_intents(["review_only,unknown"])


def test_notification_from_comment_url_builds_bridge_notification(monkeypatch):
    def fake_gh(args, gh_bin="gh"):
        if args == ["api", "repos/gisce/erp/issues/comments/4419572864"]:
            return {
                "html_url": "https://github.com/gisce/erp/pull/27675#issuecomment-4419572864",
                "body": "@pilipilisbot pots mirar això?",
                "user": {"login": "ecarreras"},
            }
        if args == ["api", "repos/gisce/erp/issues/27675"]:
            return {"title": "Eliminar descarga de módulos remotos", "pull_request": {}}
        raise AssertionError(args)

    monkeypatch.setattr("github_agent_bridge.cli._run_gh_json", fake_gh)

    n = notification_from_comment_url("https://github.com/gisce/erp/pull/27675#issuecomment-4419572864")
    ctx = extract_github_context(n.body)

    assert n.message_id == "<manual/gisce/erp/issues/27675/c4419572864@github.com>"
    assert n.subject == "Re: [gisce/erp] Eliminar descarga de módulos remotos (PR #27675)"
    assert n.from_addr == "ecarreras <notifications@github.com>"
    assert n.auth == {"spf": True, "dkim": True, "dmarc": True}
    assert ctx.repo == "gisce/erp"
    assert ctx.issue_number == 27675
    assert ctx.comment_id == 4419572864


def test_giscebot_mention_classifies_as_reply_comment():
    from github_agent_bridge.parser import classify_github_action

    body = "@giscebot pots mirar això?\nhttps://github.com/gisce/erp/pull/27675#issuecomment-4419572864"

    assert classify_github_action("Re: [gisce/erp] Example (PR #27675)", body, {"giscebot"}) == "reply_comment"


def test_msg_to_notification_accepts_google_group_rewritten_github_mail():
    msg = EmailMessage()
    msg["From"] = "'Eduard Carreras' via GISCE Bot <giscebot@gisce.net>"
    msg["Reply-To"] = "gisce/erp <reply+abc@reply.github.com>"
    msg["Message-ID"] = "<gisce/erp/pull/27853/c4547966148@github.com>"
    msg["Subject"] = "Re: [gisce/erp] Example (PR #27853)"
    msg["X-GitHub-Recipient"] = "giscebot"
    msg["X-GitHub-Reason"] = "mention"
    msg.set_content("https://github.com/gisce/erp/pull/27853#issuecomment-4547966148")

    n = msg_to_notification(msg, uid=6)

    assert n is not None
    assert n.uid == 6
    assert n.from_addr == "'Eduard Carreras' via GISCE Bot <giscebot@gisce.net>"
