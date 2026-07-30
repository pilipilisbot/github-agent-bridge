from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from typing import Any

from .dispatch import GitHubClient
from .models import Job
from .process_inspection import process_identity_matches, process_stat
from .queue import JobQueue


@dataclass(frozen=True)
class CancellationResult:
    job: Job | None
    cancelled: bool
    signalled: bool
    followup_url: str | None
    detail: str


def _runtime_process(job: Job) -> dict[str, Any] | None:
    runtime = job.metadata.get("runtime_process")
    return runtime if isinstance(runtime, dict) else None


def _signal_runtime_process(runtime: dict[str, Any], *, grace_seconds: float = 5.0) -> tuple[bool, str]:
    try:
        pid = int(runtime["pid"])
        pgid = int(runtime["pgid"])
        start_time_ticks = int(runtime["start_time_ticks"])
    except (KeyError, TypeError, ValueError):
        return False, "runtime process metadata is incomplete"

    expected_ppid = runtime.get("ppid")
    expected_ppid = int(expected_ppid) if isinstance(expected_ppid, int) else None
    if not process_identity_matches(pid, start_time_ticks, expected_ppid=expected_ppid):
        return False, f"runtime process {pid} is no longer live or no longer matches the registered identity"

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return False, f"runtime process group {pgid} no longer exists"
    except PermissionError:
        return False, f"permission denied while signalling runtime process group {pgid}"

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not process_identity_matches(pid, start_time_ticks, expected_ppid=expected_ppid):
            return True, f"sent SIGTERM to runtime process group {pgid}"
        time.sleep(0.1)

    stat = process_stat(pid)
    if stat is None or stat.get("state") == "Z":
        return True, f"sent SIGTERM to runtime process group {pgid}"
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return True, f"sent SIGTERM to runtime process group {pgid}; SIGKILL escalation could not be delivered"
    return True, f"sent SIGTERM then SIGKILL to runtime process group {pgid}"


def cancellation_comment_body(job: Job, *, actor: str, reason: str | None = None) -> str:
    clean_actor = actor.strip().lstrip("@") or "unknown"
    clean_reason = (reason or "").strip()
    lines = [f"Job #{job.id} has been cancelled by @{clean_actor}."]
    if clean_reason:
        lines.append(f"Reason: {clean_reason}")
    return "\n".join(lines)


def cancel_running_job(
    queue: JobQueue,
    job_id: int,
    *,
    actor: str,
    reason: str | None = None,
    github: GitHubClient | None = None,
    signal_grace_seconds: float = 5.0,
) -> CancellationResult:
    job = queue.request_cancel_running(job_id, actor=actor, reason=reason)
    if job is None:
        return CancellationResult(None, False, False, None, "job is not running")

    runtime = _runtime_process(job)
    if runtime:
        signalled, signal_detail = _signal_runtime_process(runtime, grace_seconds=signal_grace_seconds)
    else:
        signalled = False
        signal_detail = "no runtime process was registered for this job"

    followup_url = None
    github = github or GitHubClient()
    if job.context.repo and job.context.issue_number:
        followup_url = github.comment_on_thread(job.context, cancellation_comment_body(job, actor=actor, reason=reason))

    cancelled_job = queue.mark_cancelled(
        job_id,
        actor=actor,
        reason=reason,
        signal_detail=signal_detail,
        followup_url=followup_url,
    )
    return CancellationResult(cancelled_job, cancelled_job is not None, signalled, followup_url, signal_detail)
