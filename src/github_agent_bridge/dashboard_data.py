from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .session_events import redact_event_detail
from .session_correlation import job_session_metadata

JOB_LIST_ORDER_SQL = """
    CASE status
        WHEN 'running' THEN 0
        WHEN 'pending' THEN 1
        WHEN 'waiting_approval' THEN 2
        WHEN 'blocked' THEN 3
        WHEN 'denied' THEN 3
        WHEN 'done' THEN 4
        ELSE 5
    END,
    COALESCE(finished_at, started_at, updated_at, created_at) DESC,
    id DESC
"""


def readonly_connect(db: str | Path) -> sqlite3.Connection:
    path = Path(db).expanduser()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in con.execute(f"PRAGMA table_info({table})"))


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_seconds(start: str | None, end: str | None = None) -> int | None:
    started = parse_utc(start)
    if started is None:
        return None
    finished = parse_utc(end) or datetime.now(UTC)
    return max(0, int((finished - started).total_seconds()))


def completed_duration_seconds(start: str | None, end: str | None) -> int | None:
    started = parse_utc(start)
    finished = parse_utc(end)
    if started is None or finished is None:
        return None
    return max(0, int((finished - started).total_seconds()))


def row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def coerce_limit(value: int, maximum: int = 200) -> int:
    return max(1, min(value, maximum))


def parse_model_route_detail(detail: str | None) -> dict[str, Any]:
    if not detail:
        return {
            "configured": False,
            "model": None,
            "thinking": None,
            "summary": "n/a",
        }
    model = None
    thinking = None
    for part in detail.split():
        if part.startswith("model="):
            model = part.removeprefix("model=") or None
        elif part.startswith("thinking="):
            thinking = part.removeprefix("thinking=") or None
    return {
        "configured": bool(model or thinking),
        "model": model,
        "thinking": thinking,
        "summary": detail,
    }


def action_mode_for_intent(work_intent: str | None) -> str:
    return "fix_allowed" if work_intent == "work_allowed" else "review_only"


def intent_classifier_summary(metadata: dict[str, Any]) -> dict[str, Any] | None:
    classifier = metadata.get("intent_classifier")
    if not isinstance(classifier, dict):
        return None
    parser = classifier.get("parser") if isinstance(classifier.get("parser"), dict) else {}
    llm = classifier.get("llm") if isinstance(classifier.get("llm"), dict) else {}
    error = classifier.get("error")
    return {
        "enabled": bool(classifier.get("enabled")),
        "parser": parser,
        "llm": llm,
        "error": str(error) if error else None,
    }


def jobs_select_sql(con: sqlite3.Connection) -> str:
    if not table_exists(con, "job_session_events"):
        return "SELECT * FROM jobs"
    return """
        SELECT jobs.*,
            (
                SELECT detail
                FROM job_session_events
                WHERE job_id=jobs.id AND event_type='model_route_selected'
                ORDER BY id DESC
                LIMIT 1
            ) AS model_route_detail
        FROM jobs
    """


