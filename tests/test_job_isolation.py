from github_agent_bridge.job_isolation import (
    is_expected_job_scope,
    is_job_scope_name,
    job_scope_unit,
)


def test_job_scope_unit_is_deterministic_per_job_attempt():
    assert job_scope_unit(3025, 2) == (
        "github-agent-bridge-job-3025-attempt-2.scope"
    )


def test_job_scope_validation_rejects_other_jobs_and_arbitrary_units():
    unit = job_scope_unit(3025, 2)

    assert is_expected_job_scope(unit, 3025, 2) is True
    assert is_expected_job_scope(unit, 3026, 2) is False
    assert is_expected_job_scope(unit, 3025, 3) is False
    assert is_job_scope_name("github-agent-bridge.service") is False
    assert is_job_scope_name("github-agent-bridge-job-3025-attempt-2.scope;rm") is False
