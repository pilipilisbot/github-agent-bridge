from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import utc_now


DEFAULT_PROCESS_SAMPLE_RETENTION_SECONDS = 24 * 60 * 60
SENTRY_DSN_ENV = "GITHUB_AGENT_BRIDGE_SENTRY_DSN"
SENTRY_ENVIRONMENT_ENV = "GITHUB_AGENT_BRIDGE_SENTRY_ENVIRONMENT"
SENTRY_RELEASE_ENV = "GITHUB_AGENT_BRIDGE_SENTRY_RELEASE"
SENTRY_TRACES_SAMPLE_RATE_ENV = "GITHUB_AGENT_BRIDGE_SENTRY_TRACES_SAMPLE_RATE"
SENTRY_PROFILES_SAMPLE_RATE_ENV = "GITHUB_AGENT_BRIDGE_SENTRY_PROFILES_SAMPLE_RATE"

_SENTRY_INITIALIZED = False
_SENTRY_LAST_RESULT: dict[str, Any] | None = None


def configure_sentry(*, service: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Initialize Sentry when configured, without making sentry-sdk mandatory."""
    global _SENTRY_INITIALIZED, _SENTRY_LAST_RESULT

    values = os.environ if env is None else env
    dsn = _first_env(values, SENTRY_DSN_ENV, "SENTRY_DSN")
    if not dsn:
        return {"enabled": False, "reason": "missing_dsn"}
    if _SENTRY_INITIALIZED:
        return _SENTRY_LAST_RESULT or {"enabled": True, "service": service}

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except ImportError:
        _SENTRY_LAST_RESULT = {"enabled": False, "reason": "sentry_sdk_missing"}
        return _SENTRY_LAST_RESULT

    from . import __version__

    release = _first_env(values, SENTRY_RELEASE_ENV, "SENTRY_RELEASE") or f"github-agent-bridge@{__version__}"
    environment = _first_env(values, SENTRY_ENVIRONMENT_ENV, "SENTRY_ENVIRONMENT") or None
    options: dict[str, Any] = {
        "dsn": dsn,
        "release": release,
        "environment": environment,
        "send_default_pii": False,
    }
    traces_sample_rate = _sample_rate(values, SENTRY_TRACES_SAMPLE_RATE_ENV, "SENTRY_TRACES_SAMPLE_RATE")
    profiles_sample_rate = _sample_rate(values, SENTRY_PROFILES_SAMPLE_RATE_ENV, "SENTRY_PROFILES_SAMPLE_RATE")
    if traces_sample_rate is not None:
        options["traces_sample_rate"] = traces_sample_rate
    if profiles_sample_rate is not None:
        options["profiles_sample_rate"] = profiles_sample_rate

    sentry_sdk.init(**options)
    sentry_sdk.set_tag("service", service)
    sentry_sdk.set_tag("component", "github-agent-bridge")
    _SENTRY_INITIALIZED = True
    _SENTRY_LAST_RESULT = {
        "enabled": True,
        "service": service,
        "release": release,
        "environment": environment,
    }
    return _SENTRY_LAST_RESULT


def record_monitor_observation(
    db: str | Path,
    metrics: dict[str, Any],
    alerts: list[str],
    *,
    process_sample_retention_seconds: int = DEFAULT_PROCESS_SAMPLE_RETENTION_SECONDS,
) -> None:
    path = Path(db).expanduser()
    if not path.exists():
        return
    with sqlite3.connect(path, timeout=30) as con:
        con.row_factory = sqlite3.Row
        if not _table_exists(con, "process_samples") or not _table_exists(con, "alerts"):
            return
        now = utc_now()
        _record_process_sample(con, now, metrics, process_sample_retention_seconds)
        _record_alerts(con, now, metrics, alerts)


def recent_process_samples(db: str | Path, *, limit: int = 60) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        if not _table_exists(con, "process_samples"):
            return []
        rows = con.execute(
            """
            SELECT id, ts, executor_pid, root_pid, running_job_ids_json, cpu_ticks, io_bytes,
                   active_since_last_sample, idle_seconds
            FROM process_samples
            ORDER BY id DESC
            LIMIT ?
            """,
            (_coerce_limit(limit, maximum=500),),
        ).fetchall()
    return [_process_sample_row(row) for row in reversed(rows)]


def list_alerts(db: str | Path, *, include_resolved: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    path = Path(db).expanduser()
    if not path.exists():
        return []
    where = "" if include_resolved else "WHERE resolved_at IS NULL"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        if not _table_exists(con, "alerts"):
            return []
        rows = con.execute(
            f"""
            SELECT fingerprint, source, severity, message, context_json, first_seen, last_seen,
                   resolved_at, observations
            FROM alerts
            {where}
            ORDER BY resolved_at IS NULL DESC, last_seen DESC
            LIMIT ?
            """,
            (_coerce_limit(limit, maximum=200),),
        ).fetchall()
    return [_alert_row(row) for row in rows]


def _record_process_sample(con: sqlite3.Connection, now: str, metrics: dict[str, Any], retention_seconds: int) -> None:
    children = metrics.get("executor_children") or []
    if not isinstance(children, list):
        children = []
    flattened = list(_flatten_processes(children))
    root_pid = int(children[0]["pid"]) if children and isinstance(children[0], dict) and children[0].get("pid") is not None else None
    cpu_ticks = sum(int(process.get("cpu_ticks") or 0) for process in flattened)
    io_bytes = sum(_process_io_total(process) for process in flattened)
    running_job_ids = [job.get("id") for job in metrics.get("running_jobs", []) if isinstance(job, dict) and job.get("id") is not None]
    previous = _latest_process_sample(con)
    active = _sample_active(previous, root_pid=root_pid, pids=[p.get("pid") for p in flattened], cpu_ticks=cpu_ticks, io_bytes=io_bytes)
    idle_seconds = None
    if previous and not active and previous["ts"]:
        idle_seconds = _elapsed_seconds(previous["ts"], now)
    con.execute(
        """
        INSERT INTO process_samples(
          ts, executor_pid, root_pid, running_job_ids_json, process_tree_json,
          cpu_ticks, io_bytes, active_since_last_sample, idle_seconds
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            now,
            metrics.get("executor_pid"),
            root_pid,
            json.dumps(running_job_ids, separators=(",", ":")),
            json.dumps(children, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            cpu_ticks,
            io_bytes,
            1 if active else 0,
            idle_seconds,
        ),
    )
    if retention_seconds > 0:
        con.execute(
            "DELETE FROM process_samples WHERE (julianday(?) - julianday(ts)) * 86400 > ?",
            (now, retention_seconds),
        )


def _record_alerts(con: sqlite3.Connection, now: str, metrics: dict[str, Any], alerts: list[str]) -> None:
    fingerprints: set[str] = set()
    context = {
        "running_jobs": metrics.get("running_jobs", []),
        "executor_service": metrics.get("executor_service"),
        "executor_pid": metrics.get("executor_pid"),
        "reader_timer": metrics.get("reader_timer"),
        "reader_recent": metrics.get("reader_recent"),
    }
    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for message in alerts:
        fingerprint = _alert_fingerprint("monitor", message)
        fingerprints.add(fingerprint)
        con.execute(
            """
            INSERT INTO alerts(fingerprint, source, severity, message, context_json, first_seen, last_seen, resolved_at, observations)
            VALUES(?,?,?,?,?,?,?,NULL,1)
            ON CONFLICT(fingerprint) DO UPDATE SET
              context_json=excluded.context_json,
              last_seen=excluded.last_seen,
              resolved_at=NULL,
              observations=alerts.observations+1
            """,
            (fingerprint, "monitor", "warning", message, context_json, now, now),
        )
    if fingerprints:
        placeholders = ",".join("?" for _ in fingerprints)
        con.execute(
            f"UPDATE alerts SET resolved_at=? WHERE source='monitor' AND resolved_at IS NULL AND fingerprint NOT IN ({placeholders})",
            (now, *sorted(fingerprints)),
        )
    else:
        con.execute("UPDATE alerts SET resolved_at=? WHERE source='monitor' AND resolved_at IS NULL", (now,))


def _latest_process_sample(con: sqlite3.Connection) -> sqlite3.Row | None:
    return con.execute(
        "SELECT ts, root_pid, process_tree_json, cpu_ticks, io_bytes FROM process_samples ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _sample_active(previous: sqlite3.Row | None, *, root_pid: int | None, pids: list[Any], cpu_ticks: int, io_bytes: int) -> bool:
    if previous is None:
        return bool(root_pid or pids or cpu_ticks or io_bytes)
    previous_processes = json.loads(previous["process_tree_json"] or "[]")
    previous_pids = [process.get("pid") for process in _flatten_processes(previous_processes)]
    return (
        previous["root_pid"] != root_pid
        or previous_pids != pids
        or cpu_ticks > int(previous["cpu_ticks"] or 0)
        or io_bytes > int(previous["io_bytes"] or 0)
    )


def _flatten_processes(processes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for process in processes:
        if not isinstance(process, dict):
            continue
        out.append(process)
        children = process.get("children")
        if isinstance(children, list):
            out.extend(_flatten_processes(children))
    return out


def _process_io_total(process: dict[str, Any]) -> int:
    io_bytes = process.get("io_bytes")
    if not isinstance(io_bytes, dict):
        return 0
    return int(io_bytes.get("read_bytes") or 0) + int(io_bytes.get("write_bytes") or 0)


def _elapsed_seconds(start: str, end: str) -> int | None:
    from .dashboard_data import duration_seconds

    return duration_seconds(start, end)


def _alert_fingerprint(source: str, message: str) -> str:
    return hashlib.sha256(f"{source}\0{message}".encode("utf-8")).hexdigest()


def _process_sample_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ts": row["ts"],
        "executor_pid": row["executor_pid"],
        "root_pid": row["root_pid"],
        "running_job_ids": json.loads(row["running_job_ids_json"] or "[]"),
        "cpu_ticks": row["cpu_ticks"],
        "io_bytes": row["io_bytes"],
        "active_since_last_sample": bool(row["active_since_last_sample"]),
        "idle_seconds": row["idle_seconds"],
    }


def _alert_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "fingerprint": row["fingerprint"],
        "source": row["source"],
        "severity": row["severity"],
        "message": row["message"],
        "context": json.loads(row["context_json"] or "{}"),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "resolved_at": row["resolved_at"],
        "observations": row["observations"],
    }


def _coerce_limit(value: int, *, maximum: int) -> int:
    return max(1, min(value, maximum))


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _first_env(values: dict[str, str], *names: str) -> str:
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def _sample_rate(values: dict[str, str], *names: str) -> float | None:
    raw = _first_env(values, *names)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 0 or value > 1:
        return None
    return value
