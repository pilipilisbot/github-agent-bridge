from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .feedback import list_applicable_rules, list_repositories
from .models import utc_now

TOKEN_PREFIX = "gab_mcp_"


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
        "expires_at": row["expires_at"],
    }


def create_token(db_path: str | Path, name: str, *, expires_at: str | None = None) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("token name is required")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = secrets.token_hex(8)
    now = utc_now()
    with _connect(db_path) as con:
        con.execute(
            """INSERT INTO mcp_tokens(id, name, token_hash, created_at, expires_at)
            VALUES(?,?,?,?,?)""",
            (token_id, clean_name, _hash_token(token), now, expires_at),
        )
    return {"token": token, "record": {"id": token_id, "name": clean_name, "created_at": now, "last_used_at": None, "revoked_at": None, "expires_at": expires_at}}


def list_tokens(db_path: str | Path, *, include_revoked: bool = False) -> list[dict[str, Any]]:
    clauses = []
    if not include_revoked:
        clauses.append("revoked_at IS NULL")
    sql = "SELECT id, name, created_at, last_used_at, revoked_at, expires_at FROM mcp_tokens"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    with _connect(db_path) as con:
        return [_token_dict(row) for row in con.execute(sql)]


def revoke_token(db_path: str | Path, token_id: str) -> bool:
    now = utc_now()
    with _connect(db_path) as con:
        cur = con.execute("UPDATE mcp_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now, token_id))
        return cur.rowcount > 0


def authenticate_token(db_path: str | Path, token: str) -> dict[str, Any] | None:
    token = token.strip()
    if not token:
        return None
    digest = _hash_token(token)
    now = utc_now()
    with _connect(db_path) as con:
        rows = con.execute(
            """SELECT id, name, created_at, last_used_at, revoked_at, expires_at
            FROM mcp_tokens
            WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)""",
            (now,),
        ).fetchall()
        for row in rows:
            stored_hash = con.execute("SELECT token_hash FROM mcp_tokens WHERE id=?", (row["id"],)).fetchone()["token_hash"]
            if hmac.compare_digest(stored_hash, digest):
                con.execute("UPDATE mcp_tokens SET last_used_at=? WHERE id=?", (now, row["id"]))
                return _token_dict(row)
    return None


def _tool_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


@dataclass
class MCPServer:
    db_path: str | Path

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        try:
            result = self._dispatch(method, params)
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "github-agent-bridge", "version": "0.1"},
                "capabilities": {"tools": {}, "resources": {}},
            }
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return {"tools": self._tools()}
        if method == "tools/call":
            return self._call_tool(str(params.get("name") or ""), params.get("arguments") if isinstance(params.get("arguments"), dict) else {})
        if method == "resources/list":
            return {
                "resources": [
                    {"uri": f"gab://knowledge/{repo}", "name": f"{repo} knowledge", "mimeType": "application/json"}
                    for repo in list_repositories(self.db_path)
                ]
            }
        if method == "resources/read":
            uri = str(params.get("uri") or "")
            prefix = "gab://knowledge/"
            if not uri.startswith(prefix):
                raise ValueError("unsupported resource uri")
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(self._knowledge(uri.removeprefix(prefix), 0.0), ensure_ascii=False, indent=2)}]}
        raise ValueError(f"unsupported MCP method: {method}")

    def _tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_repositories",
                "description": "List repositories that have acquired bridge knowledge.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "list_knowledge",
                "description": "List curated bridge knowledge rules applicable to a repository.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository name such as owner/name."},
                        "min_confidence": {"type": "number", "description": "Minimum confidence threshold."},
                    },
                    "required": ["repo"],
                    "additionalProperties": False,
                },
            },
        ]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_repositories":
            return _tool_result({"repositories": list_repositories(self.db_path)})
        if name == "list_knowledge":
            repo = str(arguments.get("repo") or "").strip().lower()
            if "/" not in repo:
                raise ValueError("repo must be owner/name")
            min_confidence = float(arguments.get("min_confidence", 0.5))
            return _tool_result(self._knowledge(repo, min_confidence))
        raise ValueError(f"unknown tool: {name}")

    def _knowledge(self, repo: str, min_confidence: float) -> dict[str, Any]:
        return {
            "repo": repo,
            "min_confidence": min_confidence,
            "rules": list_applicable_rules(self.db_path, repo=repo, min_confidence=min_confidence),
        }


def _read_framed_message(stdin: BinaryIO) -> dict[str, Any] | None:
    first = stdin.read(1)
    if not first:
        return None

    header = bytearray(first)
    while b"\r\n\r\n" not in header and b"\n\n" not in header:
        chunk = stdin.read(1)
        if not chunk:
            raise ValueError("incomplete MCP header")
        header.extend(chunk)

    marker = b"\r\n\r\n" if b"\r\n\r\n" in header else b"\n\n"
    raw_headers, body_start = bytes(header).split(marker, 1)
    content_length = None
    for raw_line in raw_headers.decode("ascii").splitlines():
        name, _, value = raw_line.partition(":")
        if name.strip().lower() == "content-length":
            content_length = int(value.strip())
            break
    if content_length is None:
        raise ValueError("missing Content-Length header")

    body = body_start + stdin.read(content_length - len(body_start))
    if len(body) != content_length:
        raise ValueError("incomplete MCP body")
    request = json.loads(body.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    return request


def _write_framed_message(stdout: BinaryIO, response: dict[str, Any]) -> None:
    body = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stdout.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stdout.flush()


def serve_stdio(db_path: str | Path, *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    server = MCPServer(db_path)
    while True:
        try:
            request = _read_framed_message(stdin)
            if request is None:
                return 0
            response = server.handle(request)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            _write_framed_message(stdout, response)
