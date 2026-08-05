import io
import json
import sqlite3

from github_agent_bridge.cli import main
from github_agent_bridge import feedback
from github_agent_bridge.mcp import authenticate_token, create_token, list_tokens, revoke_token, serve_stdio, update_token_owner
from github_agent_bridge.queue import JobQueue


def _mcp_frame(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _mcp_responses(output):
    data = output.getvalue()
    responses = []
    while data:
        raw_headers, data = data.split(b"\r\n\r\n", 1)
        length = None
        for line in raw_headers.decode("ascii").splitlines():
            name, _, value = line.partition(":")
            if name.lower() == "content-length":
                length = int(value.strip())
        assert length is not None
        body, data = data[:length], data[length:]
        responses.append(json.loads(body.decode("utf-8")))
    return responses


def test_mcp_tokens_are_hashed_authenticated_and_revocable(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    created = create_token(db, "local agent", user_login="Alice", created_by="Admin")
    token = created["token"]
    records = list_tokens(db)

    assert token.startswith("gab_mcp_")
    assert records == [created["record"]]
    assert records[0]["user_login"] == "alice"
    assert records[0]["created_by"] == "admin"
    assert list_tokens(db, user_login="bob") == []
    assert list_tokens(db, user_login="alice") == [created["record"]]
    assert authenticate_token(db, token)["id"] == created["record"]["id"]
    assert revoke_token(db, created["record"]["id"]) is True
    assert authenticate_token(db, token) is None
    assert list_tokens(db) == []
    assert list_tokens(db, include_revoked=True)[0]["revoked_at"]


def test_existing_mcp_tokens_table_is_upgraded_before_user_index(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE mcp_tokens (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          last_used_at TEXT,
          revoked_at TEXT,
          expires_at TEXT
        );
        """
    )
    con.close()

    JobQueue(db)

    con = sqlite3.connect(db)
    columns = {row[1] for row in con.execute("PRAGMA table_info(mcp_tokens)")}
    indexes = {row[1] for row in con.execute("PRAGMA index_list(mcp_tokens)")}
    con.close()
    assert {"user_login", "created_by"}.issubset(columns)
    assert "idx_mcp_tokens_user" in indexes


def test_mcp_token_revoke_can_be_scoped_to_owner(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    created = create_token(db, "alice agent", user_login="alice")

    assert revoke_token(db, created["record"]["id"], user_login="bob") is False
    assert authenticate_token(db, created["token"]) is not None
    assert revoke_token(db, created["record"]["id"], user_login="alice") is True


def test_mcp_token_owner_can_be_updated(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    created = create_token(db, "legacy agent")
    updated = update_token_owner(db, created["record"]["id"], "Alice")
    listed = list_tokens(db, user_login="alice")
    revoked = revoke_token(db, created["record"]["id"], user_login="alice")
    missing_after_revoke = update_token_owner(db, created["record"]["id"], "bob")

    assert updated is not None
    assert updated["user_login"] == "alice"
    assert listed[0]["id"] == created["record"]["id"]
    assert revoked is True
    assert missing_after_revoke is None


def test_stdio_mcp_lists_applicable_knowledge(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)
    feedback.add_rule(db, "global", "operating_rule", "Global bridge rule.", 0.9)
    feedback.add_rule(db, "org:gisce", "style_preference", "Org bridge rule.", 0.8)
    feedback.add_rule(db, "repo:gisce/erp", "technical_criterion", "Repo bridge rule.", 0.7)
    feedback.add_rule(db, "repo:other/project", "technical_criterion", "Other bridge rule.", 0.9)
    output = io.BytesIO()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_repositories", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_knowledge", "arguments": {"repo": "gisce/erp", "min_confidence": 0.75}}},
    ]

    serve_stdio(db, stdin=io.BytesIO(b"".join(_mcp_frame(item) for item in requests)), stdout=output)
    responses = _mcp_responses(output)
    repositories = json.loads(responses[1]["result"]["content"][0]["text"])
    knowledge = json.loads(responses[2]["result"]["content"][0]["text"])

    assert responses[0]["result"]["serverInfo"]["name"] == "github-agent-bridge"
    assert repositories["repositories"] == ["gisce/erp", "other/project"]
    assert {rule["rule"] for rule in knowledge["rules"]} == {"Global bridge rule.", "Org bridge rule."}


def test_cli_mcp_serve_rejects_invalid_token(tmp_path, capsys):
    db = tmp_path / "bridge.sqlite3"

    status = main(["--db", str(db), "mcp-serve", "--token", "bad-token"])

    assert status == 2
    assert "invalid MCP token" in capsys.readouterr().err
