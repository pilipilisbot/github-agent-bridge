from __future__ import annotations

import re


JOB_SCOPE_PATTERN = re.compile(
    r"^github-agent-bridge-job-(?P<job_id>\d+)-attempt-(?P<attempt>\d+)\.scope$"
)


def job_scope_unit(job_id: int, attempt: int) -> str:
    safe_attempt = max(1, int(attempt or 1))
    return f"github-agent-bridge-job-{int(job_id)}-attempt-{safe_attempt}.scope"


def is_expected_job_scope(unit: str, job_id: int, attempt: int) -> bool:
    return unit == job_scope_unit(job_id, attempt) and JOB_SCOPE_PATTERN.fullmatch(unit) is not None


def is_job_scope_name(unit: str) -> bool:
    return JOB_SCOPE_PATTERN.fullmatch(unit) is not None
