from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .actors import normalize_github_login
from .models import utc_now
from .queue import JobQueue

PushSender = Callable[[dict[str, Any], dict[str, Any]], None]


def _connect(db: str | Path) -> sqlite3.Connection:
    queue = JobQueue(db)
    return queue.connect()


def _validate_subscription(subscription: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys")
    if not endpoint or not endpoint.startswith("https://"):
        raise ValueError("subscription_endpoint_required")
    if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("subscription_keys_required")
    return endpoint, subscription


def save_subscription(db: str | Path, user_login: str, subscription: dict[str, Any]) -> dict[str, Any]:
    login = normalize_github_login(user_login).lower()
    if not login:
        raise ValueError("user_login_required")
    endpoint, payload = _validate_subscription(subscription)
    now = utc_now()
    with _connect(db) as con:
        con.execute(
            """
            INSERT INTO web_push_subscriptions(user_login, endpoint, subscription_json, created_at, updated_at, disabled_at, last_error)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
              user_login=excluded.user_login,
              subscription_json=excluded.subscription_json,
              updated_at=excluded.updated_at,
              disabled_at=NULL,
              last_error=NULL
            """,
            (login, endpoint, json.dumps(payload, sort_keys=True), now, now, None, None),
        )
        row = con.execute("SELECT * FROM web_push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
    return _subscription_row(row)


def delete_subscription(db: str | Path, user_login: str, endpoint: str) -> bool:
    login = normalize_github_login(user_login).lower()
    with _connect(db) as con:
        cur = con.execute(
            "UPDATE web_push_subscriptions SET disabled_at=?, updated_at=? WHERE user_login=? AND endpoint=? AND disabled_at IS NULL",
            (utc_now(), utc_now(), login, endpoint),
        )
    return bool(cur.rowcount)


def subscription_status(db: str | Path, user_login: str) -> dict[str, Any]:
    login = normalize_github_login(user_login).lower()
    with _connect(db) as con:
        rows = con.execute(
            """
            SELECT id, endpoint, updated_at, last_success_at, last_error
            FROM web_push_subscriptions
            WHERE user_login=? AND disabled_at IS NULL
            ORDER BY updated_at DESC
            """,
            (login,),
        ).fetchall()
    return {
        "enabled": bool(rows),
        "subscriptions": [dict(row) for row in rows],
    }


def notify_job_completion(
    db: str | Path,
    *,
    actors: list[str],
    job_id: int,
    work_key: str,
    status: str,
    summary: str,
    detail: str | None = None,
    followup_url: str | None = None,
    dashboard_url: str | None = None,
    sender: PushSender | None = None,
) -> dict[str, Any]:
    recipients = _recipient_logins(actors)
    if not recipients:
        return {"recipients": [], "attempted": 0, "sent": 0, "failed": 0}
    subscriptions = _active_subscriptions(db, recipients)
    attempted = sent = failed = 0
    payload = _job_completion_payload(
        job_id=job_id,
        work_key=work_key,
        status=status,
        summary=summary,
        detail=detail,
        followup_url=followup_url,
        dashboard_url=dashboard_url,
    )
    push = sender or _send_web_push
    for row in subscriptions:
        attempted += 1
        try:
            push(json.loads(row["subscription_json"]), payload)
        except Exception as exc:
            failed += 1
            _mark_delivery(db, int(row["id"]), error=str(exc)[:500])
        else:
            sent += 1
            _mark_delivery(db, int(row["id"]))
    return {"recipients": recipients, "attempted": attempted, "sent": sent, "failed": failed}


def _recipient_logins(actors: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for actor in actors:
        login = normalize_github_login(actor)
        if not login or login.endswith("[bot]"):
            continue
        key = login.lower()
        if key == "github" or key in seen:
            continue
        seen.add(key)
        recipients.append(key)
    return recipients


def _active_subscriptions(db: str | Path, recipients: list[str]) -> list[sqlite3.Row]:
    if not recipients:
        return []
    placeholders = ",".join("?" for _ in recipients)
    with _connect(db) as con:
        return con.execute(
            f"""
            SELECT *
            FROM web_push_subscriptions
            WHERE disabled_at IS NULL AND lower(user_login) IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            tuple(recipients),
        ).fetchall()


def _job_completion_payload(
    *,
    job_id: int,
    work_key: str,
    status: str,
    summary: str,
    detail: str | None,
    followup_url: str | None,
    dashboard_url: str | None,
) -> dict[str, Any]:
    base_url = (dashboard_url or os.getenv("GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL", "")).rstrip("/")
    dashboard_job_url = f"{base_url}/jobs/{job_id}" if base_url else f"/jobs/{job_id}"
    return {
        "title": f"Bridge job {status}",
        "body": f"{work_key} finished with status {status}",
        "tag": f"gab-job-{job_id}",
        "url": dashboard_job_url,
        "job_url": dashboard_job_url,
        "github_url": followup_url,
        "followup_url": followup_url,
        "job_id": job_id,
        "work_key": work_key,
        "status": status,
        "summary": summary,
        "detail": detail,
        "timestamp": utc_now(),
    }


def _send_web_push(subscription: dict[str, Any], payload: dict[str, Any]) -> None:
    private_key = os.getenv("GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PRIVATE_KEY", "").strip()
    contact = os.getenv("GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_CONTACT", "mailto:admin@example.com").strip()
    if not private_key:
        raise RuntimeError("web_push_vapid_private_key_not_configured")
    from pywebpush import webpush

    webpush(
        subscription_info=subscription,
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=private_key,
        vapid_claims={"sub": contact},
    )


def _mark_delivery(db: str | Path, subscription_id: int, error: str | None = None) -> None:
    now = utc_now()
    with _connect(db) as con:
        if error:
            con.execute("UPDATE web_push_subscriptions SET updated_at=?, last_error=? WHERE id=?", (now, error, subscription_id))
        else:
            con.execute("UPDATE web_push_subscriptions SET updated_at=?, last_success_at=?, last_error=NULL WHERE id=?", (now, now, subscription_id))


def _subscription_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_login": row["user_login"],
        "endpoint": row["endpoint"],
        "updated_at": row["updated_at"],
        "last_success_at": row["last_success_at"],
        "last_error": row["last_error"],
        "disabled_at": row["disabled_at"],
    }
