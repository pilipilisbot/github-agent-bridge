from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from importlib import resources
from enum import StrEnum
from typing import Callable

from . import feedback
from .models import GitHubContext, Job
from .policy import DEFAULT_REPO_ROLE, Policy, Route, complexity_from_metadata
from .process_inspection import process_stat
from .session_correlation import (
    normalize_session_id,
    session_id_for_job,
    session_id_for_job_attempt,
    session_key_for_rescue,
    session_key_for_work,
)

PROMPT_RULES_PACKAGE = "github_agent_bridge.prompt_rules"


def load_prompt_rule(name: str) -> str:
    """Read a packaged Markdown prompt rule.

    The rules are package resources so they remain available after wheel/sdist
    installation. Keep them as Markdown files instead of inline strings so
    agents and humans can review/edit them directly.
    """
    return resources.files(PROMPT_RULES_PACKAGE).joinpath(name).read_text(encoding="utf-8").strip() + "\n"


def load_role_prompt(role: str) -> str:
    """Read a packaged Markdown repository-role prompt."""
    return load_prompt_rule(f"roles/{role}.md")


def load_prompt_override(path) -> str:
    """Read a policy-configured prompt override file."""
    return path.read_text(encoding="utf-8").strip() + "\n"


def prompt_rule(name: str, default: str, policy: Policy | None) -> str:
    """Read a packaged prompt rule or a policy-configured override."""
    override = policy.prompt_overrides.rule_path(name) if policy else None
    return load_prompt_override(override) if override else default


BASE_PROMPT = load_prompt_rule("base.md")
WORKTREE_RULES = load_prompt_rule("worktree.md")
PR_METADATA_RULES = load_prompt_rule("pr_metadata.md")
HUMAN_REVIEWER_RULES = load_prompt_rule("human_reviewer.md")
REVIEW_ONLY_RULES = load_prompt_rule("review_only.md")
SYNC_AFTER_MERGE_RULES = load_prompt_rule("sync_after_merge.md")
PR_REVIEW_RULES = load_prompt_rule("pr_review.md")
COMMENT_VALUE_RULES = load_prompt_rule("comment_value.md")
PROMPT_INJECTION_RULES = load_prompt_rule("prompt_injection.md")
REPO_INSTRUCTIONS_RULES = load_prompt_rule("repo_instructions.md")
FEEDBACK_LEARNING_RULES = load_prompt_rule("feedback_learning.md")


def coauthor_identity_for_job(job: Job) -> str:
    """Render the GitHub identity agents should use in commit trailers."""
    login = (job.trigger_actor or "").strip().lstrip("@")
    if not login:
        return "No triggering GitHub actor is known; do not add a user co-author trailer unless the issue/PR discussion provides an explicit commit email."
    if login.endswith("[bot]"):
        return f"Triggering actor @{login} is a bot; do not add it as a human co-author."

    raw_user_id = job.metadata.get("trigger_actor_id")
    user_id = raw_user_id if isinstance(raw_user_id, int) and raw_user_id > 0 else None
    email = f"{user_id}+{login}@users.noreply.github.com" if user_id else f"{login}@users.noreply.github.com"
    return f"Use this commit trailer when committing requested work: `Co-authored-by: {login} <{email}>`"


def action_mode_for_job(job: Job) -> str:
    return "fix_allowed" if job.work_intent == "work_allowed" else "review_only"


class RunMode(StrEnum):
    SHADOW = "shadow"  # no external side effects: no GitHub reaction, no OpenClaw dispatch
    DRY_RUN = "dry-run"  # no external side effects, but render intended commands/actions
    LIVE = "live"  # perform GitHub reaction and OpenClaw dispatch


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    reaction_ok: bool | None = None
    command: list[str] | None = None
    cancelled: bool = False

    @property
    def detail(self) -> str:
        return (self.stderr or self.stdout or "").replace("\n", " ")[:1000]


