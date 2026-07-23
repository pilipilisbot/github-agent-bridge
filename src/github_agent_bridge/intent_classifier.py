from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .feedback import _extract_json_object, _openclaw_text_from_json, compact, load_prompt_rule, session_id_for_event
from .models import GitHubContext, Notification
from .parser import github_event_flags
from .policy import DEFAULT_BOT_LOGINS, IntentClassifier, Policy

ALLOWED_ACTIONS = {
    "reply_comment",
    "open_issue",
    "submit_review",
    "workflow_run_failed",
    "sync_after_merge",
    "archive_notification",
}
ALLOWED_WORK_INTENTS = {"review_only", "work_allowed"}
ALLOWED_WRITE_PERMISSIONS = {"none", "state_change_allowed"}
ALLOWED_COMPLEXITIES = {"mechanical", "substantive"}
INTENT_CLASSIFIER_PROMPT = load_prompt_rule("intent_classifier.md")
COMMENT_TARGET_KINDS = {"issue_comment", "review_comment", "commit_comment", "review"}


@dataclass(frozen=True)
class ParserResult:
    action: str
    work_intent: str


@dataclass(frozen=True)
class IntentClassification:
    action: str
    work_intent: str
    confidence: float
    reason: str
    applied: bool
    addressed_to_agent: bool = False
    write_permission: str = "none"
    scope: str = ""
    main_request: str = ""
    subordinate_reason: str = ""
    complexity: str = "substantive"

    def to_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "action": self.action,
            "work_intent": self.work_intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "applied": self.applied,
            "addressed_to_agent": self.addressed_to_agent,
            "write_permission": self.write_permission,
            "complexity": self.complexity,
        }
        if self.scope:
            metadata["scope"] = self.scope
        if self.main_request:
            metadata["main_request"] = self.main_request
        if self.subordinate_reason:
            metadata["subordinate_reason"] = self.subordinate_reason
        return metadata


def should_classify_with_llm(
    n: Notification,
    ctx: GitHubContext,
    parser_result: ParserResult,
    policy: Policy,
) -> bool:
    cfg = policy.intent_classifier
    if not cfg.enabled:
        return False
    if ctx.target_kind not in COMMENT_TARGET_KINDS:
        return False
    if not policy.trusted_source(n, ctx):
        return False
    flags = github_event_flags(n.subject, n.body, policy.bot_logins)
    if cfg.only_when_parser_defaulted and not (
        parser_result.action == "reply_comment" and parser_result.work_intent == "review_only"
        or flags["bot_mentioned"]
    ):
        return False
    return True


def agent_identity(policy: Policy | None, agent: str | None = None) -> dict[str, object]:
    logins = sorted((policy.bot_logins if policy else set(DEFAULT_BOT_LOGINS)) or set())
    return {
        "github_logins": logins,
        "aliases": logins,
        "openclaw_agent": agent or "",
    }


def build_intent_prompt(
    n: Notification,
    ctx: GitHubContext,
    parser_result: ParserResult,
    prompt_template: str | None = None,
    *,
    policy: Policy | None = None,
    agent: str | None = None,
) -> str:
    event = {
        "subject": n.subject,
        "body": compact(n.body, 2400),
        "from_addr": n.from_addr,
        "message_id": n.message_id,
        "agent_identity": agent_identity(policy, agent),
        "github_context": json.loads(ctx.to_json()),
        "parser_result": {
            "action": parser_result.action,
            "work_intent": parser_result.work_intent,
        },
    }
    template = prompt_template or INTENT_CLASSIFIER_PROMPT
    return template.replace("{event_json}", json.dumps(event, ensure_ascii=False, sort_keys=True))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def normalize_result(result: dict[str, Any], min_confidence: float) -> IntentClassification:
    action = str(result.get("action") or "").strip().lower()
    work_intent = str(result.get("work_intent") or result.get("intent") or "").strip().lower()
    addressed_to_agent = _as_bool(result.get("addressed_to_agent"))
    write_permission = str(result.get("write_permission") or "none").strip().lower()
    if write_permission not in ALLOWED_WRITE_PERMISSIONS:
        write_permission = "none"
    if not addressed_to_agent:
        action = "archive_notification"
        work_intent = "review_only"
        write_permission = "none"
    elif work_intent == "work_allowed" and write_permission != "state_change_allowed":
        work_intent = "review_only"
    confidence = float(result.get("confidence") or 0)
    confidence = min(1.0, max(0.0, confidence))
    scope = compact(str(result.get("scope") or ""), 500)
    main_request = compact(str(result.get("main_request") or ""), 500)
    subordinate_reason = compact(str(result.get("subordinate_reason") or ""), 500)
    complexity = str(result.get("complexity") or "substantive").strip().lower()
    if complexity not in ALLOWED_COMPLEXITIES:
        complexity = "substantive"
    reason = compact(str(result.get("reason") or ""), 500)
    applied = action in ALLOWED_ACTIONS and work_intent in ALLOWED_WORK_INTENTS and confidence >= min_confidence
    return IntentClassification(
        action=action,
        work_intent=work_intent,
        confidence=confidence,
        reason=reason,
        applied=applied,
        addressed_to_agent=addressed_to_agent,
        write_permission=write_permission,
        scope=scope,
        main_request=main_request,
        subordinate_reason=subordinate_reason,
        complexity=complexity,
    )


def intent_session_event_id(n: Notification, ctx: GitHubContext) -> str:
    event = {
        "message_id": n.message_id,
        "work_key": ctx.work_key,
        "repo": ctx.repo,
        "issue_number": ctx.issue_number,
        "comment_id": ctx.comment_id,
        "review_id": ctx.review_id,
        "review_comment_id": ctx.review_comment_id,
        "commit_comment_id": ctx.commit_comment_id,
        "commit_sha": ctx.commit_sha,
        "target_kind": ctx.target_kind,
        "workflow_run_id": ctx.workflow_run_id,
    }
    return json.dumps(event, ensure_ascii=False, sort_keys=True)


def intent_session_id(base_session_id: str, n: Notification, ctx: GitHubContext, agent: str | None = None) -> str:
    return session_id_for_event(base_session_id, agent, intent_session_event_id(n, ctx))


def classify_notification_with_llm(
    n: Notification,
    ctx: GitHubContext,
    parser_result: ParserResult,
    cfg: IntentClassifier,
    *,
    policy: Policy | None = None,
    agent: str | None = None,
    prompt_template: str | None = None,
) -> IntentClassification:
    cmd = [
        cfg.openclaw_bin,
        "agent",
        "--json",
        "--session-id",
        intent_session_id(cfg.session_id, n, ctx, agent),
        "--timeout",
        str(cfg.timeout),
        "--thinking",
        cfg.thinking,
        "--message",
        build_intent_prompt(n, ctx, parser_result, prompt_template, policy=policy, agent=agent),
    ]
    if agent:
        cmd.extend(["--agent", agent])
    if cfg.model:
        cmd.extend(["--model", cfg.model])
    env = os.environ.copy()
    openclaw_dir = os.path.dirname(cfg.openclaw_bin)
    if openclaw_dir:
        env["PATH"] = openclaw_dir + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=cfg.timeout + 30,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"openclaw exited {proc.returncode}")
    text = _openclaw_text_from_json(proc.stdout)
    return normalize_result(_extract_json_object(text), cfg.min_confidence)
