import imaplib
import sqlite3

import pytest

from github_agent_bridge.reader import ImapConfig, ImapReader
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


class QueueStub:
    def get_state(self, key, default=None):
        return default


class AbortOnSelect:
    def __init__(self, *args):
        self.logged_out = False

    def login(self, username, password):
        return "OK", []

    def select(self, mailbox):
        raise imaplib.IMAP4.abort("command: SELECT => socket error: EOF")

    def logout(self):
        self.logged_out = True


class EmptyMailbox:
    def __init__(self, *args):
        self.logged_out = False

    def login(self, username, password):
        return "OK", []

    def select(self, mailbox):
        return "OK", []

    def uid(self, command, *args):
        assert command == "search"
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


class MailboxWithMessages:
    def __init__(self, messages):
        self.messages = messages
        self.logged_out = False
        self.stores = []

    def login(self, username, password):
        return "OK", []

    def select(self, mailbox):
        return "OK", []

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(str(uid).encode("ascii") for uid in self.messages)]
        if command == "fetch":
            uid = int(args[0])
            return "OK", [(None, self.messages[uid])]
        if command == "store":
            self.stores.append(args)
            return "OK", []
        raise AssertionError(f"unexpected IMAP uid command {command}")

    def logout(self):
        self.logged_out = True


def make_reader():
    config = ImapConfig("imap.example.com", 993, "bot@example.com", "secret")
    return ImapReader(config, QueueStub(), object())


def test_fetch_once_reconnects_after_imap_abort(monkeypatch):
    connections = [AbortOnSelect(), EmptyMailbox()]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *args: connections.pop(0))

    assert make_reader().fetch_once() == 0
    assert connections == []


def test_fetch_once_raises_after_second_imap_abort(monkeypatch):
    connections = [AbortOnSelect(), AbortOnSelect()]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *args: connections.pop(0))

    with pytest.raises(imaplib.IMAP4.abort, match="socket error: EOF"):
        make_reader().fetch_once()

    assert connections == []


def github_message(message_id, body):
    return (
        "From: GitHub <notifications@github.com>\r\n"
        f"Message-ID: {message_id}\r\n"
        "Subject: Re: [gisce/erp] issue\r\n"
        "Authentication-Results: mx.example; spf=pass dkim=pass dmarc=pass\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def test_fetch_once_quarantines_poison_github_notification_and_continues(monkeypatch, tmp_path):
    db = tmp_path / "bridge.sqlite3"
    queue = JobQueue(db)
    mailbox = MailboxWithMessages({
        1: github_message("<bad@github.com>", "BROKEN"),
        2: github_message(
            "<good@github.com>",
            "@pilipilisbot https://github.com/gisce/erp/issues/42#issuecomment-99",
        ),
    })

    def extract_context_or_fail(body):
        if "BROKEN" in body:
            raise ValueError("missing GitHub context")
        from github_agent_bridge.parser import extract_github_context

        return extract_github_context(body)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *args: mailbox)
    monkeypatch.setattr("github_agent_bridge.queue.extract_github_context", extract_context_or_fail)
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)

    reader = ImapReader(
        ImapConfig("imap.example.com", 993, "bot@example.com", "secret"),
        queue,
        Policy(trusted_orgs={"gisce"}, bot_logins={"pilipilisbot"}),
    )

    assert reader.fetch_once() == 2
    assert queue.get_state("last_uid") == "2"
    assert queue.stats()["pending"] == 1
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        quarantine = con.execute("SELECT * FROM quarantined_notifications").fetchone()
        job = con.execute("SELECT * FROM jobs").fetchone()

    assert quarantine["uid"] == 1
    assert quarantine["message_id"] == "<bad@github.com>"
    assert quarantine["reason"] == "ingestion_error"
    assert "missing GitHub context" in quarantine["error"]
    assert "BROKEN" in quarantine["body_excerpt"]
    assert job["uid"] == 2
