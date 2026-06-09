from __future__ import annotations

import json
import subprocess
from pathlib import Path

from github_agent_bridge.autoupdate import (
    apply_update_plan,
    complete_pending_reload,
    default_install_command,
    load_update_state,
    plan_systemd_actions,
    plan_update,
    record_update_plan,
)
from github_agent_bridge.models import Notification
from github_agent_bridge.policy import Policy
from github_agent_bridge.queue import JobQueue


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["fake"], returncode, stdout, "")


def release_runner(tag: str, files: list[str]):
    def run(args, cwd: Path | None):
        if args[:3] == ["gh", "release", "view"]:
            return completed(json.dumps({"tagName": tag, "name": tag, "url": f"https://github.com/example/repo/releases/tag/{tag}"}))
        if args[:2] == ["git", "diff"]:
            return completed("\n".join(files))
        return completed("", 1)

    return run


def enqueue_job(q: JobQueue) -> int:
    job, state = q.enqueue(
        Notification(
            uid=1,
            message_id="<1@github.com>",
            subject="Re: [gisce/erp] thing",
            from_addr="GitHub <notifications@github.com>",
            body="@pilipilisbot https://github.com/gisce/erp/pull/1#issuecomment-10",
            auth={"spf": True, "dkim": True, "dmarc": True},
        ),
        Policy(trusted_orgs={"gisce"}, bot_logins={"pilipilisbot"}),
    )
    assert state == "enqueued"
    assert job is not None
    return job.id


def test_update_plan_noops_when_release_matches_installed_version(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    plan = plan_update(db, repo_dir=tmp_path, installed_version="1.2.3", runner=release_runner("v1.2.3", []))

    assert plan["up_to_date"] is True
    assert plan["decision"] == "noop"
    assert plan["executor_reload_pending"] is False


def test_dashboard_only_update_can_stage_while_jobs_are_active(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    enqueue_job(q)

    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["dashboard/src/main.tsx", "src/github_agent_bridge/dashboard_static/index.html"]),
    )

    assert plan["decision"] == "stage_dashboard_reload"
    assert plan["classification"]["dashboard_only"] is True
    assert plan["dashboard_restart_allowed"] is True
    assert plan["executor_reload_pending"] is False
    assert plan["service_plan"]["immediate"] == [
        {
            "command": "try-restart",
            "unit": "github-agent-bridge-dashboard.service",
            "reason": "dashboard-only update can reload independently",
        }
    ]


def test_executor_update_records_pending_reload_when_jobs_are_active(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    enqueue_job(q)

    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["src/github_agent_bridge/executor.py", "tests/test_executor.py"]),
    )
    state = record_update_plan(db, plan)

    assert plan["decision"] == "stage_defer_executor_reload"
    assert plan["blocked_reason"] == "active_jobs_block_executor_reload"
    assert plan["service_plan"]["immediate"][0]["unit"] == "github-agent-bridge-dashboard.service"
    assert plan["service_plan"]["deferred"][0]["unit"] == "github-agent-bridge.service"
    assert state["executor_reload_pending"] is True
    assert load_update_state(q)["decision"] == "stage_defer_executor_reload"
    assert load_update_state(q)["service_plan"]["deferred"][0]["command"] == "restart"


def test_migration_update_is_deferred_while_jobs_are_active(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    enqueue_job(q)

    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["src/github_agent_bridge/sql/schema.sql"]),
    )

    assert plan["decision"] == "defer_migration"
    assert plan["classification"]["migration_files"] == ["src/github_agent_bridge/sql/schema.sql"]
    assert plan["executor_restart_allowed"] is False
    assert plan["blocked_reason"] == "active_jobs_block_migration"
    assert plan["service_plan"]["immediate"] == []
    assert plan["service_plan"]["deferred"][0]["unit"] == "github-agent-bridge.service"


def test_full_update_is_allowed_when_queue_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["src/github_agent_bridge/executor.py"]),
    )

    assert plan["decision"] == "stage_full_reload"
    assert plan["executor_restart_allowed"] is True
    assert [item["unit"] for item in plan["service_plan"]["immediate"]] == [
        "github-agent-bridge-dashboard.service",
        "github-agent-bridge.service",
    ]


def test_systemd_unit_changes_require_daemon_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    JobQueue(db)

    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["systemd/github-agent-bridge.service"]),
    )

    assert plan["classification"]["risk"] == "service_topology"
    assert plan["classification"]["systemd_files"] == ["systemd/github-agent-bridge.service"]
    assert plan["service_plan"]["daemon_reload_required"] is True
    assert plan["service_plan"]["immediate"][0] == {
        "command": "daemon-reload",
        "unit": "--user",
        "reason": "systemd unit files changed",
    }
    assert "github-agent-bridge.service" in plan["service_plan"]["notes"][0]


def test_systemd_plan_accepts_custom_unit_names():
    plan = plan_systemd_actions(
        "stage_full_reload",
        {"systemd_files": [], "risk": "executor_or_queue"},
        units={"executor": "custom-executor.service", "dashboard": "custom-dashboard.service"},
    )

    assert [item["unit"] for item in plan["immediate"]] == ["custom-dashboard.service", "custom-executor.service"]