def job_summary(row: sqlite3.Row) -> dict[str, Any]:
    context = json.loads(row["context_json"] or "{}")
    metadata = json.loads(row["metadata_json"] or "{}")
    return {
        "id": row["id"],
        "work_key": row["work_key"],
        "repo": row["repo"],
        "thread": row["thread"],
        "status": row["status"],
        "action": row["action"],
        "decision": row["decision"],
        "intent": row["work_intent"],
        "action_mode": action_mode_for_intent(row["work_intent"]),
        "subject": row["subject"],
        "trigger_actor": row_get(row, "trigger_actor"),
        "trigger_actor_avatar_url": row_get(row, "trigger_actor_avatar_url"),
        "attempts": row["attempts"],
        "coalesced_count": row["coalesced_count"],
        "last_error": row["last_error"],
        "locked_by": row["locked_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "queue_wait_seconds": duration_seconds(row["created_at"], row["started_at"]) if row["started_at"] else None,
        "runtime_seconds": duration_seconds(row["started_at"], row["finished_at"]),
        "github_urls": context.get("urls", []),
        "model_route": parse_model_route_detail(row_get(row, "model_route_detail")),
        "intent_classifier": intent_classifier_summary(metadata),
    }


def where_clause(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    for column, value in filters.items():
        if value is None:
            continue
        clauses.append(f"{column}=?")
        args.append(value)
    return (" WHERE " + " AND ".join(clauses), args) if clauses else ("", args)


def actor_where_clause(actor: str | None, *, has_trigger_actor: bool) -> tuple[str, list[Any]]:
    if not actor or not actor.strip():
        return "", []
    if not has_trigger_actor:
        return " WHERE 1=0", []
    return " WHERE lower(trigger_actor)=lower(?)", [actor.strip().lstrip("@")]


def inspect_db_read_only(db: str | Path) -> dict[str, Any]:
    path = Path(db).expanduser()
    out: dict[str, Any] = {"db_path": str(path), "db_exists": path.exists()}
    if not path.exists():
        return out
    with readonly_connect(path) as con:
        if not table_exists(con, "jobs"):
            return out | {"schema_ok": False}
        out["schema_ok"] = True
        counts = {r["status"]: int(r["count"]) for r in con.execute("SELECT status, count(*) count FROM jobs GROUP BY status")}
        out["counts"] = counts
        out.update(counts)
        pending_age = con.execute("SELECT CAST((julianday('now') - julianday(min(created_at))) * 86400 AS INTEGER) age FROM jobs WHERE status='pending'").fetchone()["age"]
        out["oldest_pending_age_seconds"] = None if pending_age is None else int(pending_age)
        if table_exists(con, "state"):
            state = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM state")}
            out["last_uid"] = state.get("last_uid")
        if table_exists(con, "feedback_rule_proposals"):
            knowledge_counts = {
                r["status"]: int(r["count"])
                for r in con.execute("SELECT status, count(*) count FROM feedback_rule_proposals GROUP BY status")
            }
            out["knowledge"] = {
                "proposed": knowledge_counts.get("proposed", 0),
                "approved": knowledge_counts.get("approved", 0),
                "rejected": knowledge_counts.get("rejected", 0),
                "errors": knowledge_counts.get("error", 0),
            }
        if table_exists(con, "worklog"):
            last_log = con.execute("SELECT ts, phase, summary FROM worklog ORDER BY id DESC LIMIT 1").fetchone()
            if last_log:
                out["last_worklog"] = dict(last_log)
        running_rows = con.execute(
            """
            SELECT id, work_key, work_intent, locked_by, attempts, started_at, updated_at
            FROM jobs
            WHERE status='running'
            ORDER BY id
            """
        ).fetchall()
        out["running_jobs"] = [
            {
                "id": row["id"],
                "work_key": row["work_key"],
                "intent": row["work_intent"],
                "work_intent": row["work_intent"],
                "locked_by": row["locked_by"],
                "attempts": row["attempts"],
                "age_seconds": duration_seconds(row["started_at"]),
                "idle_seconds": duration_seconds(row["updated_at"]),
                "last_worklog": _last_worklog(con, int(row["id"])),
                "semantic_progress": _latest_job_progress(con, int(row["id"]), "semantic"),
                "visible_progress": _latest_job_progress(con, int(row["id"]), "visible"),
            }
            for row in running_rows
        ]
        out["running"] = len(running_rows)
    return out


def list_jobs(
    db: str | Path,
    *,
    status_filter: str | None = None,
    repo: str | None = None,
    thread: int | None = None,
    action: str | None = None,
    intent: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    with readonly_connect(path) as con:
        if not table_exists(con, "jobs"):
            return []
        filters = {"status": status_filter, "repo": repo, "thread": thread, "action": action, "work_intent": intent}
        where, args = where_clause(filters)
        actor_where, actor_args = actor_where_clause(actor, has_trigger_actor=column_exists(con, "jobs", "trigger_actor"))
        if actor_where:
            where += actor_where.replace(" WHERE ", " AND ", 1) if where else actor_where
            args.extend(actor_args)
        if since:
            where += " AND created_at >= ?" if where else " WHERE created_at >= ?"
            args.append(since)
        if until:
            where += " AND created_at <= ?" if where else " WHERE created_at <= ?"
            args.append(until)
        args.append(coerce_limit(limit))
        rows = con.execute(f"{jobs_select_sql(con)}{where} ORDER BY {JOB_LIST_ORDER_SQL} LIMIT ?", args).fetchall()
    return [job_summary(row) for row in rows]


def list_job_actors(db: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    with readonly_connect(path) as con:
        if not table_exists(con, "jobs") or not column_exists(con, "jobs", "trigger_actor"):
            return []
        rows = con.execute(
            """
            SELECT
                trigger_actor,
                max(trigger_actor_avatar_url) AS trigger_actor_avatar_url,
                count(*) AS job_count,
                max(updated_at) AS last_seen
            FROM jobs
            WHERE trigger_actor IS NOT NULL AND trigger_actor != ''
            GROUP BY lower(trigger_actor)
            ORDER BY job_count DESC, last_seen DESC, lower(trigger_actor)
            LIMIT ?
            """,
            (coerce_limit(limit),),
        ).fetchall()
    return [
        {
            "login": row["trigger_actor"],
            "avatar_url": row["trigger_actor_avatar_url"],
            "job_count": row["job_count"],
            "last_seen": row["last_seen"],
        }
        for row in rows
    ]


def get_job_detail(db: str | Path, job_id: int) -> dict[str, Any] | None:
    path = Path(db).expanduser()
    if not path.exists():
        return None
    with readonly_connect(path) as con:
        if not table_exists(con, "jobs"):
            return None
        row = con.execute(f"{jobs_select_sql(con)} WHERE jobs.id=?", (job_id,)).fetchone()
        if row is None:
            return None
        job = job_summary(row)
        job["context"] = json.loads(row["context_json"] or "{}")
        job["metadata"] = json.loads(row["metadata_json"] or "{}")
        job["worklog"] = [
            dict(log)
            for log in con.execute(
                "SELECT id, ts, phase, summary, detail FROM worklog WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        ] if table_exists(con, "worklog") else []
        job["progress"] = [
            dict(progress)
            for progress in con.execute(
                "SELECT id, ts, kind, phase, summary, detail FROM job_progress WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        ] if table_exists(con, "job_progress") else []
        job["coalesced_notifications"] = [
            {
                "id": row["id"],
                "uid": row["uid"],
                "message_id": row["message_id"],
                "subject": row["subject"],
                "trigger_actor": row_get(row, "trigger_actor"),
                "trigger_actor_avatar_url": row_get(row, "trigger_actor_avatar_url"),
                "context": json.loads(row["context_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in con.execute(
                "SELECT * FROM coalesced_notifications WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        ] if table_exists(con, "coalesced_notifications") else []
    return job


def job_logs(db: str | Path, job_id: int, limit: int = 100) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    with readonly_connect(path) as con:
        if not table_exists(con, "worklog"):
            return []
        rows = con.execute(
            "SELECT id, ts, phase, summary, detail FROM worklog WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, coerce_limit(limit, maximum=500)),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def latest_job_progress(db: str | Path, job_id: int, kind: str | None = None) -> dict[str, Any] | None:
    path = Path(db).expanduser()
    if not path.exists():
        return None
    with readonly_connect(path) as con:
        if not table_exists(con, "job_progress"):
            return None
        return _latest_job_progress(con, job_id, kind)


def job_session(db: str | Path, job_id: int) -> dict[str, Any] | None:
    job = get_job_detail(db, job_id)
    if job is None:
        return None
    session = job_session_metadata(job)
    session["job_id"] = job_id
    session["work_key"] = job["work_key"]
    session["status"] = job["status"]
    session_id = str(session["id"])
    session["transcript_available"] = (
        openclaw_session_file(session_id) is not None or openclaw_trajectory_file(session_id) is not None
    )
    session["detail"] = (
        "Dispatches use this explicit OpenClaw session id. "
        "The dashboard exposes bounded, redacted bridge events and OpenClaw transcript entries for this session."
    )
    return session


def job_session_events(db: str | Path, job_id: int, *, after_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    with readonly_connect(path) as con:
        if not table_exists(con, "job_session_events"):
            return []
        where = "job_id=?"
        args: list[Any] = [job_id]
        if after_id is not None:
            where += " AND id>?"
            args.append(after_id)
        args.append(coerce_limit(limit, maximum=500))
        rows = con.execute(
            f"""
            SELECT id, ts, job_id, work_key, session_id, event_type, summary, detail
            FROM job_session_events
            WHERE {where}
            ORDER BY id
            LIMIT ?
            """,
            args,
        ).fetchall()
    return [dict(row) | {"detail": redact_event_detail(row["detail"])} for row in rows]


def job_session_transcript(db: str | Path, job_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
    session = job_session(db, job_id)
    if session is None:
        return []
    max_entries = coerce_limit(limit, maximum=1000)
    session_id = str(session.get("id") or "")
    if not session_id:
        return []
    session_file = openclaw_session_file(session_id)
    entries: list[dict[str, Any]] = []
    if session_file is not None and session_file.exists():
        for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if len(entries) >= max_entries:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = transcript_entry(item)
            if entry is not None:
                entries.append(entry)
    if (session.get("status") == "running" or not entries) and len(entries) < max_entries:
        trajectory_file = openclaw_trajectory_file(session_id)
        if trajectory_file is not None and trajectory_file.exists():
            for line in trajectory_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if len(entries) >= max_entries:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry = transcript_entry_from_trajectory(item)
                if entry is not None:
                    entries.append(entry)
    if session.get("status") == "running" and len(entries) < max_entries:
        live_events = job_session_events(db, job_id, limit=max_entries)
        for event in live_events:
            entry = transcript_entry_from_session_event(event)
            if entry is None:
                continue
            entries.append(entry)
            if len(entries) >= max_entries:
                break
    return entries


def openclaw_session_file(session_id: str) -> Path | None:
    store = Path(os.getenv("GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE", "~/.openclaw/agents/github/sessions/sessions.json")).expanduser()
    if not store.exists():
        fallback = store.with_name(f"{session_id}.jsonl")
        return fallback if fallback.exists() else None
    try:
        sessions = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for value in sessions.values() if isinstance(sessions, dict) else []:
        if not isinstance(value, dict) or str(value.get("sessionId") or "") != session_id:
            continue
        session_file = value.get("sessionFile")
        if not session_file:
            continue
        path = Path(str(session_file)).expanduser()
        return path if path.exists() else None
    fallback = store.with_name(f"{session_id}.jsonl")
    return fallback if fallback.exists() else None


def openclaw_trajectory_file(session_id: str) -> Path | None:
    store = Path(os.getenv("GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE", "~/.openclaw/agents/github/sessions/sessions.json")).expanduser()
    fallback = store.with_name(f"{session_id}.trajectory.jsonl")
    if fallback.exists():
        return fallback
    pointer = store.with_name(f"{session_id}.trajectory-path.json")
    if not pointer.exists():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = payload.get("runtimeFile")
    if not path:
        return None
    trajectory_file = Path(str(path)).expanduser()
    return trajectory_file if trajectory_file.exists() else None


def transcript_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("type") == "session":
        return {
            "timestamp": item.get("timestamp"),
            "role": "system",
            "kind": "session",
            "title": "Session started",
            "text": f"cwd={item.get('cwd') or ''}",
        }
    if item.get("type") != "message":
        return None
    message = item.get("message")
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "unknown")
    content = message.get("content")
    kind = role
    title = role
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "toolCall":
                name = block.get("name") or "tool"
                kind = "tool_call"
                title = f"Tool call: {name}"
                args = block.get("arguments") or block.get("input") or {}
                parts.append(json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True))
            elif block_type == "toolResult":
                name = block.get("toolName") or block.get("name") or "tool"
                kind = "tool_result"
                title = f"Tool result: {name}"
                parts.append(str(block.get("content") or block.get("text") or ""))
            elif "text" in block:
                parts.append(str(block.get("text") or ""))
            elif "content" in block:
                parts.append(str(block.get("content") or ""))
        text = "\n\n".join(part for part in parts if part)
    if not text:
        text = str(message.get("content") or "")
    text = redact_event_detail(text)
    if len(text) > 6000:
        text = text[:6000] + "\n... [truncated]"
    return {
        "timestamp": item.get("timestamp") or message.get("timestamp"),
        "role": role,
        "kind": kind,
        "title": title,
        "text": text,
    }


def transcript_entry_from_trajectory(item: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(item.get("type") or "")
    timestamp = item.get("ts")
    data = item.get("data")
    if not isinstance(data, dict):
        return None
    if event_type == "session.started":
        workspace = data.get("workspaceDir") or item.get("workspaceDir") or ""
        return {
            "timestamp": timestamp,
            "role": "system",
            "kind": "trajectory_session",
            "title": "Session started",
            "text": redact_event_detail(f"workspace={workspace}"),
        }
    if event_type == "tool.call":
        name = str(data.get("name") or "tool")
        arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        text = json.dumps(arguments, ensure_ascii=False, indent=2, sort_keys=True)
        return {
            "timestamp": timestamp,
            "role": "assistant",
            "kind": "tool_call",
            "title": f"Tool call: {name}",
            "text": _bounded_transcript_text(text),
        }
    if event_type == "tool.result":
        name = str(data.get("name") or "tool")
        output = data.get("output")
        result = data.get("result")
        if output is None and isinstance(result, dict):
            output = result.get("output") or result.get("stdout") or result.get("stderr")
        text = str(output or "")
        status = str(data.get("status") or (result.get("status") if isinstance(result, dict) else "") or "completed")
        return {
            "timestamp": timestamp,
            "role": "tool",
            "kind": "tool_result",
            "title": f"Tool result: {name}",
            "text": _bounded_transcript_text(f"status={status}\n{text}".strip()),
        }
    return None


def transcript_entry_from_session_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "")
    if event_type not in {"openclaw_stdout", "openclaw_stderr"}:
        return None
    detail = redact_event_detail(event.get("detail"))
    if not detail:
        return None
    title = "OpenClaw stdout" if event_type == "openclaw_stdout" else "OpenClaw stderr"
    return {
        "timestamp": event.get("ts"),
        "role": "assistant",
        "kind": event_type,
        "title": title,
        "text": detail,
    }


def _bounded_transcript_text(text: str) -> str:
    text = redact_event_detail(text)
    if len(text) > 6000:
        return text[:6000] + "\n... [truncated]"
    return text


def metrics_summary(db: str | Path, *, timezone_name: str = "UTC") -> dict[str, Any]:
    path = Path(db).expanduser()
    if not path.exists():
        return {"db_exists": False, "status_counts": {}, "runtime_seconds": {}}
    timezone = dashboard_timezone(timezone_name)
    with readonly_connect(path) as con:
        if not table_exists(con, "jobs"):
            return {"db_exists": True, "schema_ok": False, "status_counts": {}, "runtime_seconds": {}}
        rows = con.execute("SELECT status, repo, action, work_intent, created_at, started_at, finished_at FROM jobs").fetchall()
    status_counts = Counter(row["status"] for row in rows)
    by_repo = Counter(row["repo"] or "unknown" for row in rows)
    by_action = Counter(row["action"] for row in rows)
    by_intent = Counter(row["work_intent"] for row in rows)
    by_created_day = Counter(day for day in (created_day(row["created_at"], timezone) for row in rows) if day)
    runtimes = sorted(
        seconds for seconds in (completed_duration_seconds(row["started_at"], row["finished_at"]) for row in rows) if seconds is not None
    )
    waits = sorted(
        seconds for seconds in (duration_seconds(row["created_at"], row["started_at"]) for row in rows if row["started_at"]) if seconds is not None
    )
    return {
        "db_exists": True,
        "schema_ok": True,
        "status_counts": dict(status_counts),
        "by_repo": dict(by_repo),
        "by_action": dict(by_action),
        "by_intent": dict(by_intent),
        "by_created_day": dict(sorted(by_created_day.items())),
        "runtime_usage": runtime_usage(rows, timezone),
        "runtime_seconds": percentiles(runtimes),
        "queue_wait_seconds": percentiles(waits),
    }


def dashboard_timezone(name: str | None) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def created_day(value: str | None, timezone: ZoneInfo = ZoneInfo("UTC")) -> str | None:
    created = parse_utc(value)
    if created is None:
        return None
    return created.astimezone(timezone).date().isoformat()


def runtime_usage(rows: list[sqlite3.Row], timezone: ZoneInfo) -> dict[str, list[dict[str, Any]]]:
    daily: dict[str, dict[str, int]] = {}
    monthly: dict[str, dict[str, int]] = {}
    for row in rows:
        started = parse_utc(row["started_at"])
        finished = parse_utc(row["finished_at"])
        seconds = completed_duration_seconds(row["started_at"], row["finished_at"])
        if started is None or finished is None or seconds is None:
            continue
        bucket_at = finished
        local = bucket_at.astimezone(timezone)
        day = local.date().isoformat()
        month = f"{local.year:04d}-{local.month:02d}"
        mode = runtime_usage_mode(row["work_intent"])
        add_runtime_bucket(daily, day, seconds, mode)
        add_runtime_bucket(monthly, month, seconds, mode)
    return {
        "day": runtime_bucket_rows(daily),
        "month": runtime_bucket_rows(monthly),
    }


def runtime_usage_mode(work_intent: str | None) -> str:
    return "work" if work_intent == "work_allowed" else "review"


def add_runtime_bucket(buckets: dict[str, dict[str, int]], bucket: str, seconds: int, mode: str) -> None:
    current = buckets.setdefault(bucket, {"seconds": 0, "jobs": 0, "work_seconds": 0, "review_seconds": 0, "work_jobs": 0, "review_jobs": 0})
    current["seconds"] += seconds
    current["jobs"] += 1
    current[f"{mode}_seconds"] += seconds
    current[f"{mode}_jobs"] += 1


def runtime_bucket_rows(buckets: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    return [
        {
            "bucket": bucket,
            "seconds": values["seconds"],
            "minutes": round(values["seconds"] / 60, 2),
            "jobs": values["jobs"],
            "work_seconds": values["work_seconds"],
            "review_seconds": values["review_seconds"],
            "work_jobs": values["work_jobs"],
            "review_jobs": values["review_jobs"],
        }
        for bucket, values in sorted(buckets.items())
    ]


def percentiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"median": None, "p90": None, "p99": None}
    return {
        "median": nearest_rank(values, 0.50),
        "p90": nearest_rank(values, 0.90),
        "p99": nearest_rank(values, 0.99),
    }


def nearest_rank(values: list[int], percentile: float) -> int:
    index = max(0, min(len(values) - 1, int(round(percentile * (len(values) - 1)))))
    return values[index]


def _last_worklog(con: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    if not table_exists(con, "worklog"):
        return None
    row = con.execute(
        "SELECT ts, phase, summary FROM worklog WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return dict(row) if row else None


def _latest_job_progress(con: sqlite3.Connection, job_id: int, kind: str | None = None) -> dict[str, Any] | None:
    if not table_exists(con, "job_progress"):
        return None
    where = "job_id=?"
    args: list[Any] = [job_id]
    if kind:
        where += " AND kind=?"
        args.append(kind)
    row = con.execute(
        f"SELECT id, ts, kind, phase, summary, detail FROM job_progress WHERE {where} ORDER BY id DESC LIMIT 1",
        args,
    ).fetchone()
    if row is None:
        return None
    progress = dict(row)
    progress["age_seconds"] = duration_seconds(progress.get("ts"))
    return progress