class GitHubClient:
    def __init__(self, gh_bin: str = "gh", mode: RunMode = RunMode.LIVE):
        self.mode = mode
        self.gh_bin = gh_bin

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [self.gh_bin, *args]
        try:
            return subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(command, 127, "", str(exc))

    def current_login(self) -> str | None:
        result = self._run(["api", "user", "--jq", ".login"])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def pull_request_review(self, ctx: GitHubContext) -> dict | None:
        if not ctx.repo or not ctx.review_id:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/pulls/{ctx.issue_number}/reviews/{ctx.review_id}"])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

    def pull_request_review_comments(self, ctx: GitHubContext) -> list[dict]:
        if not ctx.repo or not ctx.issue_number or not ctx.review_id:
            return []
        result = self._run(["api", f"repos/{ctx.repo}/pulls/{ctx.issue_number}/reviews/{ctx.review_id}/comments"])
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        return [item for item in data if isinstance(item, dict)]

    def pull_request_review_comment(self, ctx: GitHubContext) -> dict | None:
        if not ctx.repo or not ctx.review_comment_id:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/pulls/comments/{ctx.review_comment_id}"])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

    def is_non_actionable_review(self, ctx: GitHubContext) -> bool:
        review = self.pull_request_review(ctx)
        if not review:
            return False
        state = (review.get("state") or "").upper()
        if state == "APPROVED":
            return True
        body = (review.get("body") or "").lower()
        non_actionable_markers = (
            "generated no new comments",
            "wasn't able to review any files",
            "was not able to review any files",
            "no actionable comments",
            "no actionable findings",
            "no action required",
            "nothing to change",
        )
        return any(marker in body for marker in non_actionable_markers)

    def is_non_actionable_copilot_review(self, ctx: GitHubContext) -> bool:
        return self.is_non_actionable_review(ctx)

    def issue_comment_body(self, ctx: GitHubContext) -> str | None:
        if not ctx.repo or not ctx.comment_id:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/issues/comments/{ctx.comment_id}", "--jq", ".body"])
        if result.returncode != 0:
            return None
        return result.stdout or ""

    def commit_comment_body(self, ctx: GitHubContext) -> str | None:
        if not ctx.repo or not ctx.commit_comment_id:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/comments/{ctx.commit_comment_id}", "--jq", ".body"])
        if result.returncode != 0:
            return None
        return result.stdout or ""

    def issue_comment(self, ctx: GitHubContext) -> dict | None:
        if not ctx.repo or not ctx.comment_id:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/issues/comments/{ctx.comment_id}"])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

    def issue_created_at(self, ctx: GitHubContext) -> str | None:
        if not ctx.repo or not ctx.issue_number:
            return None
        result = self._run(["api", f"repos/{ctx.repo}/issues/{ctx.issue_number}", "--jq", ".created_at"])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def current_user_thread_comment_after(self, ctx: GitHubContext, after: str | None = None) -> str | None:
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return None
        login = self.current_login()
        if not login:
            return None
        result = self._run([
            "api",
            "--paginate",
            f"repos/{repo}/issues/{issue}/comments",
            "--jq",
            ".[] | @json",
        ])
        if result.returncode != 0:
            return None
        newest_url = None
        newest_created_at = ""
        for line in result.stdout.splitlines():
            try:
                comment = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = comment.get("user") if isinstance(comment, dict) else None
            if not isinstance(user, dict) or user.get("login") != login:
                continue
            created_at = comment.get("created_at") or ""
            if after and created_at <= after:
                continue
            if created_at >= newest_created_at:
                newest_created_at = created_at
                newest_url = comment.get("html_url") or f"{repo}#{issue}"
        return newest_url

    def current_user_commented_after(self, ctx: GitHubContext) -> str | None:
        repo, issue, comment_id = ctx.repo, ctx.issue_number, ctx.comment_id
        if not repo or not issue or not comment_id:
            return None
        login = self.current_login()
        trigger = self.issue_comment(ctx)
        trigger_created_at = trigger.get("created_at") if isinstance(trigger, dict) else None
        if not login or not trigger_created_at:
            return None
        result = self._run([
            "api",
            "--paginate",
            f"repos/{repo}/issues/{issue}/comments",
            "--jq",
            ".[] | @json",
        ])
        if result.returncode != 0:
            return None
        newest_url = None
        newest_created_at = ""
        for line in result.stdout.splitlines():
            try:
                comment = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = comment.get("user") if isinstance(comment, dict) else None
            if not isinstance(user, dict) or user.get("login") != login:
                continue
            created_at = comment.get("created_at") or ""
            if created_at > trigger_created_at and created_at >= newest_created_at:
                newest_created_at = created_at
                newest_url = comment.get("html_url") or f"{repo}#{issue}"
        return newest_url

    def current_user_review_comment_after(self, ctx: GitHubContext, after: str | None = None) -> str | None:
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return None
        login = self.current_login()
        if not login:
            return None
        result = self._run([
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{issue}/comments",
            "--jq",
            ".[] | @json",
        ])
        if result.returncode != 0:
            return None
        newest_url = None
        newest_created_at = ""
        for line in result.stdout.splitlines():
            try:
                comment = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = comment.get("user") if isinstance(comment, dict) else None
            if not isinstance(user, dict) or user.get("login") != login:
                continue
            created_at = comment.get("created_at") or ""
            if after and created_at <= after:
                continue
            if created_at >= newest_created_at:
                newest_created_at = created_at
                newest_url = comment.get("html_url") or f"{repo}#{issue}"
        return newest_url

    def current_user_review_after(self, ctx: GitHubContext, after: str | None = None) -> str | None:
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return None
        login = self.current_login()
        if not login:
            return None
        result = self._run([
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{issue}/reviews",
            "--jq",
            ".[] | @json",
        ])
        if result.returncode != 0:
            return None
        newest_url = None
        newest_created_at = ""
        for line in result.stdout.splitlines():
            try:
                review = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = review.get("user") if isinstance(review, dict) else None
            if not isinstance(user, dict) or user.get("login") != login:
                continue
            submitted_at = review.get("submitted_at") or ""
            if after and submitted_at <= after:
                continue
            if submitted_at >= newest_created_at:
                newest_created_at = submitted_at
                newest_url = review.get("html_url") or f"https://github.com/{repo}/pull/{issue}#pullrequestreview-{review.get('id')}"
        return newest_url

    def visible_followup_after_trigger(self, ctx: GitHubContext) -> str | None:
        if ctx.comment_id:
            trigger = self.issue_comment(ctx)
            trigger_created_at = trigger.get("created_at") if isinstance(trigger, dict) else None
            return self.current_user_thread_comment_after(ctx, trigger_created_at) or self.current_user_review_comment_after(ctx, trigger_created_at) or self.current_user_review_after(ctx, trigger_created_at)
        if ctx.review_comment_id:
            trigger = self.pull_request_review_comment(ctx)
            trigger_created_at = trigger.get("created_at") if isinstance(trigger, dict) else None
            return self.current_user_review_comment_after(ctx, trigger_created_at) or self.current_user_thread_comment_after(ctx, trigger_created_at) or self.current_user_review_after(ctx, trigger_created_at)
        if ctx.review_id:
            review = self.pull_request_review(ctx)
            trigger_created_at = review.get("submitted_at") if isinstance(review, dict) else None
            return self.current_user_review_comment_after(ctx, trigger_created_at) or self.current_user_thread_comment_after(ctx, trigger_created_at) or self.current_user_review_after(ctx, trigger_created_at)
        after = self.issue_created_at(ctx)
        return self.current_user_thread_comment_after(ctx, after) or self.current_user_review_comment_after(ctx, after) or self.current_user_review_after(ctx, after)

    def issue_comment_addresses_current_user(self, ctx: GitHubContext) -> bool:
        body = self.issue_comment_body(ctx)
        login = self.current_login()
        if body is None or not login:
            return False
        mentions = [m.lower() for m in re.findall(r"@([A-Za-z0-9-]+)", body)]
        if not mentions:
            return False
        # Treat the comment as addressed to the bot only when the bot is the
        # first mentioned user. A later mention can be merely referential, e.g.
        # "@Marc what do you think about @pilipilisbot's changes?"
        return mentions[0] == login.lower()

    def issue_comment_mentions_current_user(self, ctx: GitHubContext) -> bool:
        return self.issue_comment_addresses_current_user(ctx)

    def react(self, ctx: GitHubContext, content: str) -> bool:
        if self.mode != RunMode.LIVE:
            return True
        repo, issue = ctx.repo, ctx.issue_number
        if not repo:
            return False
        if ctx.comment_id:
            return self._run(["api", "-X", "POST", f"repos/{repo}/issues/comments/{ctx.comment_id}/reactions", "-f", f"content={content}", "-H", "Accept: application/vnd.github+json"]).returncode == 0
        if ctx.review_comment_id:
            return self._run(["api", "-X", "POST", f"repos/{repo}/pulls/comments/{ctx.review_comment_id}/reactions", "-f", f"content={content}", "-H", "Accept: application/vnd.github+json"]).returncode == 0
        if ctx.commit_comment_id:
            return self._run(["api", "-X", "POST", f"repos/{repo}/comments/{ctx.commit_comment_id}/reactions", "-f", f"content={content}", "-H", "Accept: application/vnd.github+json"]).returncode == 0
        if ctx.review_id:
            comments = self.pull_request_review_comments(ctx)
            if comments:
                ok = True
                for comment in comments:
                    comment_id = comment.get("id")
                    if comment_id:
                        ok = self._run(["api", "-X", "POST", f"repos/{repo}/pulls/comments/{comment_id}/reactions", "-f", f"content={content}", "-H", "Accept: application/vnd.github+json"]).returncode == 0 and ok
                return ok
        if not issue:
            return False
        return self._run(["api", "-X", "POST", f"repos/{repo}/issues/{issue}/reactions", "-f", f"content={content}", "-H", "Accept: application/vnd.github+json"]).returncode == 0

    def is_assigned_to_current_user(self, ctx: GitHubContext) -> bool:
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return False
        login = self.current_login()
        if not login:
            return False
        result = self._run(["api", f"repos/{repo}/issues/{issue}"])
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return False
        return login in {a.get("login") for a in data.get("assignees", []) if isinstance(a, dict)}

    def is_pull_request_authored_by_current_user(self, ctx: GitHubContext) -> bool:
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return False
        login = self.current_login()
        if not login:
            return False
        result = self._run(["api", f"repos/{repo}/pulls/{issue}"])
        if result.returncode != 0:
            return False
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return False
        author = data.get("user") if isinstance(data, dict) else None
        return isinstance(author, dict) and author.get("login") == login

    def react_eyes(self, ctx: GitHubContext) -> bool:
        return self.react(ctx, "eyes")

    def react_ack_no_comment(self, ctx: GitHubContext) -> bool:
        return self.react(ctx, "+1")

    def comment_on_thread(self, ctx: GitHubContext, body: str) -> str | None:
        if self.mode != RunMode.LIVE:
            return ctx.short_url
        repo, issue = ctx.repo, ctx.issue_number
        if not repo or not issue:
            return None
        result = self._run([
            "api",
            "-X",
            "POST",
            f"repos/{repo}/issues/{issue}/comments",
            "-f",
            f"body={body}",
            "--jq",
            ".html_url",
        ])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None