def test_default_install_command_targets_release_tag():
    assert default_install_command("pilipilisbot/github-agent-bridge", "v1.2.4", python_bin="python") == [
        "python",
        "-m",
        "pip",
        "install",
        "git+https://github.com/pilipilisbot/github-agent-bridge.git@v1.2.4",
    ]


def test_apply_update_plan_installs_and_runs_immediate_systemd_actions():
    calls: list[list[str]] = []

    def runner(args, cwd: Path | None):
        calls.append(list(args))
        return completed("ok")

    execution = apply_update_plan(
        {
            "target": {"tag_name": "v1.2.4"},
            "decision": "stage_full_reload",
            "up_to_date": False,
            "classification": {"migration_files": []},
            "service_plan": {
                "immediate": [
                    {"command": "try-restart", "unit": "github-agent-bridge-dashboard.service", "reason": "refresh dashboard"},
                    {"command": "restart", "unit": "github-agent-bridge.service", "reason": "queue is quiet"},
                ]
            },
        },
        repo="pilipilisbot/github-agent-bridge",
        install_command=["python", "-m", "pip", "install", "pkg"],
        runner=runner,
    )

    assert execution["applied"] is True
    assert execution["blocked"] == []
    assert calls == [
        ["python", "-m", "pip", "install", "pkg"],
        ["systemctl", "--user", "try-restart", "github-agent-bridge-dashboard.service"],
        ["systemctl", "--user", "restart", "github-agent-bridge.service"],
    ]


def test_apply_update_plan_blocks_migration_execution_before_install():
    calls: list[list[str]] = []

    execution = apply_update_plan(
        {
            "target": {"tag_name": "v1.2.4"},
            "decision": "stage_full_reload",
            "classification": {"migration_files": ["src/github_agent_bridge/sql/schema.sql"]},
            "service_plan": {"immediate": [{"command": "restart", "unit": "github-agent-bridge.service"}]},
        },
        runner=lambda args, cwd: calls.append(list(args)) or completed("ok"),
    )

    assert execution["applied"] is False
    assert execution["blocked"] == ["migration_execution_not_supported"]
    assert calls == []


def test_apply_update_plan_stops_before_services_when_install_fails():
    calls: list[list[str]] = []

    def runner(args, cwd: Path | None):
        calls.append(list(args))
        if args[:3] == ["python", "-m", "pip"]:
            return completed("install failed", returncode=1)
        return completed("service should not run")

    execution = apply_update_plan(
        {
            "target": {"tag_name": "v1.2.4"},
            "decision": "stage_dashboard_reload",
            "classification": {"migration_files": []},
            "service_plan": {"immediate": [{"command": "try-restart", "unit": "github-agent-bridge-dashboard.service"}]},
        },
        install_command=["python", "-m", "pip", "install", "pkg"],
        runner=runner,
    )

    assert execution["applied"] is False
    assert calls == [["python", "-m", "pip", "install", "pkg"]]


def test_complete_pending_reload_blocks_until_queue_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    enqueue_job(q)
    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["src/github_agent_bridge/executor.py"]),
    )
    record_update_plan(db, plan)

    completion = complete_pending_reload(db, runner=lambda args, cwd: completed("should not run"))

    assert completion["completed"] is False
    assert completion["blocked"] == ["active_jobs_block_executor_reload"]
    assert completion["commands"] == []
    assert load_update_state(q)["executor_reload_pending"] is True
    assert load_update_state(q)["queue"]["active_total"] == 1


def test_complete_pending_reload_runs_deferred_actions_and_clears_state(tmp_path, monkeypatch):
    monkeypatch.setattr("github_agent_bridge.actors.github_actor_details_for_context", lambda ctx, *, gh_bin="gh": None)
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    job_id = enqueue_job(q)
    plan = plan_update(
        db,
        repo_dir=tmp_path,
        installed_version="1.2.3",
        runner=release_runner("v1.2.4", ["src/github_agent_bridge/executor.py"]),
    )
    record_update_plan(db, plan)
    q.finish(job_id, "done", "finished")
    calls: list[list[str]] = []

    completion = complete_pending_reload(
        db,
        systemctl_bin="systemctl-test",
        runner=lambda args, cwd: calls.append(list(args)) or completed("ok"),
    )

    state = load_update_state(q)
    assert completion["completed"] is True
    assert completion["blocked"] == []
    assert calls == [["systemctl-test", "--user", "restart", "github-agent-bridge.service"]]
    assert state["executor_reload_pending"] is False
    assert state["decision"] == "noop"
    assert state["service_plan"]["deferred"] == []
    assert state["completion"]["commands"][0]["unit"] == "github-agent-bridge.service"


def test_complete_pending_reload_refuses_migration_state(tmp_path):
    db = tmp_path / "bridge.sqlite3"
    q = JobQueue(db)
    q.set_state(
        "autoupdate",
        json.dumps(
            {
                "executor_reload_pending": True,
                "classification": {"migration_files": ["src/github_agent_bridge/sql/schema.sql"]},
                "service_plan": {"deferred": [{"command": "restart", "unit": "github-agent-bridge.service"}]},
            }
        ),
    )

    completion = complete_pending_reload(db, runner=lambda args, cwd: completed("should not run"))

    assert completion["completed"] is False
    assert completion["blocked"] == ["migration_completion_not_supported"]
    assert completion["commands"] == []
