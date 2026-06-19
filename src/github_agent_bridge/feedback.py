from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from .models import GitHubContext, Notification, utc_now
from .parser import extract_github_context
from .policy import Policy


ACTIONABLE_FEEDBACK_ACTIONS = {"reply_comment", "open_issue", "submit_review", "docs_update", "content_change"}
FEEDBACK_DECISIONS = {"auto_trusted", "ask"}
PROMPT_RULES_PACKAGE = "github_agent_bridge.prompt_rules"


def load_prompt_rule(name: str) -> str:
    return resources.files(PROMPT_RULES_PACKAGE).joinpath(name).read_text(encoding="utf-8").strip() + "\n"


def load_prompt_override(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip() + "\n"


FEEDBACK_CLASSIFIER_PROMPT = load_prompt_rule("feedback_classifier.md")


def compact(text: str, limit: int = 1600) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def event_id(n: Notification) -> str:
    seed = n.message_id or f"{n.uid}:{n.subject}:{n.received_at}"
    return "github-agent-bridge-" + uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]


def canonical_key(scope: str, rule_type: str, rule: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", f"{scope}:{rule_type}:{rule}".lower()).strip("-")
    return short_hash(normalized)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def capture_feedback(
    db_path: str | Path,
    n: Notification,
    ctx: GitHubContext,
    action: str,
    decision: str,
    work_intent: str,
    trigger_actor: str | None = None,
    trigger_actor_avatar_url: str | None = None,
) -> bool:
    """Capture feedback candidates into bridge-owned storage.

    This deliberately does not synthesize rules. The bridge records auditable
    evidence; only curated rows in feedback_rules are injected into agents.
    """
    if decision not in FEEDBACK_DECISIONS or action not in ACTIONABLE_FEEDBACK_ACTIONS:
        return False

    repo = ctx.repo or "unknown/repo"
    scope = f"repo:{repo}" if repo != "unknown/repo" else "github"
    github_context = json.loads(ctx.to_json())
    github_urls = github_context.get("urls") if isinstance(github_context.get("urls"), list) else []
    context = {
        "subject": n.subject,
        "bridge_action": action,
        "decision": decision,
        "work_intent": work_intent,
        "work_key": ctx.work_key,
        "message_id": n.message_id,
        "uid": n.uid,
        "trigger_actor": trigger_actor,
        "trigger_actor_avatar_url": trigger_actor_avatar_url,
        "github_context": github_context,
        "github_urls": github_urls,
        "source_url": github_urls[0] if github_urls else None,
    }

    try:
        with _connect(db_path) as con:
            con.execute(
                """INSERT OR IGNORE INTO feedback_events(
                    id, occurred_at, captured_at, source, scope, actor, comment, context_json,
                    classification, confidence, memorable
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id(n),
                    n.received_at,
                    utc_now(),
                    "github-agent-bridge",
                    scope,
                    trigger_actor or "github",
                    compact(f"{n.subject}\n\n{n.body}"),
                    json.dumps(context, ensure_ascii=False, sort_keys=True),
                    "unreviewed",
                    0.0,
                    0,
                ),
            )
    except sqlite3.Error:
        return False
    return True


def pending_events(db_path: str | Path, scope: str = "", limit: int = 10) -> list[dict[str, Any]]:
    clauses = [
        """NOT EXISTS (
            SELECT 1 FROM feedback_rule_proposals p
            WHERE p.event_id=feedback_events.id
            AND p.status != 'error'
        )"""
    ]
    args: list[Any] = []
    if scope:
        clauses.append("(scope=? OR scope LIKE ?)")
        args.extend([scope, f"{scope}:%"])
    sql = "SELECT * FROM feedback_events WHERE " + " AND ".join(clauses) + " ORDER BY occurred_at ASC, id ASC LIMIT ?"
    args.append(limit)
    with _connect(db_path) as con:
        return [_enrich_event(con, _event_dict(row)) for row in con.execute(sql, args)]


def add_rule(
    db_path: str | Path,
    scope: str,
    rule_type: str,
    rule: str,
    confidence: float,
    source_events: list[str] | None = None,
) -> dict[str, Any]:
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    clean_rule = compact(rule, 600)
    if not scope.strip():
        raise ValueError("scope is required")
    if not rule_type.strip():
        raise ValueError("rule type is required")
    if not clean_rule:
        raise ValueError("rule is required")

    now = utc_now()
    rule_id = canonical_key(scope, rule_type, clean_rule)
    events = sorted(set(source_events or []))
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM feedback_rules WHERE id=?", (rule_id,)).fetchone()
        if row:
            events = sorted(set(json.loads(row["source_events_json"] or "[]") + events))
            confidence = max(float(row["confidence"]), confidence)
            observations = int(row["observations"]) + 1
            con.execute(
                """UPDATE feedback_rules
                SET confidence=?, last_seen=?, source_events_json=?, observations=?
                WHERE id=?""",
                (confidence, now, json.dumps(events, ensure_ascii=False, sort_keys=True), observations, rule_id),
            )
        else:
            con.execute(
                """INSERT INTO feedback_rules(
                    id, scope, type, confidence, rule, created_at, last_seen, source_events_json, observations
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (rule_id, scope, rule_type, confidence, clean_rule, now, now, json.dumps(events, ensure_ascii=False, sort_keys=True), 1),
            )
    return next(rule for rule in list_rules(db_path, scope=scope, min_confidence=0) if rule["id"] == rule_id)


def proposal_id(event_id: str, scope: str, rule_type: str, rule: str) -> str:
    return "feedback-proposal-" + canonical_key(event_id, rule_type, f"{scope}:{rule}")


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM output did not contain a JSON object")
    return json.loads(stripped[start : end + 1])


def _openclaw_text_from_json(raw: str) -> str:
    data = json.loads(raw)
    for key in ("message", "reply", "text", "content", "output"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(data.get("result"), dict):
        payloads = data["result"].get("payloads")
        if isinstance(payloads, list):
            for payload in payloads:
                if isinstance(payload, dict) and isinstance(payload.get("text"), str) and payload["text"].strip():
                    return payload["text"]
        for key in ("message", "reply", "text", "content", "output"):
            value = data["result"].get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw


def build_learning_prompt(event: dict[str, Any], prompt_template: str | None = None) -> str:
    template = prompt_template or FEEDBACK_CLASSIFIER_PROMPT
    return template.format(event_json=json.dumps(event, ensure_ascii=False, sort_keys=True))


def repo_from_event_scope(event: dict[str, Any]) -> str | None:
    scope = str(event.get("scope") or "")
    if not scope.startswith("repo:"):
        return None
    repo = scope.removeprefix("repo:").strip().lower()
    return repo or None


def route_agent_for_event(event: dict[str, Any], policy: Policy | None = None) -> str | None:
    repo = repo_from_event_scope(event)
    if not repo or not policy:
        return None
    return policy.route_for(repo).agent


def is_model_override_not_allowed(exc: Exception) -> bool:
    message = str(exc)
    return "Model override" in message and "not allowed" in message


def session_id_for_agent(base_session_id: str, agent: str | None) -> str:
    if not agent:
        return base_session_id
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent).strip("-")
    return f"{base_session_id}-{suffix}" if suffix else base_session_id


def session_id_for_event(base_session_id: str, agent: str | None, event_id: str) -> str:
    agent_session_id = session_id_for_agent(base_session_id, agent)
    event_suffix = short_hash(event_id)
    return f"{agent_session_id}-{event_suffix}"


def classify_event_with_llm(
    event: dict[str, Any],
    openclaw_bin: str = "openclaw",
    agent: str | None = None,
    model: str | None = None,
    thinking: str = "low",
    session_id: str = "github-agent-bridge-feedback",
    timeout: int = 180,
    prompt_template: str | None = None,
) -> dict[str, Any]:
    cmd = [
        openclaw_bin,
        "agent",
        "--json",
        "--session-id",
        session_id,
        "--timeout",
        str(timeout),
        "--thinking",
        thinking,
        "--message",
        build_learning_prompt(event, prompt_template),
    ]
    if agent:
        cmd.extend(["--agent", agent])
    if model:
        cmd.extend(["--model", model])
    env = os.environ.copy()
    openclaw_dir = os.path.dirname(openclaw_bin)
    if openclaw_dir:
        env["PATH"] = openclaw_dir + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout + 30, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"openclaw exited {proc.returncode}")
    text = _openclaw_text_from_json(proc.stdout)
    result = _extract_json_object(text)
    return normalize_proposal(event, result)


def normalize_proposal(event: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    is_feedback = bool(result.get("is_feedback"))
    scope = str(result.get("scope") or event["scope"]).strip()
    if not (scope == "global" or scope.startswith("repo:") or scope.startswith("org:")):
        scope = event["scope"]
    rule_type = str(result.get("type") or "domain_context").strip() or "domain_context"
    rule = compact(str(result.get("rule") or ""), 600)
    confidence = float(result.get("confidence") or 0)
    confidence = min(1.0, max(0.0, confidence))
    reason = compact(str(result.get("reason") or ""), 500)
    if not is_feedback:
        rule = ""
        confidence = min(confidence, 0.49)
    return {
        "event_id": event["id"],
        "is_feedback": is_feedback,
        "scope": scope,
        "type": rule_type,
        "rule": rule,
        "confidence": confidence,
        "reason": reason,
    }


def store_proposal(
    db_path: str | Path,
    proposal: dict[str, Any],
    auto_approve_confidence: float,
    model: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    status = "error" if error else "rejected"
    if not error and proposal["is_feedback"] and proposal["rule"] and proposal["confidence"] >= auto_approve_confidence:
        status = "approved"
    elif not error and proposal["is_feedback"] and proposal["rule"]:
        status = "proposed"
    pid = proposal_id(proposal["event_id"], proposal["scope"], proposal["type"], proposal["rule"] or proposal.get("reason", ""))
    with _connect(db_path) as con:
        con.execute(
            """INSERT OR REPLACE INTO feedback_rule_proposals(
                id, event_id, created_at, updated_at, status, scope, type, confidence, rule, reason, model, error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                proposal["event_id"],
                now,
                now,
                status,
                proposal["scope"],
                proposal["type"],
                proposal["confidence"],
                proposal["rule"],
                proposal.get("reason", ""),
                model or "",
                error,
            ),
        )
    if status == "approved":
        add_rule(
            db_path,
            proposal["scope"],
            proposal["type"],
            proposal["rule"],
            proposal["confidence"],
            [proposal["event_id"], pid],
        )
    return next(item for item in list_proposals(db_path, status="", limit=100) if item["id"] == pid)


def approve_proposal(db_path: str | Path, proposal_id: str, *, react: bool = False, gh_bin: str = "gh") -> dict[str, Any] | None:
    now = utc_now()
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM feedback_rule_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            return None
        con.execute("UPDATE feedback_rule_proposals SET status='approved', updated_at=?, error=NULL WHERE id=?", (now, proposal_id))
    add_rule(db_path, row["scope"], row["type"], row["rule"], float(row["confidence"]), [row["event_id"], proposal_id])
    if react:
        react_to_feedback_event(db_path, row["event_id"], gh_bin=gh_bin)
    return get_proposal(db_path, proposal_id)


def reject_proposal(db_path: str | Path, proposal_id: str) -> dict[str, Any] | None:
    now = utc_now()
    with _connect(db_path) as con:
        row = con.execute("SELECT id FROM feedback_rule_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            return None
        con.execute("UPDATE feedback_rule_proposals SET status='rejected', updated_at=? WHERE id=?", (now, proposal_id))
    return get_proposal(db_path, proposal_id)


def get_proposal(db_path: str | Path, proposal_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM feedback_rule_proposals WHERE id=?", (proposal_id,)).fetchone()
        return _proposal_dict(row) if row else None


def delete_rule(db_path: str | Path, rule_id: str) -> bool:
    with _connect(db_path) as con:
        cur = con.execute("DELETE FROM feedback_rules WHERE id=?", (rule_id,))
        return cur.rowcount > 0


def validate_rule_scope(scope: str) -> str:
    normalized = (scope or "").strip().lower()
    if normalized == "global":
        return normalized
    if normalized.startswith("org:"):
        org = normalized.removeprefix("org:").strip()
        if org and "/" not in org:
            return f"org:{org}"
    if normalized.startswith("repo:"):
        repo = normalized.removeprefix("repo:").strip()
        if repo.count("/") == 1 and all(part.strip() for part in repo.split("/", 1)):
            return f"repo:{repo}"
    raise ValueError("scope must be global, org:<owner>, or repo:<owner>/<name>")


def update_rule_scope(db_path: str | Path, rule_id: str, scope: str) -> dict[str, Any] | None:
    new_scope = validate_rule_scope(scope)
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM feedback_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            return None
        if row["scope"] == new_scope:
            return _rule_dict(con, row)

        new_id = canonical_key(new_scope, row["type"], row["rule"])
        source_events = json.loads(row["source_events_json"] or "[]")
        existing = con.execute("SELECT * FROM feedback_rules WHERE id=?", (new_id,)).fetchone()
        if existing and existing["id"] != rule_id:
            merged_events = sorted(set(json.loads(existing["source_events_json"] or "[]") + source_events))
            con.execute(
                """UPDATE feedback_rules
                SET confidence=?, created_at=?, last_seen=?, source_events_json=?, observations=?
                WHERE id=?""",
                (
                    max(float(existing["confidence"]), float(row["confidence"])),
                    min(str(existing["created_at"]), str(row["created_at"])),
                    max(str(existing["last_seen"]), str(row["last_seen"])),
                    json.dumps(merged_events, ensure_ascii=False, sort_keys=True),
                    int(existing["observations"]) + int(row["observations"]),
                    existing["id"],
                ),
            )
            con.execute("DELETE FROM feedback_rules WHERE id=?", (rule_id,))
            merged = con.execute("SELECT * FROM feedback_rules WHERE id=?", (new_id,)).fetchone()
            return _rule_dict(con, merged) if merged else None

        con.execute(
            """UPDATE feedback_rules
            SET id=?, scope=?
            WHERE id=?""",
            (new_id, new_scope, rule_id),
        )
        updated = con.execute("SELECT * FROM feedback_rules WHERE id=?", (new_id,)).fetchone()
        return _rule_dict(con, updated) if updated else None


def reaction_endpoint(ctx: GitHubContext) -> str | None:
    if not ctx.repo:
        return None
    if ctx.comment_id:
        return f"repos/{ctx.repo}/issues/comments/{ctx.comment_id}/reactions"
    if ctx.review_comment_id:
        return f"repos/{ctx.repo}/pulls/comments/{ctx.review_comment_id}/reactions"
    if ctx.commit_comment_id:
        return f"repos/{ctx.repo}/comments/{ctx.commit_comment_id}/reactions"
    return None


def react_to_feedback_comment(event: dict[str, Any], gh_bin: str = "gh") -> bool:
    ctx = _github_context_from_event(event)
    if ctx.review_id and not ctx.review_comment_id:
        resolved = resolve_review_comment_source(event, gh_bin=gh_bin)
        if resolved:
            ctx = resolved
    endpoint = reaction_endpoint(ctx)
    if not endpoint:
        return False
    try:
        result = subprocess.run(
            [
                gh_bin,
                "api",
                "-X",
                "POST",
                endpoint,
                "-f",
                "content=heart",
                "-H",
                "Accept: application/vnd.github+json",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def react_to_feedback_event(db_path: str | Path, event_id: str, gh_bin: str = "gh") -> bool:
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM feedback_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return False
        event = _enrich_event(con, _event_dict(row))
    return react_to_feedback_comment(event, gh_bin=gh_bin)


def learn_from_events(
    db_path: str | Path,
    openclaw_bin: str = "openclaw",
    gh_bin: str = "gh",
    policy: Policy | None = None,
    model: str | None = None,
    thinking: str = "low",
    session_id: str = "github-agent-bridge-feedback",
    limit: int = 10,
    auto_approve_confidence: float = 0.8,
    timeout: int = 180,
    prompt_template: str | None = None,
) -> dict[str, Any]:
    events = pending_events(db_path, limit=limit)
    proposals = []
    reacted = 0
    for event in events:
        try:
            event = persist_resolved_review_comment_source(db_path, event, gh_bin=gh_bin)
            agent = route_agent_for_event(event, policy)
            event_session_id = session_id_for_event(session_id, agent, event["id"])
            model_used = model
            try:
                proposal = classify_event_with_llm(
                    event,
                    openclaw_bin=openclaw_bin,
                    agent=agent,
                    model=model,
                    thinking=thinking,
                    session_id=event_session_id,
                    timeout=timeout,
                    prompt_template=prompt_template,
                )
            except Exception as exc:
                if not model or not is_model_override_not_allowed(exc):
                    raise
                model_used = None
                proposal = classify_event_with_llm(
                    event,
                    openclaw_bin=openclaw_bin,
                    agent=agent,
                    model=None,
                    thinking=thinking,
                    session_id=event_session_id,
                    timeout=timeout,
                    prompt_template=prompt_template,
                )
            stored = store_proposal(db_path, proposal, auto_approve_confidence, model=model_used or "")
            proposals.append(stored)
            if stored["status"] == "approved" and react_to_feedback_comment(event, gh_bin=gh_bin):
                reacted += 1
        except Exception as exc:
            fallback = {
                "event_id": event["id"],
                "is_feedback": False,
                "scope": event["scope"],
                "type": "error",
                "rule": "",
                "confidence": 0.0,
                "reason": "classification failed",
            }
            proposals.append(store_proposal(db_path, fallback, auto_approve_confidence, model=model or "", error=str(exc)))
    return {
        "processed": len(events),
        "approved": sum(1 for item in proposals if item["status"] == "approved"),
        "proposed": sum(1 for item in proposals if item["status"] == "proposed"),
        "rejected": sum(1 for item in proposals if item["status"] == "rejected"),
        "errors": sum(1 for item in proposals if item["status"] == "error"),
        "reacted": reacted,
        "proposals": proposals,
    }


def list_proposals(db_path: str | Path, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
    args: list[Any] = []
    sql = "SELECT * FROM feedback_rule_proposals"
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with _connect(db_path) as con:
        proposals = []
        for row in con.execute(sql, args):
            event_row = con.execute("SELECT * FROM feedback_events WHERE id=?", (row["event_id"],)).fetchone()
            source_event = _enrich_event(con, _event_dict(event_row)) if event_row else None
            proposals.append(_proposal_dict(row, source_event=source_event))
        return proposals


def _proposal_dict(row: sqlite3.Row, source_event: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "scope": row["scope"],
        "type": row["type"],
        "confidence": row["confidence"],
        "rule": row["rule"],
        "reason": row["reason"],
        "model": row["model"],
        "error": row["error"],
        "source_event": source_event,
    }


def list_events(db_path: str | Path, scope: str = "", limit: int = 20) -> list[dict[str, Any]]:
    clauses = []
    args: list[Any] = []
    if scope:
        clauses.append("(scope=? OR scope LIKE ?)")
        args.extend([scope, f"{scope}:%"])
    sql = "SELECT * FROM feedback_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with _connect(db_path) as con:
        return [_enrich_event(con, _event_dict(row)) for row in con.execute(sql, args)]


def list_repositories(db_path: str | Path) -> list[str]:
    with _connect(db_path) as con:
        rows = con.execute(
            """
            SELECT scope FROM feedback_events
            UNION
            SELECT scope FROM feedback_rules
            UNION
            SELECT scope FROM feedback_rule_proposals
            """
        ).fetchall()
    repos = {
        str(row["scope"]).removeprefix("repo:")
        for row in rows
        if str(row["scope"]).startswith("repo:") and str(row["scope"]).removeprefix("repo:")
    }
    return sorted(repos)


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "captured_at": row["captured_at"],
        "source": row["source"],
        "scope": row["scope"],
        "actor": row["actor"],
        "comment": row["comment"],
        "context": json.loads(row["context_json"] or "{}"),
        "classification": row["classification"],
        "confidence": row["confidence"],
        "memorable": bool(row["memorable"]),
    }


def _safe_json_object(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _github_urls_from_context(context: dict[str, Any]) -> list[str]:
    urls = context.get("github_urls")
    if isinstance(urls, list):
        return [str(url) for url in urls if isinstance(url, str) and url.strip()]
    github_context = context.get("github_context")
    if isinstance(github_context, dict):
        nested_urls = github_context.get("urls")
        if isinstance(nested_urls, list):
            return [str(url) for url in nested_urls if isinstance(url, str) and url.strip()]
    return []


def _source_from_stored_context(context: dict[str, Any]) -> dict[str, Any]:
    github_context = context.get("github_context") if isinstance(context.get("github_context"), dict) else {}
    urls = _github_urls_from_context(context)
    return {
        "trigger_actor": context.get("trigger_actor"),
        "trigger_actor_avatar_url": context.get("trigger_actor_avatar_url"),
        "github_urls": urls,
        "source_url": context.get("source_url") or (urls[0] if urls else None),
        "github_context": github_context,
        "source_job_id": context.get("source_job_id"),
        "source_table": context.get("source_table"),
    }


def _source_from_row(row: sqlite3.Row, table: str) -> dict[str, Any]:
    github_context = _safe_json_object(row["context_json"])
    urls = github_context.get("urls") if isinstance(github_context.get("urls"), list) else []
    clean_urls = [str(url) for url in urls if isinstance(url, str) and url.strip()]
    return {
        "trigger_actor": row["trigger_actor"],
        "trigger_actor_avatar_url": row["trigger_actor_avatar_url"],
        "github_urls": clean_urls,
        "source_url": clean_urls[0] if clean_urls else None,
        "github_context": github_context,
        "source_job_id": row["id"] if table == "jobs" else row["job_id"],
        "source_table": "job" if table == "jobs" else "coalesced_notification",
    }


def _source_for_message_id(con: sqlite3.Connection, message_id: str | None) -> dict[str, Any]:
    if not message_id:
        return {}
    job = con.execute(
        "SELECT id, trigger_actor, trigger_actor_avatar_url, context_json FROM jobs WHERE message_id=? ORDER BY id DESC LIMIT 1",
        (message_id,),
    ).fetchone()
    if job:
        return _source_from_row(job, "jobs")
    coalesced = con.execute(
        "SELECT id, job_id, trigger_actor, trigger_actor_avatar_url, context_json FROM coalesced_notifications WHERE message_id=? ORDER BY id DESC LIMIT 1",
        (message_id,),
    ).fetchone()
    if coalesced:
        return _source_from_row(coalesced, "coalesced_notifications")
    return {}


def _fallback_source_from_comment(comment: str) -> dict[str, Any]:
    ctx = extract_github_context(comment)
    github_context = _safe_json_object(ctx.to_json())
    urls = github_context.get("urls") if isinstance(github_context.get("urls"), list) else []
    clean_urls = [str(url) for url in urls if isinstance(url, str) and url.strip()]
    return {
        "github_urls": clean_urls,
        "source_url": clean_urls[0] if clean_urls else None,
        "github_context": github_context,
        "source_table": "feedback_comment" if clean_urls else None,
    }


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _github_context_from_event(event: dict[str, Any]) -> GitHubContext:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    github_context = event.get("github_context")
    if not isinstance(github_context, dict):
        github_context = context.get("github_context")
    if isinstance(github_context, dict):
        try:
            return GitHubContext.from_json(json.dumps(github_context))
        except (TypeError, ValueError):
            pass
    return extract_github_context(str(event.get("comment") or ""))


def _review_comment_candidates(ctx: GitHubContext, gh_bin: str = "gh") -> list[dict[str, Any]]:
    if not (ctx.repo and ctx.issue_number and ctx.review_id):
        return []
    try:
        result = subprocess.run(
            [
                gh_bin,
                "api",
                f"repos/{ctx.repo}/pulls/{ctx.issue_number}/reviews/{ctx.review_id}/comments",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    try:
        comments = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in comments if isinstance(item, dict)]


def _matching_review_comment(ctx: GitHubContext, text: str, gh_bin: str = "gh") -> dict[str, Any] | None:
    normalized_text = re.sub(r"\s+", " ", text).strip().lower()
    candidates = _review_comment_candidates(ctx, gh_bin=gh_bin)
    if not candidates:
        return None
    for comment in candidates:
        body = str(comment.get("body") or "")
        normalized_body = re.sub(r"\s+", " ", body).strip().lower()
        if normalized_body and normalized_body in normalized_text:
            return comment
    return None


def _context_with_review_comment(ctx: GitHubContext, comment: dict[str, Any]) -> GitHubContext | None:
    try:
        review_comment_id = int(comment["id"])
    except (KeyError, TypeError, ValueError):
        return None
    html_url = str(comment.get("html_url") or "").strip()
    urls = [html_url, *[url for url in ctx.urls if url != html_url]] if html_url else ctx.urls
    return GitHubContext(
        urls=urls,
        repo=ctx.repo,
        issue_number=ctx.issue_number,
        comment_id=ctx.comment_id,
        review_id=ctx.review_id,
        review_comment_id=review_comment_id,
        commit_comment_id=ctx.commit_comment_id,
        commit_sha=ctx.commit_sha,
        target_kind="review_comment",
        workflow_run_id=ctx.workflow_run_id,
    )


def resolve_review_comment_source(event: dict[str, Any], gh_bin: str = "gh") -> GitHubContext | None:
    ctx = _github_context_from_event(event)
    if ctx.review_comment_id or not ctx.review_id:
        return None
    comment = _matching_review_comment(ctx, str(event.get("comment") or ""), gh_bin=gh_bin)
    return _context_with_review_comment(ctx, comment) if comment else None


def persist_resolved_review_comment_source(db_path: str | Path, event: dict[str, Any], gh_bin: str = "gh") -> dict[str, Any]:
    resolved = resolve_review_comment_source(event, gh_bin=gh_bin)
    if not resolved:
        return event
    source_url = resolved.short_url
    with _connect(db_path) as con:
        row = con.execute("SELECT context_json FROM feedback_events WHERE id=?", (event["id"],)).fetchone()
        if not row:
            return event
        context = _safe_json_object(row["context_json"])
        github_context = _safe_json_object(resolved.to_json())
        context["github_context"] = github_context
        context["github_urls"] = resolved.urls
        context["source_url"] = source_url
        con.execute(
            "UPDATE feedback_events SET context_json=? WHERE id=?",
            (json.dumps(context, ensure_ascii=False, sort_keys=True), event["id"]),
        )
    enriched = {
        **event,
        "github_context": _safe_json_object(resolved.to_json()),
        "github_urls": resolved.urls,
        "source_url": source_url,
    }
    if isinstance(enriched.get("context"), dict):
        enriched["context"] = {
            **enriched["context"],
            "github_context": enriched["github_context"],
            "github_urls": resolved.urls,
            "source_url": source_url,
        }
    return enriched


def _enrich_event(con: sqlite3.Connection, event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    stored = _source_from_stored_context(context)
    joined = _source_for_message_id(con, str(context.get("message_id") or "") or None)
    fallback = _fallback_source_from_comment(str(event.get("comment") or ""))

    github_urls = _first_value(stored.get("github_urls"), joined.get("github_urls"), fallback.get("github_urls")) or []
    source_url = _first_value(stored.get("source_url"), joined.get("source_url"), fallback.get("source_url"))
    github_context = _first_value(stored.get("github_context"), joined.get("github_context"), fallback.get("github_context")) or {}
    trigger_actor = _first_value(stored.get("trigger_actor"), joined.get("trigger_actor"), event.get("actor") if event.get("actor") != "github" else None)
    trigger_actor_avatar_url = _first_value(stored.get("trigger_actor_avatar_url"), joined.get("trigger_actor_avatar_url"))

    enriched = {
        **event,
        "trigger_actor": trigger_actor,
        "trigger_actor_avatar_url": trigger_actor_avatar_url,
        "github_urls": github_urls,
        "source_url": source_url,
        "github_context": github_context,
        "source_job_id": _first_value(stored.get("source_job_id"), joined.get("source_job_id")),
        "source_table": _first_value(stored.get("source_table"), joined.get("source_table"), fallback.get("source_table")),
    }
    if trigger_actor and enriched.get("actor") == "github":
        enriched["actor"] = trigger_actor
    return enriched


def list_rules(db_path: str | Path, scope: str = "", min_confidence: float | None = None) -> list[dict[str, Any]]:
    clauses = []
    args: list[Any] = []
    if scope:
        clauses.append("(scope=? OR scope LIKE ?)")
        args.extend([scope, f"{scope}:%"])
    if min_confidence is not None:
        clauses.append("confidence>=?")
        args.append(min_confidence)
    sql = "SELECT * FROM feedback_rules"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY last_seen DESC, created_at DESC, scope ASC, type ASC, rule ASC"
    with _connect(db_path) as con:
        return [_rule_dict(con, row) for row in con.execute(sql, args)]


def rule_scopes_for_repo(repo: str) -> list[str]:
    normalized = (repo or "").strip().lower()
    if "/" not in normalized:
        return ["global"]
    owner, name = [part.strip() for part in normalized.split("/", 1)]
    if not owner or not name:
        return ["global"]
    return ["global", f"org:{owner}", f"repo:{owner}/{name}"]


def list_applicable_rules(db_path: str | Path, repo: str, min_confidence: float | None = None) -> list[dict[str, Any]]:
    scopes = rule_scopes_for_repo(repo)
    clauses = []
    args: list[Any] = []
    scope_clauses = []
    for scope in scopes:
        scope_clauses.append("(scope=? OR scope LIKE ?)")
        args.extend([scope, f"{scope}:%"])
    if scope_clauses:
        clauses.append("(" + " OR ".join(scope_clauses) + ")")
    if min_confidence is not None:
        clauses.append("confidence>=?")
        args.append(min_confidence)
    sql = "SELECT * FROM feedback_rules"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY last_seen DESC, created_at DESC, scope ASC, type ASC, rule ASC"
    with _connect(db_path) as con:
        return [_rule_dict(con, row) for row in con.execute(sql, args)]


def _rule_dict(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    source_events = json.loads(row["source_events_json"] or "[]")
    return {
        "id": row["id"],
        "scope": row["scope"],
        "type": row["type"],
        "confidence": row["confidence"],
        "rule": row["rule"],
        "created_at": row["created_at"],
        "last_seen": row["last_seen"],
        "source_events": source_events,
        "source_event_details": _source_event_details(con, source_events),
        "observations": row["observations"],
    }


def format_rules_context(repo: str, min_confidence: float, rules: list[dict[str, Any]]) -> str:
    scope = f"repo:{(repo or 'unknown/repo').strip().lower()}"
    if not rules:
        return f"No curated feedback rules matched {scope} at confidence >= {min_confidence}."
    lines = []
    for rule in rules:
        lines.append(
            f"- [{rule['scope']}] {rule['type']} "
            f"(confidence {rule['confidence']:.2f}, observations {rule['observations']}): "
            f"{rule['rule']}"
        )
    return "\n".join(lines)


def _source_event_details(con: sqlite3.Connection, source_events: list[str]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_id in source_events:
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        row = con.execute("SELECT * FROM feedback_events WHERE id=?", (event_id,)).fetchone()
        if row:
            details.append(_enrich_event(con, _event_dict(row)))
    return details
