from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .monitor import ALERT_RUNNING_NO_EXECUTOR_CHILD, ALERT_RUNNING_PROCESS_MISMATCH
from .observability import configure_sentry


@dataclass(frozen=True)
class AlertConfig:
    bridge_bin: str
    openclaw_bin: str
    db: str
    policy: str
    channel: str
    target: str
    state_dir: Path
    resend_seconds: int
    auto_unlock_seconds: int | None
    pending_warn_seconds: int
    review_running_warn_seconds: int
    work_running_warn_seconds: int
    progress_warn_seconds: int
    kill_stale_children: bool
    terminate_grace_seconds: int
    proc_idle_seconds: int
    executor_unit: str = "github-agent-bridge.service"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "monitor-alert.state"

    @property
    def proc_state_file(self) -> Path:
        return self.state_dir / "monitor-proc-state.json"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AlertConfig:
        values = env or os.environ
        state_dir = Path(values.get("GITHUB_AGENT_BRIDGE_STATE_DIR", "~/.local/state/github-agent-bridge")).expanduser()
        return cls(
            bridge_bin=values.get("GITHUB_AGENT_BRIDGE_BIN", "~/.local/bin/github-agent-bridge"),
            openclaw_bin=values.get("GITHUB_AGENT_BRIDGE_OPENCLAW_BIN", "~/.nvm/versions/node/v24.14.0/bin/openclaw"),
            db=values.get("GITHUB_AGENT_BRIDGE_DB", "~/.local/state/github-agent-bridge/bridge.sqlite3"),
            policy=values.get("GITHUB_AGENT_BRIDGE_POLICY", "~/.config/github-agent-bridge/policy.json"),
            channel=values.get("GITHUB_AGENT_BRIDGE_ALERT_CHANNEL", "telegram"),
            target=values.get("GITHUB_AGENT_BRIDGE_ALERT_TO", "43532269"),
            state_dir=state_dir,
            resend_seconds=int(values.get("GITHUB_AGENT_BRIDGE_ALERT_RESEND_SECONDS", "900")),
            auto_unlock_seconds=_parse_optional_int(values.get("GITHUB_AGENT_BRIDGE_AUTO_UNLOCK_STALE_SECONDS", "900")),
            pending_warn_seconds=int(values.get("GITHUB_AGENT_BRIDGE_PENDING_WARN_SECONDS", "300")),
            review_running_warn_seconds=int(values.get("GITHUB_AGENT_BRIDGE_REVIEW_RUNNING_WARN_SECONDS", "600")),
            work_running_warn_seconds=int(values.get("GITHUB_AGENT_BRIDGE_WORK_RUNNING_WARN_SECONDS", "900")),
            progress_warn_seconds=int(values.get("GITHUB_AGENT_BRIDGE_PROGRESS_WARN_SECONDS", "600")),
            kill_stale_children=_parse_bool(values.get("GITHUB_AGENT_BRIDGE_KILL_STALE_CHILDREN", "")),
            terminate_grace_seconds=int(values.get("GITHUB_AGENT_BRIDGE_TERMINATE_GRACE_SECONDS", "5")),
            proc_idle_seconds=int(values.get("GITHUB_AGENT_BRIDGE_PROC_IDLE_SECONDS", "240")),
            executor_unit=values.get("GITHUB_AGENT_BRIDGE_EXECUTOR_UNIT", "github-agent-bridge.service"),
        )


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return int(value)


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def _run(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def run_monitor(config: AlertConfig) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            _expand(config.bridge_bin),
            "--db",
            _expand(config.db),
            "--policy",
            _expand(config.policy),
            "monitor",
            "--pending-warn-seconds",
            str(config.pending_warn_seconds),
            "--review-running-warn-seconds",
            str(config.review_running_warn_seconds),
            "--work-running-warn-seconds",
            str(config.work_running_warn_seconds),
            "--progress-warn-seconds",
            str(config.progress_warn_seconds),
            "--executor-unit",
            config.executor_unit,
        ]
    )


def get_main_pid(unit: str = "github-agent-bridge.service") -> str:
    proc = _run(["systemctl", "--user", "show", unit, "--property=MainPID", "--value"])
    return (proc.stdout or "").strip()


def has_child_processes(pid: str) -> bool:
    proc = subprocess.run(["pgrep", "-P", pid], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=False)
    return proc.returncode == 0


def child_pids(pid: str) -> list[int]:
    proc = _run(["pgrep", "-P", pid])
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def descendant_pids(pid: int) -> list[int]:
    out: list[int] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        children = child_pids(str(current))
        out.extend(children)
        stack.extend(children)
    return out


