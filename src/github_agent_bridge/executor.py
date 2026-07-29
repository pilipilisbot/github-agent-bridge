from __future__ import annotations

import fcntl
import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass

from .dispatch import GitHubClient, OpenClawDispatcher
from .policy import Policy, complexity_from_metadata
from .queue import JobQueue
from .session_events import redact_event_detail
from .web_push import notify_job_completion


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
    work_intents: frozenset[str] | None = None
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
        self.executor_id = f"executor-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._executor_lock_file = None

    def work_one(self, worker_id: str | None = None) -> bool:
        worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        job = self.queue.claim_next(worker_id, self.config.work_intents)
        if not job:
            return False
        if self.stop_event.is_set():
            self.queue.block_running(
                "executor shutdown interrupted job before dispatch",
                "The executor received a shutdown request after claiming this job. It was not dispatched or auto-requeued.",
                job_ids=[job.id],
                locked_by={worker_id},
            )
            return True
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
                self.queue.add_session_event(
                    job.id,
                    "action_mode_retained",
                    "review_only retained; assignment/authorship alone does not grant write permission",
                    reason,
                )
            reaction_ok = self.react_eyes_for_job_contexts(job)
            self.queue.add_session_event(job.id, "dispatch_started", "OpenClaw agent dispatch started", f"reaction_ok={reaction_ok}")
            complexity = complexity_from_metadata(job.metadata)
            model_route = self.policy.model_route_for(job.repo, job.action, job.work_intent, complexity)
            self.queue.add_session_event(job.id, "model_route_selected", "OpenClaw model route selected", model_route.summary())
            if job.metadata.get("fresh_session_on_retry") and job.attempts > 1:
                self.queue.add_session_event(
                    job.id,
                    "session_rescue_selected",
                    "fresh OpenClaw session selected after compaction failure",
                    f"attempt={job.attempts}",
                )
            try:
                result = self.dispatcher.dispatch(
                    job,
                    self.policy,
                    reaction_ok=reaction_ok,
                    activity_callback=lambda event_type, summary, detail: self.queue.add_session_event(job.id, event_type, summary, redact_event_detail(detail)),
                    process_callback=lambda identity: self.queue.register_runtime_process(
                        job.id,
                        worker_id,
                        self.executor_id,
                        identity,
                    ),
                )
            finally:
                self.queue.mark_runtime_process_exited(job.id, worker_id)
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
                reason = (
                    "executor shutdown interrupted dispatch"
                    if result.cancelled
                    else "dispatch timeout"
                    if result.timed_out
                    else f"dispatch failed rc={result.returncode}"
                )
                followup_url = self.github.visible_followup_after_trigger(job.context)
                if followup_url:
                    summary = "dispatch failed after producing visible GitHub follow-up"
                    detail = f"followup_url={followup_url}; {reason}; {result.detail}"
                    self._finish(job, "blocked", summary, detail, notify_completion=True, followup_url=followup_url)
                    return True
                if self._dispatch_failure_is_retryable(result) and job.attempts <= self.config.transient_dispatch_retries:
                    self.queue.requeue_running(
                        job.id,
                        "transient OpenClaw dispatch failure; auto-requeued",
                        result.detail,
                        fresh_session=self._dispatch_failure_needs_fresh_session(result),
                    )
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
        notify_job_completion(
            self.queue.path,
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

    @staticmethod
    def _dispatch_failure_needs_fresh_session(result: DispatchResult) -> bool:
        output = f"{result.stdout}\n{result.stderr}\n{result.detail}".lower()
        return "transcript compaction failed" in output or "turn prefix summarization failed" in output

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

    def _request_shutdown(self) -> None:
        self.stop_event.set()
        shutdown = getattr(self.dispatcher, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def _handle_signal(self, signum, frame) -> None:
        self._request_shutdown()

    def _install_signal_handlers(self) -> dict[signal.Signals, object]:
        if threading.current_thread() is not threading.main_thread():
            return {}
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, self._handle_signal)
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: dict[signal.Signals, object]) -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    def _acquire_executor_lock(self) -> None:
        lock_path = self.queue.path.with_name(f"{self.queue.path.name}.executor.lock")
        lock_file = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError(f"another executor already holds {lock_path}") from None
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{self.executor_id}\n")
        lock_file.flush()
        self._executor_lock_file = lock_file

    def _release_executor_lock(self) -> None:
        if self._executor_lock_file is None:
            return
        fcntl.flock(self._executor_lock_file.fileno(), fcntl.LOCK_UN)
        self._executor_lock_file.close()
        self._executor_lock_file = None

    def run(self) -> None:
        self._acquire_executor_lock()
        previous_handlers = self._install_signal_handlers()
        worker_count = 1 if self.config.run_once or self.config.workers <= 1 else self.config.workers
        worker_ids = [f"{self.executor_id}/worker-{i}" for i in range(worker_count)]
        threads: list[threading.Thread] = []
        try:
            self.queue.block_running(
                "orphaned running job recovered at executor startup",
                "No prior executor process owns this running job. It was blocked, not auto-requeued, to avoid duplicate external actions.",
            )
            self.queue.set_state("executor_process_tracking_id", self.executor_id)
            threads = [
                threading.Thread(target=self._loop, args=(worker_id,), daemon=False)
                for worker_id in worker_ids
            ]
            for thread in threads:
                thread.start()
            while any(thread.is_alive() for thread in threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._request_shutdown()
        finally:
            self._request_shutdown()
            for thread in threads:
                thread.join()
            self.queue.block_running(
                "executor shutdown interrupted running job",
                "The executor stopped before this job completed. It was blocked, not auto-requeued, to avoid duplicate external actions.",
                locked_by=set(worker_ids),
            )
            self._restore_signal_handlers(previous_handlers)
            self._release_executor_lock()
