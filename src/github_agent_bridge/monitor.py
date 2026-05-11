from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MonitorThresholds:
    pending_warn_seconds: int = 300
    review_running_warn_seconds: int = 1200
    work_running_warn_seconds: int = 4200
    reader_recent_seconds: int = 180


@dataclass
class MonitorReport:
    ok: bool
    alerts: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "alerts": self.alerts, "metrics": self.metrics}

    def text(self) -> str:
        status = "OK" if self.ok else "ALERT"
        parts = [f"{status} github-agent-bridge"]
        metrics = self.metrics
        compact = [
            f"executor={metrics.get('executor_service', 'unknown')}",
            f"reader_timer={metrics.get('reader_timer', 'unknown')}",
            f"reader_recent={metrics.get('reader_recent', 'unknown')}",
            f"pending={metrics.get('pending', 0)}",
            f"blocked={metrics.get('blocked', 0)}",
            f"running={metrics.get('running', 0)}",
            f"oldest_pending={metrics.get('oldest_pending_age_seconds') if metrics.get('oldest_pending_age_seconds') is not None else '-'}",
            f"last_uid={metrics.get('last_uid', '-')}",
        ]
        parts.append(" ".join(compact))
        if self.alerts:
            parts.extend(f"- {a}" for a in self.alerts)
        return "\n".join(parts)


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _is_active(unit: str) -> str:
    proc = _run_systemctl(["is-active", unit])
    return proc.stdout.strip() or "unknown"


def _last_service_result(unit: str) -> tuple[str, str | None]:
    proc = _run_systemctl(["show", unit, "--property=Result,ExecMainStatus,InactiveExitTimestampMonotonic", "--value"])
    lines = [line.strip() for line in proc.stdout.splitlines()]
    result = lines[0] if lines else "unknown"
    exit_status = lines[1] if len(lines) > 1 else None
    return result or "unknown", exit_status


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def inspect_db(path: str | Path) -> dict[str, Any]:
    db = Path(path).expanduser()
    out: dict[str, Any] = {"db_path": str(db), "db_exists": db.exists()}
    if not db.exists():
        return out
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if not _table_exists(con, "jobs"):
        return out | {"schema_ok": False}
    out["schema_ok"] = True
    stats = {r["status"]: int(r["count"]) for r in con.execute("SELECT status, count(*) count FROM jobs GROUP BY status")}
    out.update(stats)
    pending_age = con.execute("SELECT CAST((julianday('now') - julianday(min(created_at))) * 86400 AS INTEGER) age FROM jobs WHERE status='pending'").fetchone()["age"]
    out["oldest_pending_age_seconds"] = None if pending_age is None else int(pending_age)
    state = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM state")}
    out["last_uid"] = state.get("last_uid")
    running_rows = con.execute("SELECT id, work_key, work_intent, started_at FROM jobs WHERE status='running'").fetchall()
    now = datetime.now(UTC)
    running = []
    for row in running_rows:
        started = _parse_utc(row["started_at"])
        age = int((now - started).total_seconds()) if started else None
        running.append({"id": row["id"], "work_key": row["work_key"], "work_intent": row["work_intent"], "age_seconds": age})
    out["running_jobs"] = running
    out["running"] = len(running)
    last_log = con.execute("SELECT ts, phase, summary FROM worklog ORDER BY id DESC LIMIT 1").fetchone() if _table_exists(con, "worklog") else None
    if last_log:
        out["last_worklog"] = dict(last_log)
    return out


def monitor(
    db: str | Path,
    executor_unit: str = "github-agent-bridge.service",
    reader_timer_unit: str = "github-agent-bridge-reader.timer",
    reader_service_unit: str = "github-agent-bridge-reader.service",
    thresholds: MonitorThresholds | None = None,
    check_systemd: bool = True,
) -> MonitorReport:
    thresholds = thresholds or MonitorThresholds()
    metrics = inspect_db(db)
    alerts: list[str] = []

    if not metrics.get("db_exists"):
        alerts.append(f"database missing: {metrics.get('db_path')}")
    elif not metrics.get("schema_ok", True):
        alerts.append("database schema missing jobs table")

    pending = int(metrics.get("pending", 0) or 0)
    blocked = int(metrics.get("blocked", 0) or 0)
    waiting = int(metrics.get("waiting_approval", 0) or 0)
    pending_age = metrics.get("oldest_pending_age_seconds")
    if blocked:
        alerts.append(f"blocked jobs: {blocked}")
    if pending and pending_age is not None and pending_age > thresholds.pending_warn_seconds:
        alerts.append(f"pending queue oldest age {pending_age}s > {thresholds.pending_warn_seconds}s")
    if waiting:
        metrics["waiting_approval"] = waiting

    for job in metrics.get("running_jobs", []):
        age = job.get("age_seconds")
        if age is None:
            continue
        limit = thresholds.review_running_warn_seconds if job.get("work_intent") == "review_only" else thresholds.work_running_warn_seconds
        if age > limit:
            alerts.append(f"running job {job.get('id')} {job.get('work_key')} age {age}s > {limit}s")

    if check_systemd:
        executor_state = _is_active(executor_unit)
        timer_state = _is_active(reader_timer_unit)
        reader_result, reader_exit = _last_service_result(reader_service_unit)
        metrics.update({
            "executor_service": executor_state,
            "reader_timer": timer_state,
            "reader_last_result": reader_result,
            "reader_last_exit_status": reader_exit,
        })
        if executor_state != "active":
            alerts.append(f"executor service is {executor_state}")
        if timer_state != "active":
            alerts.append(f"reader timer is {timer_state}")
        if reader_result not in ("success", "unknown"):
            alerts.append(f"reader last result is {reader_result} exit={reader_exit}")
        metrics["reader_recent"] = "unknown"

    return MonitorReport(ok=not alerts, alerts=alerts, metrics=metrics)


def report_json(report: MonitorReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