def process_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def proc_cmd(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def proc_cpu_ticks(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    # The second field is the command in parentheses and may contain spaces.
    fields = stat.rsplit(")", 1)[1].strip().split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return 0


def proc_io_bytes(pid: int) -> int:
    total = 0
    try:
        lines = Path(f"/proc/{pid}/io").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for line in lines:
        if line.startswith(("read_bytes:", "write_bytes:")):
            try:
                total += int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return total


def sample_process_tree(root_pid: int, now: int | None = None) -> dict[str, object]:
    now = int(time.time() if now is None else now)
    pids = [root_pid, *descendant_pids(root_pid)]
    live = sorted(pid for pid in pids if process_exists(pid))
    return {
        "ts": now,
        "root_pid": root_pid,
        "pids": live,
        "cmds": {str(pid): proc_cmd(pid) for pid in live},
        "cpu_ticks": sum(proc_cpu_ticks(pid) for pid in live),
        "io_bytes": sum(proc_io_bytes(pid) for pid in live),
    }


def sample_process_forest(root_pids: list[int], now: int | None = None) -> dict[str, object]:
    now = int(time.time() if now is None else now)
    roots = sorted(dict.fromkeys(root_pids))
    pids: set[int] = set()
    for root_pid in roots:
        pids.add(root_pid)
        pids.update(descendant_pids(root_pid))
    live = sorted(pid for pid in pids if process_exists(pid))
    return {
        "ts": now,
        "root_pids": [pid for pid in roots if pid in live],
        "pids": live,
        "cmds": {str(pid): proc_cmd(pid) for pid in live},
        "cpu_ticks": sum(proc_cpu_ticks(pid) for pid in live),
        "io_bytes": sum(proc_io_bytes(pid) for pid in live),
    }


def load_proc_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_proc_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def process_sample_active(previous: dict[str, object] | None, current: dict[str, object]) -> bool:
    if not previous:
        return True
    previous_roots = previous.get("root_pids")
    if previous_roots is None and previous.get("root_pid") is not None:
        previous_roots = [previous.get("root_pid")]
    current_roots = current.get("root_pids")
    if current_roots is None and current.get("root_pid") is not None:
        current_roots = [current.get("root_pid")]
    if previous_roots != current_roots:
        return True
    if previous.get("pids") != current.get("pids"):
        return True
    return (
        int(current.get("cpu_ticks") or 0) > int(previous.get("cpu_ticks") or 0)
        or int(current.get("io_bytes") or 0) > int(previous.get("io_bytes") or 0)
    )


def sample_executor_activity(config: AlertConfig, main_pid: str | None = None, now: int | None = None) -> str:
    main_pid = main_pid or get_main_pid(config.executor_unit)
    if not main_pid or main_pid == "0" or not has_child_processes(main_pid):
        return ""
    children = child_pids(main_pid)
    if not children:
        return ""
    current = sample_process_forest(children, now=now)
    previous = load_proc_state(config.proc_state_file)
    active = process_sample_active(previous, current)
    current["active_since_last_sample"] = active
    if not active and previous.get("ts"):
        current["idle_seconds"] = int(current["ts"]) - int(previous["ts"])
    save_proc_state(config.proc_state_file, current)
    roots = ",".join(str(pid) for pid in current["root_pids"]) or "-"
    return f"proc sample: root_pids={roots} active={active} cpu_ticks={current['cpu_ticks']} io_bytes={current['io_bytes']}\n"


def terminate_process_group(pid: int, grace_seconds: int) -> str:
    try:
        pgid = os.getpgid(pid)
    except OSError as exc:
        return f"pid {pid}: already gone ({exc})"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return f"pid {pid}: already gone"
    except PermissionError as exc:
        return f"pid {pid}: terminate denied ({exc})"
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not process_exists(pid):
            return f"pid {pid}: terminated"
        time.sleep(0.2)
    if process_exists(pid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return f"pid {pid}: terminated"
        except PermissionError as exc:
            return f"pid {pid}: kill denied ({exc})"
        return f"pid {pid}: killed"
    return f"pid {pid}: terminated"


def running_job_ids(output: str) -> list[str]:
    ids = re.findall(r"running job (\d+)\b", output)
    no_child_alert = (
        f"[{ALERT_RUNNING_NO_EXECUTOR_CHILD}]" in output
        or "running jobs exist but executor has no child process" in output
    )
    if no_child_alert:
        ids.extend(re.findall(r"running detail: job=(\d+)\b", output))
    return list(dict.fromkeys(ids))


def process_mismatch_job_ids(output: str) -> list[str]:
    if f"[{ALERT_RUNNING_PROCESS_MISMATCH}]" not in output:
        return []
    return list(
        dict.fromkeys(
            re.findall(r"running job (\d+) process ownership mismatch", output)
            + re.findall(r"running detail: job=(\d+)\b", output)
        )
    )


def block_orphaned_jobs(
    config: AlertConfig,
    job_ids: list[str],
    reason: str,
    *,
    older_than_seconds: int | None = None,
) -> str:
    older_than = (config.auto_unlock_seconds or 0) if older_than_seconds is None else older_than_seconds
    proc = _run(
        [
            _expand(config.bridge_bin),
            "--db",
            _expand(config.db),
            "--policy",
            _expand(config.policy),
            "block-running",
            "--older-than",
            str(older_than),
            "--reason",
            reason,
            *(item for job_id in dict.fromkeys(job_ids) for item in ("--job-id", job_id)),
        ]
    )
    return proc.stdout


def reconcile_process_mismatch(config: AlertConfig, job_ids: list[str]) -> str:
    """Restart the whole executor cgroup so detached descendants cannot survive."""
    main_pid = get_main_pid(config.executor_unit)
    restart_output = ""
    if main_pid and main_pid != "0":
        proc = _run(["systemctl", "--user", "restart", config.executor_unit])
        returncode = int(getattr(proc, "returncode", 0) or 0)
        restart_output = f"executor cgroup restart rc={returncode}\n"
        if proc.stdout:
            restart_output += proc.stdout
    blocked_output = block_orphaned_jobs(
        config,
        job_ids,
        "The registered root process no longer matched this running job. The complete executor cgroup was restarted so detached descendants were stopped; the job was blocked and not auto-requeued.",
        older_than_seconds=0,
    )
    return restart_output + blocked_output


def maybe_unlock_stale(config: AlertConfig, output: str) -> str:
    mismatch_ids = process_mismatch_job_ids(output)
    if mismatch_ids:
        return reconcile_process_mismatch(config, mismatch_ids)
    job_ids = running_job_ids(output)
    if config.auto_unlock_seconds is None or not job_ids:
        return ""
    main_pid = get_main_pid(config.executor_unit)
    if not main_pid or main_pid == "0":
        return block_orphaned_jobs(
            config,
            job_ids,
            "The executor service is inactive and no process owns this stale running job. It was not auto-requeued.",
        )
    child_output = ""
    if has_child_processes(main_pid):
        if not config.kill_stale_children:
            return ""
        sample_output = sample_executor_activity(config, main_pid=main_pid)
        sample = load_proc_state(config.proc_state_file)
        idle_seconds = int(sample.get("idle_seconds") or 0)
        if sample.get("active_since_last_sample", True) or idle_seconds < config.proc_idle_seconds:
            return sample_output
        results = [terminate_process_group(pid, config.terminate_grace_seconds) for pid in child_pids(main_pid)]
        child_output = sample_output + "terminated stale child processes:\n" + "\n".join(results) + "\n"
    blocked_output = block_orphaned_jobs(
        config,
        job_ids,
        "No live executor child owns this stale running job. It was blocked after process reconciliation and not auto-requeued.",
    )
    return child_output + blocked_output


def load_state(path: Path) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    text = path.read_text(encoding="utf-8")
    hash_match = re.search(r"LAST_HASH='([^']*)'", text)
    ts_match = re.search(r"LAST_TS=(\d+)", text)
    if hash_match and ts_match:
        return hash_match.group(1), int(ts_match.group(1))
    parts = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return parts.get("LAST_HASH", ""), int(parts.get("LAST_TS", "0") or "0")


def save_state(path: Path, alert_hash: str, now: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"LAST_HASH='{alert_hash}'\nLAST_TS={now}\n", encoding="utf-8")


def should_send_alert(path: Path, output: str, resend_seconds: int, now: int | None = None) -> bool:
    now = int(time.time() if now is None else now)
    alert_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
    last_hash, last_ts = load_state(path)
    if alert_hash == last_hash and (now - last_ts) < resend_seconds:
        return False
    save_state(path, alert_hash, now)
    return True


def send_alert(config: AlertConfig, output: str, unlock_output: str) -> None:
    _run(
        [
            _expand(config.openclaw_bin),
            "message",
            "send",
            "--channel",
            config.channel,
            "--target",
            config.target,
            "--message",
            f"GitHub bridge ALERTA\n\n{output}\n\n{unlock_output}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    del argv
    configure_sentry(service="monitor-alert")
    config = AlertConfig.from_env()
    config.state_dir.mkdir(parents=True, exist_ok=True)

    monitor_proc = run_monitor(config)
    output = monitor_proc.stdout
    print(output, end="" if output.endswith("\n") else "\n")

    if monitor_proc.returncode == 0:
        if "running detail:" in output:
            sample_output = sample_executor_activity(config)
            if sample_output:
                print(sample_output, end="" if sample_output.endswith("\n") else "\n")
        config.state_file.unlink(missing_ok=True)
        return 0

    unlock_output = maybe_unlock_stale(config, output)
    if unlock_output:
        print(unlock_output, end="" if unlock_output.endswith("\n") else "\n")

    if not should_send_alert(config.state_file, output, config.resend_seconds):
        return 0

    send_alert(config, output, unlock_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
