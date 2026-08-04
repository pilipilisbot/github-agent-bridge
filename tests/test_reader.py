import imaplib

import pytest

from github_agent_bridge.reader import ImapConfig, ImapReader


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
