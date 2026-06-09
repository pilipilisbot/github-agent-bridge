from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .models import utc_now
from .queue import JobQueue

CommandRunner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]

UPDATE_STATE_KEY = "autoupdate"
ACTIVE_JOB_STATUSES = ("pending", "running", "waiting_approval")
RISKY_PATH_PREFIXES = (
    "src/github_agent_bridge/cli.py",
    "src/github_agent_bridge/dispatch.py",
    "src/github_agent_bridge/executor.py",
    "src/github_agent_bridge/monitor.py",
    "src/github_agent_bridge/parser.py",
    "src/github_agent_bridge/policy.py",
    "src/github_agent_bridge/queue.py",
    "src/github_agent_bridge/reader.py",
    "src/github_agent_bridge/reader_run.py",
    "src/github_agent_bridge/sql/",
)
DASHBOARD_PATH_PREFIXES = (
    "dashboard/",
    "src/github_agent_bridge/backend.py",
    "src/github_agent_bridge/dashboard_data.py",
    "src/github_agent_bridge/dashboard_static/",
)
SYSTEMD_PATH_PREFIXES = ("systemd/",)
DEFAULT_SYSTEMD_UNITS = {
    "executor": "github-agent-bridge.service",
    "dashboard": "github-agent-bridge-dashboard.service",
    "reader": "github-agent-bridge-reader.timer",
    "monitor": "github-agent-bridge-monitor.timer",
    "feedback": "github-agent-bridge-feedback.timer",
    "autoupdate_service": "github-agent-bridge-autoupdate.service",
    "autoupdate_timer": "github-agent-bridge-autoupdate.timer",
}


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str = ""
    url: str = ""
    body: str = ""
    published_at: str = ""
    source: str = "github_release"

    def to_json(self) -> dict[str, str]:
        return {
            "tag_name": self.tag_name,
            "name": self.name,
            "url": self.url,
            "body": self.body,
            "published_at": self.published_at,
            "source": self.source,
        }


