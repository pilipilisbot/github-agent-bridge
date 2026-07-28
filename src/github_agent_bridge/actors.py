from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from .models import GitHubContext, Notification

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?$")
ISSUE_EVENT_URL_RE = re.compile(
    r"https://github\.com/([^/]+/[^/]+)/(?:issues|pull)/(\d+)#event-(\d+)"
)
RESERVED_SENDERS = {"github", "notifications"}


def default_gh_bin() -> str:
    return os.getenv("GITHUB_AGENT_BRIDGE_GH_BIN", "gh")


@dataclass(frozen=True)
class TriggerActor:
    login: str
    avatar_url: str | None = None
    user_id: int | None = None


def normalize_github_login(value: str | None) -> str | None:
    if not value:
        return None
    login = value.strip().strip("@")
    if login.lower() in RESERVED_SENDERS:
        return None
    return login if LOGIN_RE.fullmatch(login) else None


def github_avatar_url(login: str | None) -> str | None:
    normalized = normalize_github_login(login)
    return f"https://github.com/{normalized}.png?size=80" if normalized else None


def trigger_actor_details_from_notification(notification: Notification) -> TriggerActor | None:
    display_name, email_addr = parseaddr(notification.from_addr)
    if "notifications@github.com" not in email_addr.lower():
        return None
    login = normalize_github_login(display_name)
    return TriggerActor(login=login, avatar_url=github_avatar_url(login)) if login else None


def trigger_actor_details_for_enqueue(notification: Notification, ctx: GitHubContext, *, gh_bin: str | None = None) -> TriggerActor | None:
    gh_bin = gh_bin or default_gh_bin()
    return github_actor_details_for_context(ctx, gh_bin=gh_bin) or trigger_actor_details_from_notification(notification)


def trigger_actor_from_notification(notification: Notification) -> str | None:
    actor = trigger_actor_details_from_notification(notification)
    return actor.login if actor else None


def actor_details_from_github_payload(payload: dict[str, Any]) -> TriggerActor | None:
    actor_keys = (
        ("assigner", "actor", "user", "sender")
        if payload.get("event") in {"assigned", "unassigned"}
        else ("user", "actor", "sender")
    )
    for key in actor_keys:
        value = payload.get(key)
        if isinstance(value, dict):
            login = normalize_github_login(value.get("login"))
            if login:
                avatar_url = value.get("avatar_url") if isinstance(value.get("avatar_url"), str) else None
                user_id = value.get("id")
                return TriggerActor(
                    login=login,
                    avatar_url=avatar_url or github_avatar_url(login),
                    user_id=user_id if isinstance(user_id, int) and user_id > 0 else None,
                )
    return None


def actor_from_github_payload(payload: dict[str, Any]) -> str | None:
    actor = actor_details_from_github_payload(payload)
    return actor.login if actor else None


def actor_endpoint(ctx: GitHubContext) -> str | None:
    if not ctx.repo:
        return None
    if ctx.comment_id:
        return f"repos/{ctx.repo}/issues/comments/{ctx.comment_id}"
    if ctx.review_comment_id:
        return f"repos/{ctx.repo}/pulls/comments/{ctx.review_comment_id}"
    if ctx.review_id and ctx.issue_number:
        return f"repos/{ctx.repo}/pulls/{ctx.issue_number}/reviews/{ctx.review_id}"
    if ctx.commit_comment_id:
        return f"repos/{ctx.repo}/comments/{ctx.commit_comment_id}"
    if ctx.workflow_run_id:
        return f"repos/{ctx.repo}/actions/runs/{ctx.workflow_run_id}"
    if ctx.issue_number:
        for url in ctx.urls:
            event = ISSUE_EVENT_URL_RE.search(url)
            if event and event.group(1).lower() == ctx.repo.lower() and int(event.group(2)) == ctx.issue_number:
                return f"repos/{ctx.repo}/issues/events/{event.group(3)}"
        return f"repos/{ctx.repo}/issues/{ctx.issue_number}"
    return None