class OpenClawDispatcher:
    def __init__(
        self,
        openclaw_bin: str = "openclaw",
        node_bin: str | None = None,
        default_channel: str = "telegram",
        default_to: str = "",
        timeout_seconds: int = 3600,
        mode: RunMode = RunMode.LIVE,
        review_timeout_seconds: int = 900,
        work_timeout_seconds: int = 3600,
        cli_grace_seconds: int = 60,
        feedback_db_path: str | None = None,
    ):
        self.openclaw_bin = openclaw_bin
        self.node_bin = node_bin
        self.default_channel = default_channel
        self.default_to = default_to
        self.timeout_seconds = timeout_seconds
        self.review_timeout_seconds = review_timeout_seconds
        self.work_timeout_seconds = work_timeout_seconds
        self.cli_grace_seconds = cli_grace_seconds
        self.feedback_db_path = feedback_db_path
        self.mode = mode
        self._shutdown_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_processes: set[subprocess.Popen] = set()

    def shutdown(self, kill_grace_seconds: float = 5.0) -> None:
        """Cancel active CLI process groups and prevent new dispatches."""
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        with self._process_lock:
            processes = list(self._active_processes)
        for proc in processes:
            self._signal_process_group(proc, signal.SIGTERM)
        if processes and kill_grace_seconds >= 0:
            threading.Thread(
                target=self._kill_processes_after_grace,
                args=(processes, kill_grace_seconds),
                daemon=True,
            ).start()

    @staticmethod
    def _signal_process_group(proc: subprocess.Popen, sig: signal.Signals) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (OSError, ProcessLookupError):
            try:
                proc.send_signal(sig)
            except (OSError, ProcessLookupError):
                pass

    def _kill_processes_after_grace(self, processes: list[subprocess.Popen], grace_seconds: float) -> None:
        if grace_seconds:
            threading.Event().wait(grace_seconds)
        for proc in processes:
            self._signal_process_group(proc, signal.SIGKILL)

    def timeout_for(self, job: Job) -> int:
        if job.work_intent == "review_only":
            return self.review_timeout_seconds
        if job.work_intent == "work_allowed":
            return self.work_timeout_seconds
        return self.timeout_seconds

    def build_prompt(self, job: Job, policy: Policy | None = None) -> str:
        repo = job.repo or "unknown/repo"; thread = job.thread or 0
        role = policy.role_for(job.repo) if policy else DEFAULT_REPO_ROLE
        role_override = policy.prompt_overrides.role_path(role) if policy else None
        intent_override = policy.prompt_overrides.intent_path(job.work_intent) if policy else None
        base_template = load_prompt_override(policy.prompt_overrides.base) if policy and policy.prompt_overrides.base else BASE_PROMPT
        role_prompt = load_prompt_override(role_override) if role_override else load_role_prompt(role)
        intent_rules = ""
        if job.work_intent == "review_only":
            intent_rules = load_prompt_override(intent_override) if intent_override else REVIEW_ONLY_RULES
        action_rules = ""
        if job.action == "sync_after_merge":
            action_rules = prompt_rule("sync_after_merge", SYNC_AFTER_MERGE_RULES, policy)
        elif job.action == "submit_review":
            action_rules = prompt_rule("pr_review", PR_REVIEW_RULES, policy)
        base_prompt = base_template.format(
            repo=repo,
            thread=thread,
            action=job.action,
            work_intent=job.work_intent,
            action_mode=action_mode_for_job(job),
            url=job.context.short_url,
            message_id=job.message_id,
            subject=job.subject,
            trigger_actor=job.trigger_actor or "",
            coauthor_identity=coauthor_identity_for_job(job),
        )
        feedback_min_confidence = policy.feedback_learning.min_confidence if policy else 0.5
        feedback_rules_template = prompt_rule("feedback_learning", FEEDBACK_LEARNING_RULES, policy)
        feedback_rules = feedback_rules_template.format(
            repo=repo,
            min_confidence=feedback_min_confidence,
            rules=self.feedback_rules_context(repo, feedback_min_confidence),
        )
        prompt_injection_rules = prompt_rule("prompt_injection", PROMPT_INJECTION_RULES, policy)
        repo_instructions_rules = prompt_rule("repo_instructions", REPO_INSTRUCTIONS_RULES, policy)
        comment_value_rules = prompt_rule("comment_value", COMMENT_VALUE_RULES, policy)
        worktree_rules = prompt_rule("worktree", WORKTREE_RULES, policy)
        pr_metadata_rules = prompt_rule("pr_metadata", PR_METADATA_RULES, policy)
        human_reviewer_rules = prompt_rule("human_reviewer", HUMAN_REVIEWER_RULES, policy)
        return f"{base_prompt}{role_prompt}{intent_rules}{action_rules}{prompt_injection_rules}{repo_instructions_rules}{comment_value_rules}{worktree_rules}{pr_metadata_rules}{human_reviewer_rules}{feedback_rules}"

    def feedback_rules_context(self, repo: str, min_confidence: float) -> str:
        if not self.feedback_db_path:
            return "No bridge database was provided, so no curated feedback rules were loaded."
        try:
            rules = feedback.list_applicable_rules(self.feedback_db_path, repo=repo, min_confidence=min_confidence)
        except Exception as exc:
            return f"Could not load curated feedback rules from the bridge database: {exc}"
        return feedback.format_rules_context(repo, min_confidence, rules)

    def route_for(self, job: Job, policy: Policy) -> tuple[str | None, str, str]:
        route: Route = policy.route_for(job.repo)
        agent = route.agent
        channel = route.channel or self.default_channel
        to = route.to or self.default_to
        return agent, channel, to

    def dispatch(
        self,
        job: Job,
        policy: Policy,
        reaction_ok: bool | None = None,
        activity_callback: Callable[[str, str, str | None], None] | None = None,
        process_callback: Callable[[dict[str, int]], bool | None] | None = None,
    ) -> DispatchResult:
        agent, channel, to = self.route_for(job, policy)
        cmd = [self.openclaw_bin, "agent"]
        if agent:
            cmd += ["--agent", agent]
        model_route = policy.model_route_for(
            job.repo,
            job.action,
            job.work_intent,
            complexity_from_metadata(job.metadata),
        )
        if model_route.model:
            cmd += ["--model", model_route.model]
        if model_route.thinking:
            cmd += ["--thinking", model_route.thinking]
        agent_timeout = self.timeout_for(job)
        fresh_session = bool(job.metadata.get("fresh_session_on_retry")) and job.attempts > 1
        session_key = (
            session_key_for_rescue(job.work_key, job.id, job.attempts)
            if fresh_session
            else session_key_for_work(job.work_key)
        )
        if fresh_session or job.work_intent == "work_allowed":
            default_session_id = session_id_for_job_attempt(job.id, job.attempts)
            session_id = normalize_session_id(default_session_id)
        else:
            default_session_id = session_id_for_job(job.id)
            session_id = normalize_session_id(str(job.metadata.get("openclaw_session_id") or default_session_id))
        cmd += [
            "--session-id",
            session_id,
            "--session-key",
            session_key,
            "--verbose",
            "on",
            "--channel",
            channel,
            "--to",
            to,
            "--deliver",
            "--timeout",
            str(agent_timeout),
            "--message",
            self.build_prompt(job, policy),
        ]
        env = os.environ.copy()
        env["GITHUB_AGENT_BRIDGE_ACTION_MODE"] = action_mode_for_job(job)
        env["GITHUB_AGENT_BRIDGE_WORK_INTENT"] = job.work_intent
        env["GITHUB_AGENT_BRIDGE_ALLOW_REPOSITORY_WRITE"] = "1" if job.work_intent == "work_allowed" else "0"
        env["GITHUB_AGENT_BRIDGE_ALLOW_PUSH"] = "1" if job.work_intent == "work_allowed" else "0"
        if self.node_bin:
            env["PATH"] = os.path.dirname(self.node_bin) + os.pathsep + env.get("PATH", "")
        if self.mode != RunMode.LIVE:
            return DispatchResult(True, 0, "side effects skipped", "", False, reaction_ok, cmd)
        with self._process_lock:
            if self._shutdown_event.is_set():
                return DispatchResult(False, 130, "", "executor shutdown requested before dispatch", False, reaction_ok, cmd, True)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
            self._active_processes.add(proc)
        if process_callback:
            stat = process_stat(proc.pid)
            identity = {
                "pid": proc.pid,
                "ppid": int(stat["ppid"]) if stat else os.getpid(),
                "pgid": int(stat["pgid"]) if stat else proc.pid,
                "sid": int(stat["sid"]) if stat else proc.pid,
                "start_time_ticks": int(stat["start_time_ticks"]) if stat else 0,
            }
            try:
                registered = bool(stat) and process_callback(identity) is not False
            except Exception:
                registered = False
            if not registered:
                self._signal_process_group(proc, signal.SIGKILL)
                proc.wait()
                with self._process_lock:
                    self._active_processes.discard(proc)
                return DispatchResult(
                    False,
                    125,
                    "",
                    "failed to persist runtime process ownership; dispatch terminated",
                    False,
                    reaction_ok,
                    cmd,
                )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stream(stream, chunks: list[str], event_type: str) -> None:
            if stream is None:
                return
            while True:
                data = os.read(stream.fileno(), 4096)
                if not data:
                    break
                chunk = data.decode("utf-8", errors="replace")
                chunks.append(chunk)
                if activity_callback:
                    activity_callback(event_type, "OpenClaw CLI output" if event_type == "openclaw_stdout" else "OpenClaw CLI error output", chunk.rstrip("\n"))

        stdout_thread = threading.Thread(target=read_stream, args=(proc.stdout, stdout_chunks, "openclaw_stdout"), daemon=True)
        stderr_thread = threading.Thread(target=read_stream, args=(proc.stderr, stderr_chunks, "openclaw_stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            try:
                # Let OpenClaw's own --timeout own the agent run deadline. The bridge only
                # keeps a small grace window so it can capture the CLI result cleanly.
                proc.wait(timeout=agent_timeout + self.cli_grace_seconds)
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                out, err = "".join(stdout_chunks), "".join(stderr_chunks)
                cancelled = self._shutdown_event.is_set() and proc.returncode != 0
                return DispatchResult(proc.returncode == 0, proc.returncode, (out or "")[:2000], (err or "")[:4000], False, reaction_ok, cmd, cancelled)
            except subprocess.TimeoutExpired:
                self._signal_process_group(proc, signal.SIGKILL)
                proc.wait()
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                out, err = "".join(stdout_chunks), "".join(stderr_chunks)
                return DispatchResult(False, 124, (out or "")[:2000], (err or "")[:4000], True, reaction_ok, cmd)
        finally:
            with self._process_lock:
                self._active_processes.discard(proc)