def _default_runner(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _run_json(args: Sequence[str], cwd: Path | None, runner: CommandRunner) -> dict[str, Any]:
    proc = runner(args, cwd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{args[0]} failed with exit code {proc.returncode}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{args[0]} returned invalid JSON") from exc


def latest_release(repo: str, *, gh_bin: str = "gh", runner: CommandRunner = _default_runner) -> ReleaseInfo:
    data = _run_json(
        [
            gh_bin,
            "release",
            "view",
            "--repo",
            repo,
            "--json",
            "tagName,name,url,body,publishedAt,isDraft,isPrerelease",
        ],
        None,
        runner,
    )
    tag_name = str(data.get("tagName") or "")
    if not tag_name:
        raise RuntimeError("latest release did not include a tagName")
    return ReleaseInfo(
        tag_name=tag_name,
        name=str(data.get("name") or ""),
        url=str(data.get("url") or ""),
        body=str(data.get("body") or ""),
        published_at=str(data.get("publishedAt") or ""),
    )


def _git_output(args: Sequence[str], repo_dir: Path, runner: CommandRunner) -> str:
    proc = runner(["git", *args], repo_dir)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout.strip()


def changed_files_between(repo_dir: Path, base_ref: str, target_ref: str, *, runner: CommandRunner = _default_runner) -> list[str]:
    output = _git_output(["diff", "--name-only", f"{base_ref}..{target_ref}"], repo_dir, runner)
    return [line.strip() for line in output.splitlines() if line.strip()]


def classify_changed_files(files: Sequence[str]) -> dict[str, Any]:
    risky_files = [path for path in files if path.startswith(RISKY_PATH_PREFIXES)]
    migration_files = [path for path in files if path.startswith("src/github_agent_bridge/sql/") or "/migrations/" in path]
    dashboard_files = [path for path in files if path.startswith(DASHBOARD_PATH_PREFIXES)]
    systemd_files = [path for path in files if path.startswith(SYSTEMD_PATH_PREFIXES)]
    dashboard_only = bool(files) and len(dashboard_files) == len(files)
    risk = "dashboard_only" if dashboard_only else "executor_or_shared"
    if migration_files:
        risk = "migration_required"
    elif risky_files:
        risk = "executor_or_queue"
    elif systemd_files:
        risk = "service_topology"
    elif not files:
        risk = "none"
    return {
        "risk": risk,
        "dashboard_only": dashboard_only,
        "risky_files": risky_files,
        "migration_files": migration_files,
        "systemd_files": systemd_files,
        "changed_files": list(files),
    }


def plan_systemd_actions(decision: str, classification: dict[str, Any], *, units: dict[str, str] | None = None) -> dict[str, Any]:
    unit_names = {**DEFAULT_SYSTEMD_UNITS, **(units or {})}
    systemd_files = list(classification.get("systemd_files") or [])
    daemon_reload = bool(systemd_files)

    immediate: list[dict[str, str]] = []
    deferred: list[dict[str, str]] = []
    notes: list[str] = []

    def action(command: str, unit_key: str, reason: str) -> dict[str, str]:
        unit = unit_names.get(unit_key, "")
        return {"command": command, "unit": unit, "reason": reason}

    if daemon_reload:
        immediate.append({"command": "daemon-reload", "unit": "--user", "reason": "systemd unit files changed"})

    if decision == "stage_dashboard_reload":
        immediate.append(action("try-restart", "dashboard", "dashboard-only update can reload independently"))
    elif decision == "stage_defer_executor_reload":
        immediate.append(action("try-restart", "dashboard", "dashboard can refresh while executor jobs finish"))
        deferred.append(action("restart", "executor", "executor/shared update waits for active queue to drain"))
    elif decision == "stage_full_reload":
        immediate.append(action("try-restart", "dashboard", "refresh dashboard after package update"))
        immediate.append(action("restart", "executor", "queue is quiet, executor reload is allowed"))
    elif decision == "defer_migration":
        deferred.append(action("restart", "executor", "schema migration must wait for active queue to drain"))
        deferred.append(action("try-restart", "dashboard", "dashboard refresh waits for migration window"))

    if daemon_reload:
        affected_units = sorted(
            {
                unit_names[key]
                for path in systemd_files
                for key, filename in (
                    ("executor", "github-agent-bridge.service"),
                    ("dashboard", "github-agent-bridge-dashboard.service"),
                    ("reader", "github-agent-bridge-reader.timer"),
                    ("monitor", "github-agent-bridge-monitor.timer"),
                    ("feedback", "github-agent-bridge-feedback.timer"),
                    ("autoupdate_service", "github-agent-bridge-autoupdate.service"),
                    ("autoupdate_timer", "github-agent-bridge-autoupdate.timer"),
                )
                if path.endswith(filename)
            }
        )
        if affected_units:
            notes.append("Unit changes detected: " + ", ".join(affected_units))
        notes.append("Run systemctl --user daemon-reload before restarting changed units.")

    return {
        "manager": "systemd",
        "scope": "user",
        "units": unit_names,
        "daemon_reload_required": daemon_reload,
        "immediate": immediate,
        "deferred": deferred,
        "notes": notes,
    }


def active_queue_counts(queue: JobQueue) -> dict[str, int]:
    stats = queue.stats()
    return {status: int(stats.get(status, 0)) for status in ACTIVE_JOB_STATUSES}


def load_update_state(queue: JobQueue) -> dict[str, Any]:
    raw = queue.get_state(UPDATE_STATE_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"state_error": "invalid_autoupdate_state"}
    return data if isinstance(data, dict) else {}


def save_update_state(queue: JobQueue, state: dict[str, Any]) -> None:
    queue.set_state(UPDATE_STATE_KEY, json.dumps(state, sort_keys=True))


def default_install_command(repo: str, target_tag: str, *, python_bin: str = sys.executable) -> list[str]:
    repo_ref = repo
    if not repo_ref.startswith(("http://", "https://", "git@")):
        repo_ref = f"https://github.com/{repo_ref}.git"
    return [python_bin, "-m", "pip", "install", f"git+{repo_ref}@{target_tag}"]


def _command_result(
    kind: str,
    args: Sequence[str],
    proc: subprocess.CompletedProcess[str],
    *,
    unit: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "command": list(args),
        "unit": unit,
        "reason": reason,
        "returncode": proc.returncode,
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def apply_update_plan(
    plan: dict[str, Any],
    *,
    repo: str = "pilipilisbot/github-agent-bridge",
    install_command: Sequence[str] | None = None,
    systemctl_bin: str = "systemctl",
    run_install: bool = True,
    run_systemd: bool = True,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    """Execute the safe, immediate subset described by an update plan."""

    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    target_tag = str(target.get("tag_name") or "")
    classification = plan.get("classification") if isinstance(plan.get("classification"), dict) else {}
    result: dict[str, Any] = {
        "applied": False,
        "blocked": [],
        "commands": [],
    }

    if plan.get("decision") == "noop" or plan.get("up_to_date"):
        result["applied"] = True
        return result
    if not target_tag:
        result["blocked"].append("missing_target_tag")
        return result
    if classification.get("migration_files"):
        result["blocked"].append("migration_execution_not_supported")
        return result

    if run_install:
        command = list(install_command or default_install_command(repo, target_tag))
        proc = runner(command, None)
        result["commands"].append(_command_result("install", command, proc, reason=f"install {target_tag}"))
        if proc.returncode != 0:
            return result

    if run_systemd:
        service_plan = plan.get("service_plan") if isinstance(plan.get("service_plan"), dict) else {}
        if not _run_systemd_actions(service_plan.get("immediate") or [], result, systemctl_bin=systemctl_bin, runner=runner):
            return result

    result["applied"] = True
    return result


def _run_systemd_actions(
    actions: Sequence[dict[str, Any]],
    result: dict[str, Any],
    *,
    systemctl_bin: str,
    runner: CommandRunner,
) -> bool:
    for action in actions:
        command = str(action.get("command") or "")
        unit = str(action.get("unit") or "")
        if not command:
            continue
        if command == "daemon-reload":
            args = [systemctl_bin, "--user", "daemon-reload"]
        elif unit:
            args = [systemctl_bin, "--user", command, unit]
        else:
            result["blocked"].append(f"missing_unit_for_{command}")
            return False
        proc = runner(args, None)
        result["commands"].append(
            _command_result("systemd", args, proc, unit=unit, reason=str(action.get("reason") or ""))
        )
        if proc.returncode != 0:
            return False
    return True


def complete_pending_reload(
    db: str | Path,
    *,
    systemctl_bin: str = "systemctl",
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    """Run a recorded deferred executor reload once the queue is quiet."""

    queue = JobQueue(db)
    state = load_update_state(queue)
    active_counts = active_queue_counts(queue)
    active_total = sum(active_counts.values())
    result: dict[str, Any] = {
        "completed": False,
        "blocked": [],
        "commands": [],
        "queue": {
            "active_counts": active_counts,
            "active_total": active_total,
        },
        "state": state,
    }

    if not state:
        result["blocked"].append("no_recorded_update")
        return result
    if state.get("state_error"):
        result["blocked"].append(str(state["state_error"]))
        return result
    if not state.get("executor_reload_pending"):
        result["completed"] = True
        result["message"] = "no_pending_executor_reload"
        return result

    classification = state.get("classification") if isinstance(state.get("classification"), dict) else {}
    if classification.get("migration_files"):
        result["blocked"].append("migration_completion_not_supported")
        return result
    if active_total:
        result["blocked"].append("active_jobs_block_executor_reload")
        updated_state = {
            **state,
            "updated_at": utc_now(),
            "blocked_reason": "active_jobs_block_executor_reload",
            "queue": result["queue"],
        }
        save_update_state(queue, updated_state)
        result["state"] = updated_state
        return result

    service_plan = state.get("service_plan") if isinstance(state.get("service_plan"), dict) else {}
    deferred_actions = service_plan.get("deferred") or []
    if not deferred_actions:
        result["blocked"].append("no_deferred_actions")
        return result

    if not _run_systemd_actions(deferred_actions, result, systemctl_bin=systemctl_bin, runner=runner):
        return result

    completed_at = utc_now()
    completed_state = {
        **state,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "decision": "noop",
        "executor_reload_pending": False,
        "blocked_reason": "",
        "queue": result["queue"],
        "service_plan": {**service_plan, "deferred": []},
        "completion": {
            "commands": result["commands"],
            "completed_at": completed_at,
        },
    }
    save_update_state(queue, completed_state)
    result["completed"] = True
    result["state"] = completed_state
    return result


def plan_update(
    db: str | Path,
    *,
    repo: str = "pilipilisbot/github-agent-bridge",
    repo_dir: str | Path = ".",
    target_tag: str | None = None,
    gh_bin: str = "gh",
    installed_version: str = __version__,
    systemd_units: dict[str, str] | None = None,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    queue = JobQueue(db)
    repo_path = Path(repo_dir).expanduser().resolve()
    current_tag = f"v{installed_version.lstrip('v')}"
    release = ReleaseInfo(tag_name=target_tag, source="explicit_target") if target_tag else latest_release(repo, gh_bin=gh_bin, runner=runner)
    active_counts = active_queue_counts(queue)
    active_total = sum(active_counts.values())

    warnings: list[str] = []
    try:
        files = [] if release.tag_name == current_tag else changed_files_between(repo_path, current_tag, release.tag_name, runner=runner)
    except RuntimeError as exc:
        files = []
        warnings.append(f"changed_files_unavailable: {exc}")
    classification = classify_changed_files(files)
    up_to_date = release.tag_name == current_tag
    migration_required = bool(classification["migration_files"])
    executor_reload_pending = False
    dashboard_restart_allowed = False
    executor_restart_allowed = False
    blocked_reason = ""

    if up_to_date:
        decision = "noop"
    elif migration_required and active_total:
        decision = "defer_migration"
        blocked_reason = "active_jobs_block_migration"
    elif classification["dashboard_only"]:
        decision = "stage_dashboard_reload"
        dashboard_restart_allowed = True
    elif active_total:
        decision = "stage_defer_executor_reload"
        executor_reload_pending = True
        dashboard_restart_allowed = True
        blocked_reason = "active_jobs_block_executor_reload"
    else:
        decision = "stage_full_reload"
        dashboard_restart_allowed = True
        executor_restart_allowed = True

    service_plan = plan_systemd_actions(decision, classification, units=systemd_units)

    return {
        "checked_at": utc_now(),
        "installed_version": installed_version,
        "installed_tag": current_tag,
        "target": release.to_json(),
        "up_to_date": up_to_date,
        "queue": {
            "active_counts": active_counts,
            "active_total": active_total,
        },
        "classification": classification,
        "decision": decision,
        "dashboard_restart_allowed": dashboard_restart_allowed,
        "executor_restart_allowed": executor_restart_allowed,
        "executor_reload_pending": executor_reload_pending,
        "blocked_reason": blocked_reason,
        "service_plan": service_plan,
        "warnings": warnings,
    }


def record_update_plan(db: str | Path, plan: dict[str, Any]) -> dict[str, Any]:
    queue = JobQueue(db)
    state = {
        "updated_at": utc_now(),
        "installed_version": plan["installed_version"],
        "installed_tag": plan["installed_tag"],
        "target": plan["target"],
        "decision": plan["decision"],
        "executor_reload_pending": bool(plan["executor_reload_pending"]),
        "blocked_reason": plan["blocked_reason"],
        "queue": plan["queue"],
        "classification": {
            "risk": plan["classification"]["risk"],
            "migration_files": plan["classification"]["migration_files"],
            "risky_files": plan["classification"]["risky_files"],
            "systemd_files": plan["classification"].get("systemd_files", []),
        },
        "service_plan": plan.get("service_plan", {}),
        "warnings": plan["warnings"],
    }
    save_update_state(queue, state)
    return state