def github_actor_details_for_context(ctx: GitHubContext, *, gh_bin: str | None = None) -> TriggerActor | None:
    gh_bin = gh_bin or default_gh_bin()
    endpoint = actor_endpoint(ctx)
    if endpoint is None:
        return None
    try:
        proc = subprocess.run([gh_bin, "api", endpoint], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return actor_details_from_github_payload(payload if isinstance(payload, dict) else {})


def github_actor_for_context(ctx: GitHubContext, *, gh_bin: str | None = None) -> str | None:
    actor = github_actor_details_for_context(ctx, gh_bin=gh_bin)
    return actor.login if actor else None


def backfill_trigger_actors(db: str | Path, *, gh_bin: str | None = None, limit: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    gh_bin = gh_bin or default_gh_bin()
    path = Path(db).expanduser()
    if not path.exists():
        return {"db_exists": False, "checked": 0, "updated": 0, "missing": 0, "dry_run": dry_run}
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        jobs_table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
        if jobs_table is None:
            return {"db_exists": True, "checked": 0, "updated": 0, "missing": 0, "dry_run": dry_run, "updates": []}
        has_actor_column = _has_column(con, "jobs", "trigger_actor")
        has_avatar_column = _has_column(con, "jobs", "trigger_actor_avatar_url")
        if not dry_run:
            _ensure_trigger_actor_columns(con)
            has_actor_column = has_avatar_column = True
        conditions = []
        if has_actor_column:
            conditions.append("(trigger_actor IS NULL OR trigger_actor='')")
        if has_avatar_column:
            conditions.append("(trigger_actor_avatar_url IS NULL OR trigger_actor_avatar_url='')")
        where = f"WHERE {' OR '.join(conditions)}" if conditions else ""
        select_columns = "id, context_json"
        if has_actor_column:
            select_columns += ", trigger_actor"
        if has_avatar_column:
            select_columns += ", trigger_actor_avatar_url"
        rows = con.execute(
            f"""
            SELECT {select_columns}
            FROM jobs
            {where}
            ORDER BY id
            LIMIT ?
            """,
            (max(1, limit or 1000000),),
        ).fetchall()
        checked = updated = missing = 0
        updates: list[dict[str, Any]] = []
        for row in rows:
            checked += 1
            try:
                ctx = GitHubContext.from_json(row["context_json"])
            except (TypeError, json.JSONDecodeError):
                missing += 1
                continue
            existing_actor = normalize_github_login(row["trigger_actor"]) if has_actor_column else None
            actor = (
                TriggerActor(login=existing_actor, avatar_url=github_avatar_url(existing_actor))
                if existing_actor and (not has_avatar_column or not row["trigger_actor_avatar_url"])
                else github_actor_details_for_context(ctx, gh_bin=gh_bin)
            )
            if not actor:
                missing += 1
                continue
            updates.append({"job_id": int(row["id"]), "trigger_actor": actor.login, "trigger_actor_avatar_url": actor.avatar_url})
            updated += 1
            if not dry_run:
                con.execute(
                    "UPDATE jobs SET trigger_actor=?, trigger_actor_avatar_url=? WHERE id=?",
                    (actor.login, actor.avatar_url, row["id"]),
                )
        if not dry_run:
            con.commit()
        return {"db_exists": True, "checked": checked, "updated": updated, "missing": missing, "dry_run": dry_run, "updates": updates}
    finally:
        con.close()


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if exists is None:
        return False
    return column in {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}


def _ensure_trigger_actor_columns(con: sqlite3.Connection) -> None:
    tables = {
        "jobs": {"trigger_actor": "TEXT", "trigger_actor_avatar_url": "TEXT"},
        "coalesced_notifications": {"trigger_actor": "TEXT", "trigger_actor_avatar_url": "TEXT"},
    }
    for table, columns in tables.items():
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if exists is None:
            continue
        existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
