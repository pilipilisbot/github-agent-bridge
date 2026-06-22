from __future__ import annotations

import json
import sqlite3
from importlib import resources
from pathlib import Path

from .models import GitHubContext, Job, Notification, utc_now
from .parser import classify_github_action, classify_work_intent, extract_github_context
from .policy import Policy
from .session_correlation import session_id_for_job, session_id_for_job_attempt
from . import feedback
from .actors import trigger_actor_details_for_enqueue
from .intent_classifier import ParserResult, classify_notification_with_llm, should_classify_with_llm

SCHEMA_PACKAGE = "github_agent_bridge.sql"


def load_schema() -> str:
    """Read the packaged SQLite schema resource."""
    return resources.files(SCHEMA_PACKAGE).joinpath("schema.sql").read_text(encoding="utf-8")


SCHEMA = load_schema()
ACTIVE_STATUSES = ("pending", "running", "waiting_approval")
COALESCE_STATUSES = ("pending", "waiting_approval")


class JobQueue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def init(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            self._ensure_columns(con)

    def enqueue(self, n: Notification, policy: Policy) -> tuple[Job | None, str]:
        ctx = extract_github_context(n.body)
        action = classify_github_action(
            n.subject,
            n.body,
            policy.bot_logins,
            message_id=n.message_id,
        )
        intent = classify_work_intent(n.subject, n.body, policy.bot_logins)
        metadata: dict[str, object] = {"received_at": n.received_at}
        parser_result = ParserResult(action, intent)
        if should_classify_with_llm(n, ctx, parser_result, policy):
            metadata["intent_classifier"] = {
                "parser": {"action": action, "work_intent": intent},
                "enabled": True,
            }
            try:
                llm_result = classify_notification_with_llm(
                    n,
                    ctx,
                    parser_result,
                    policy.intent_classifier,
                    agent=policy.route_for(ctx.repo).agent,
                    prompt_template=(
                        feedback.load_prompt_override(path)
                        if (path := policy.prompt_overrides.rule_path("intent_classifier"))
                        else None
                    ),
                )
                classifier_metadata = llm_result.to_metadata()
                metadata["intent_classifier"] = {
                    **metadata["intent_classifier"],
                    "llm": classifier_metadata,
                }
                if llm_result.applied:
                    action = llm_result.action
                    intent = llm_result.work_intent
            except Exception as exc:
                metadata["intent_classifier"] = {
                    **metadata["intent_classifier"],
                    "error": str(exc)[:500],
                }
        decision = policy.decision(n, ctx, action)
        status = {"auto": "done", "ask": "waiting_approval", "deny": "denied"}.get(decision, "pending")
        now = utc_now()
        trigger_actor = trigger_actor_details_for_enqueue(n, ctx)
        if trigger_actor and trigger_actor.user_id:
            metadata["trigger_actor_id"] = trigger_actor.user_id
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                existing = con.execute(
                    f"SELECT * FROM jobs WHERE work_key=? AND status IN ({','.join('?' for _ in COALESCE_STATUSES)}) ORDER BY id LIMIT 1",
                    (ctx.work_key, *COALESCE_STATUSES),
                ).fetchone()
                if existing and decision == "auto_trusted":
                    con.execute(
                        "INSERT OR IGNORE INTO coalesced_notifications(job_id,uid,message_id,subject,trigger_actor,trigger_actor_avatar_url,context_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (existing["id"], n.uid, n.message_id, n.subject, trigger_actor.login if trigger_actor else None, trigger_actor.avatar_url if trigger_actor else None, ctx.to_json(), now),
                    )
                    con.execute("UPDATE jobs SET coalesced_count=coalesced_count+1, uid=?, message_id=message_id, subject=?, context_json=?, updated_at=? WHERE id=?", (n.uid, n.subject, ctx.to_json(), now, existing["id"]))
                    self._log(con, existing["id"], ctx.work_key, "coalesced", "Notification coalesced into active job", n.message_id)
                    con.commit()
                    if policy.feedback_learning.enabled and existing["message_id"] != n.message_id:
                        feedback.capture_feedback(
                            self.path,
                            n,
                            ctx,
                            action,
                            decision,
                            intent,
                            trigger_actor=trigger_actor.login if trigger_actor else None,
                            trigger_actor_avatar_url=trigger_actor.avatar_url if trigger_actor else None,
                        )
                    return self._row_to_job(existing), "coalesced"
                con.execute(
                    "INSERT INTO jobs(work_key,repo,thread,status,action,decision,work_intent,subject,message_id,uid,trigger_actor,trigger_actor_avatar_url,context_json,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ctx.work_key, ctx.repo, ctx.issue_number, status, action, decision, intent, n.subject, n.message_id, n.uid, trigger_actor.login if trigger_actor else None, trigger_actor.avatar_url if trigger_actor else None, ctx.to_json(), json.dumps(metadata), now, now),
                )
                job_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
                self._log(con, job_id, ctx.work_key, "queued" if status == "pending" else status, f"decision={decision} action={action}", n.message_id)
                con.commit()
                if policy.feedback_learning.enabled:
                    feedback.capture_feedback(
                        self.path,
                        n,
                        ctx,
                        action,
                        decision,
                        intent,
                        trigger_actor=trigger_actor.login if trigger_actor else None,
                        trigger_actor_avatar_url=trigger_actor.avatar_url if trigger_actor else None,
                    )
                return self.get(job_id), "enqueued"
            except sqlite3.IntegrityError:
                con.rollback()
                row = con.execute("SELECT * FROM jobs WHERE message_id=?", (n.message_id,)).fetchone()
                return self._row_to_job(row) if row else None, "duplicate"

    def claim_next(self, worker_id: str) -> Job | None:
        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT * FROM jobs j WHERE j.status='pending'
                AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.work_key=j.work_key AND r.status='running')
                ORDER BY j.created_at LIMIT 1"""
            ).fetchone()
            if not row:
                con.commit(); return None
            metadata = json.loads(row["metadata_json"] or "{}")
            if row["work_intent"] == "work_allowed":
                metadata["openclaw_session_id"] = session_id_for_job_attempt(int(row["id"]), int(row["attempts"]) + 1)
            else:
                metadata.setdefault("openclaw_session_id", session_id_for_job(int(row["id"])))
            con.execute(
                "UPDATE jobs SET status='running', locked_by=?, attempts=attempts+1, started_at=?, updated_at=?, metadata_json=? WHERE id=?",
                (worker_id, now, now, json.dumps(metadata, sort_keys=True), row["id"]),
            )
            self._log(con, row["id"], row["work_key"], "running", f"claimed by {worker_id}", None)
            self._session_event(con, row["id"], row["work_key"], metadata["openclaw_session_id"], "claimed", f"claimed by {worker_id}", None)
            self._progress(con, row["id"], row["work_key"], "semantic", "claimed", f"claimed by {worker_id}", None)
            con.commit()
            return self.get(int(row["id"]))

    def finish(self, job_id: int, status: str, summary: str, detail: str | None = None) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute("UPDATE jobs SET status=?, last_error=?, locked_by=NULL, finished_at=?, updated_at=? WHERE id=?", (status, detail if status == "blocked" else None, now, now, job_id))
            row = con.execute("SELECT work_key FROM jobs WHERE id=?", (job_id,)).fetchone()
            self._log(con, job_id, row["work_key"] if row else None, status, summary, detail)
            metadata = self._job_metadata(con, job_id)
            session_id = metadata.get("openclaw_session_id") or session_id_for_job(job_id)
            self._session_event(con, job_id, row["work_key"] if row else None, str(session_id), status, summary, detail)
            self._progress(con, job_id, row["work_key"] if row else None, "semantic", status, summary, detail)

    def requeue_running(self, job_id: int, summary: str, detail: str | None = None) -> bool:
        now = utc_now()
        with self.connect() as con:
            cur = con.execute("UPDATE jobs SET status='pending', locked_by=NULL, last_error=NULL, updated_at=? WHERE id=? AND status='running'", (now, job_id))
            if cur.rowcount:
                row = con.execute("SELECT work_key FROM jobs WHERE id=?", (job_id,)).fetchone()
                self._log(con, job_id, row["work_key"] if row else None, "retry", summary, detail)
            return bool(cur.rowcount)

    def update_work_intent(self, job_id: int, work_intent: str, summary: str) -> Job | None:
        now = utc_now()
        with self.connect() as con:
            row = con.execute("SELECT work_key FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            con.execute("UPDATE jobs SET work_intent=?, updated_at=? WHERE id=?", (work_intent, now, job_id))
            self._log(con, job_id, row["work_key"], "intent_update", summary, None)
        return self.get(job_id)

    def add_session_event(self, job_id: int, event_type: str, summary: str, detail: str | None = None) -> None:
        now = utc_now()
        with self.connect() as con:
            row = con.execute("SELECT work_key, metadata_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            metadata = json.loads(row["metadata_json"] or "{}")
            session_id = str(metadata.get("openclaw_session_id") or session_id_for_job(job_id))
            con.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now, job_id))
            self._session_event(con, job_id, row["work_key"], session_id, event_type, summary, detail)
            kind = "visible" if event_type.startswith("openclaw_") else "semantic"
            self._progress(con, job_id, row["work_key"], kind, event_type[:80], summary, detail)

    def list_jobs(self, status: str | None = None, limit: int = 20) -> list[Job]:
        sql = "SELECT * FROM jobs"
        args: tuple[object, ...] = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY id DESC LIMIT ?"
        args = (*args, limit)
        with self.connect() as con:
            return [j for j in (self._row_to_job(r) for r in con.execute(sql, args)) if j]

    def retry(self, job_id: int, *, actor: str | None = None) -> bool:
        now = utc_now()
        summary = f"job requeued by @{actor}" if actor else "job requeued"
        with self.connect() as con:
            cur = con.execute("UPDATE jobs SET status='pending', locked_by=NULL, last_error=NULL, updated_at=? WHERE id=? AND status IN ('blocked','denied','waiting_approval')", (now, job_id))
            if cur.rowcount:
                row = con.execute("SELECT work_key FROM jobs WHERE id=?", (job_id,)).fetchone()
                self._log(con, job_id, row["work_key"] if row else None, "retry", summary, None)
            return bool(cur.rowcount)

    def dismiss(self, job_id: int, reason: str) -> bool:
        now = utc_now()
        with self.connect() as con:
            cur = con.execute(
                "UPDATE jobs SET status='done', locked_by=NULL, last_error=NULL, finished_at=COALESCE(finished_at, ?), updated_at=? WHERE id=? AND status IN ('blocked','denied','waiting_approval')",
                (now, now, job_id),
            )
            if cur.rowcount:
                row = con.execute("SELECT work_key FROM jobs WHERE id=?", (job_id,)).fetchone()
                self._log(con, job_id, row["work_key"] if row else None, "dismissed", "job dismissed manually", reason)
            return bool(cur.rowcount)

    def unlock_stale(self, older_than_seconds: int, job_ids: list[int] | None = None) -> int:
        with self.connect() as con:
            args: list[object] = [older_than_seconds]
            sql = "SELECT id, work_key FROM jobs WHERE status='running' AND started_at IS NOT NULL AND (julianday('now') - julianday(started_at)) * 86400 > ?"
            if job_ids is not None:
                if not job_ids:
                    return 0
                sql += f" AND id IN ({','.join('?' for _ in job_ids)})"
                args.extend(job_ids)
            rows = con.execute(sql, args).fetchall()
            for row in rows:
                con.execute("UPDATE jobs SET status='pending', locked_by=NULL, updated_at=? WHERE id=?", (utc_now(), row["id"]))
                self._log(con, row["id"], row["work_key"], "unlock_stale", f"running job older than {older_than_seconds}s requeued", None)
            return len(rows)

    def get(self, job_id: int) -> Job | None:
        with self.connect() as con:
            return self._row_to_job(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def coalesced_contexts(self, job_id: int) -> list[GitHubContext]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT context_json FROM coalesced_notifications WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [GitHubContext.from_json(row["context_json"]) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as con:
            return {r["status"]: r["count"] for r in con.execute("SELECT status, count(*) count FROM jobs GROUP BY status")}

    def pending_age_seconds(self) -> int | None:
        with self.connect() as con:
            row = con.execute("SELECT CAST((julianday('now') - julianday(min(created_at))) * 86400 AS INTEGER) age FROM jobs WHERE status='pending'").fetchone()
            return None if row is None or row["age"] is None else int(row["age"])

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_state(self, key: str, default: str = "") -> str:
        with self.connect() as con:
            row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def _log(self, con: sqlite3.Connection, job_id: int | None, work_key: str | None, phase: str, summary: str, detail: str | None) -> None:
        con.execute("INSERT INTO worklog(ts,job_id,work_key,phase,summary,detail) VALUES(?,?,?,?,?,?)", (utc_now(), job_id, work_key, phase, summary, detail))

    def _session_event(self, con: sqlite3.Connection, job_id: int, work_key: str | None, session_id: str, event_type: str, summary: str, detail: str | None) -> None:
        con.execute(
            "INSERT INTO job_session_events(ts,job_id,work_key,session_id,event_type,summary,detail) VALUES(?,?,?,?,?,?,?)",
            (utc_now(), job_id, work_key, session_id, event_type, summary, detail),
        )

    def _progress(self, con: sqlite3.Connection, job_id: int, work_key: str | None, kind: str, phase: str, summary: str, detail: str | None) -> None:
        con.execute(
            "INSERT INTO job_progress(ts,job_id,work_key,kind,phase,summary,detail) VALUES(?,?,?,?,?,?,?)",
            (utc_now(), job_id, work_key, kind, phase, summary, detail),
        )

    def _job_metadata(self, con: sqlite3.Connection, job_id: int) -> dict[str, object]:
        row = con.execute("SELECT metadata_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return {}
        return json.loads(row["metadata_json"] or "{}")

    def _ensure_columns(self, con: sqlite3.Connection) -> None:
        tables = {
            "jobs": {"trigger_actor": "TEXT", "trigger_actor_avatar_url": "TEXT"},
            "coalesced_notifications": {"trigger_actor": "TEXT", "trigger_actor_avatar_url": "TEXT"},
        }
        for table, columns in tables.items():
            existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _row_to_job(self, row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        return Job(id=row["id"], work_key=row["work_key"], repo=row["repo"], thread=row["thread"], status=row["status"], action=row["action"], work_intent=row["work_intent"], subject=row["subject"], message_id=row["message_id"], uid=row["uid"], trigger_actor=row["trigger_actor"], trigger_actor_avatar_url=row["trigger_actor_avatar_url"], context=GitHubContext.from_json(row["context_json"]), attempts=row["attempts"], coalesced_count=row["coalesced_count"], last_error=row["last_error"], locked_by=row["locked_by"], created_at=row["created_at"], updated_at=row["updated_at"], metadata=json.loads(row["metadata_json"] or "{}"))
