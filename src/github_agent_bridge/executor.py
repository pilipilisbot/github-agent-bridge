from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

from .dispatch import GitHubClient, OpenClawDispatcher
from .policy import Policy
from .queue import JobQueue
from .session_events import redact_event_detail


NO_FOLLOWUP_OK_MARKERS = (
    "no github follow-up comment was appropriate",
    "no github follow-up was appropriate",
    "no new github follow-up was appropriate",
)
NO_FOLLOWUP_DUPLICATE_MARKERS = (
    "duplicate",
    "already contains",
    "already has",
    "already reported",
    "no new information",
    "no new repository state",
    "no new state",
    "prior cleanup note",
    "repeat",
)
TRANSIENT_DISPATCH_ERROR_MARKERS = (
    "cli transcript compaction failed",
    "summarization failed: connection error",
    "codex app-server client closed before turn completed",
)


@dataclass(frozen=True)
class ExecutorConfig:
    workers: int = 4
    idle_sleep_seconds: float = 1.0
    run_once: bool = False
    missing_followup_retries: int = 1
    transient_dispatch_retries: int = 2


class ExecutorPool:
    def __init__(self, queue: JobQueue, policy: Policy, dispatcher: OpenClawDispatcher, github: GitHubClient | None = None, config: ExecutorConfig | None = None):
        self.queue = queue
        self.policy = policy
        self.dispatcher = dispatcher
        self.github = github or GitHubClient()
        self.config = config or ExecutorConfig()
        self.stop_event = threading.Event()

    def work_one(self, worker_id: str | None = None) -> bool:
        worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        job = self.queue.claim_next(worker_id)
        if not job:
            return False
        dispatched = False
        try:
            assigned_to_bot = self.github.is_assigned_to_current_user(job.context)
            authored_by_bot = self.github.is_pull_request_authored_by_current_user(job.context)
            if job.action == "reply_comment" and job.context.review_id and self.github.is_non_actionable_review(job.context):
                reaction_ok = self.react_eyes_for_job_contexts(job)
                ack_ok = self.github.react_ack_no_comment(job.context)
                summary = "non-actionable review; skipped dispatch"
                detail = f"eyes={reaction_ok} ack={ack_ok}"
                self.queue.finish(job.id, "done", summary, detail)
                return True
            if job.action == "reply_comment" and job.context.comment_id and not assigned_to_bot and not self.github.issue_comment_addresses_current_user(job.context):
                reaction_ok = self.react_eyes_for_job_contexts(job)
                ack_ok = self.github.react_ack_no_comment(job.context)
                summary = "comment not addressed to bot and bot not assigned; skipped dispatch"
                detail = f"eyes={reaction_ok} ack={ack_ok}"
                self.queue.finish(job.id, "done", summary, detail)
                return True
            if job.action == "reply_comment" and job.work_intent == "review_only" and (assigned_to_bot or authored_by_bot):
                reason = "PR/issue assigned to authenticated bot" if assigned_to_bot else "PR authored by authenticated bot"
                job = self.queue.update_work_intent(job.id, "work_allowed", f"{reason}; upgraded review-only comment to work_allowed") or job
            reaction_ok = self.react_eyes_for_job_contexts(job)
            self.queue.add_session_event(job.id, "dispatch_started", "OpenClaw agent dispatch started", f"reaction_ok={reaction_ok}")
            model_route = self.policy.model_route_for(job.repo, job.action, job.work_intent)
            self.queue.add_session_event(job.id, "model_route_selected", "OpenClaw model route selected", model_route.summary())
            result = self.dispatcher.dispatch(
                job,
                self.policy,
                reaction_ok=reaction_ok,
                activity_callback=lambda event_type, summary, detail: self.queue.add_session_event(job.id, event_type, summary, redact_event_detail(detail)),
            )
            dispatched = True
            dispatch_detail = "\n".join(part for part in [result.stdout, result.stderr] if part)
            self.queue.add_session_event(
                job.id,
                "dispatch_finished" if result.ok else "dispatch_failed",
                f"OpenClaw agent exited rc={result.returncode}",
                redact_event_detail(dispatch_detail),
            )
            if result.ok:
                followup_url = self.github.visible_followup_after_trigger(job.context)
                missing_followup_ok = self._missing_followup_is_acceptable(job, result)
                if job.work_intent == "work_allowed" and job.action not in {"archive_notification", "workflow_run_failed"} and not followup_url and not missing_followup_ok:
                    summary = "agent finished without visible GitHub follow-up"
                    detail = result.detail or "OpenClaw command succeeded, but no new bot comment was found in the GitHub thread."
                    if job.attempts <= self.config.missing_followup_retries:
                        self.queue.requeue_running(job.id, "agent finished without visible GitHub follow-up; auto-requeued", detail)
                        return True
                    self._finish(job, "blocked", summary, detail, notify_completion=True)
                    return True
                summary = "👀 reaction ok + agent dispatch queued" if reaction_ok else "agent dispatch queued; reaction failed or unavailable"
                detail = f"followup_url={followup_url}; {result.detail}" if followup_url else result.detail
                self._finish(job, "done", summary, detail, notify_completion=True, followup_url=followup_url)
            else:
                reason = "dispatch timeout" if result.timed_out else f"dispatch failed rc={result.returncode}"
                followup_url = self.github.visible_followup_after_trigger(job.context)
                if followup_url:
                    summary = "agent produced visible GitHub follow-up before dispatch failure"
                    detail = f"followup_url={followup_url}; {result.detail}"
                    self._finish(job, "done", summary, detail, notify_completion=True, followup_url=followup_url)
                    return True
                if self._dispatch_failure_is_retryable(result) and job.attempts <= self.config.transient_dispatch_retries:
                    self.queue.requeue_running(job.id, "transient OpenClaw dispatch failure; auto-requeued", result.detail)
                    return True
                self._finish(job, "blocked", reason, result.detail, notify_completion=True)
        except Exception as exc:
            self._finish(job, "blocked", f"executor exception: {type(exc).__name__}", str(exc), notify_completion=dispatched)
        return True

    def _finish(
        self,
        job,
        status: str,
        summary: str,
        detail: str | None = None,
        *,
        notify_completion: bool = False,
        followup_url: str | None = None,
    ) -> None:
        self.queue.finish(job.id, status, summary, detail)
        if not notify_completion:
            return
        actors = [actor for actor in [job.trigger_actor, *self.queue.coalesced_trigger_actors(job.id)] if actor]
        self.github.post_completion_notification(
            job.context,
            actors=actors,
            job_id=job.id,
            work_key=job.work_key,
            status=status,
            summary=summary,
            detail=detail,
            followup_url=followup_url,
        )

    def _missing_followup_is_acceptable(self, job, result) -> bool:
        if job.action not in {"reply_comment", "sync_after_merge"}:
            return False
        output = f"{result.stdout}\n{result.stderr}".lower()
        return any(marker in output for marker in NO_FOLLOWUP_OK_MARKERS) and any(marker in output for marker in NO_FOLLOWUP_DUPLICATE_MARKERS)

    def _dispatch_failure_is_retryable(self, result: DispatchResult) -> bool:
        if result.timed_out:
            return False
        output = f"{result.stdout}\n{result.stderr}\n{result.detail}".lower()
        return any(marker in output for marker in TRANSIENT_DISPATCH_ERROR_MARKERS)

    def react_eyes_for_job_contexts(self, job) -> bool:
        contexts = [job.context, *self.queue.coalesced_contexts(job.id)]
        ok = True
        seen = set()
        for ctx in contexts:
            key = (ctx.repo, ctx.issue_number, ctx.comment_id, ctx.review_comment_id, ctx.review_id)
            if key in seen:
                continue
            seen.add(key)
            ok = self.github.react_eyes(ctx) and ok
        return ok

    def _loop(self, worker_id: str) -> None:
        while not self.stop_event.is_set():
            did = self.work_one(worker_id)
            if self.config.run_once:
                return
            if not did:
                time.sleep(self.config.idle_sleep_seconds)

    def run(self) -> None:
        if self.config.run_once or self.config.workers <= 1:
            self._loop("worker-0")
            return
        threads = [threading.Thread(target=self._loop, args=(f"worker-{i}",), daemon=True) for i in range(self.config.workers)]
        for t in threads: t.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop_event.set()
            for t in threads: t.join(timeout=5)
