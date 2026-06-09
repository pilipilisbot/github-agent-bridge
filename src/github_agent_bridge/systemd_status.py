from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass(frozen=True)
class BridgeUnit:
    role: str
    kind: str
    env: str
    default: str


BRIDGE_UNITS = [
    BridgeUnit("executor", "service", "GITHUB_AGENT_BRIDGE_EXECUTOR_UNIT", "github-agent-bridge.service"),
    BridgeUnit("dashboard", "service", "GITHUB_AGENT_BRIDGE_DASHBOARD_UNIT", "github-agent-bridge-dashboard.service"),
    BridgeUnit("reader", "service", "GITHUB_AGENT_BRIDGE_READER_SERVICE_UNIT", "github-agent-bridge-reader.service"),
    BridgeUnit("reader", "timer", "GITHUB_AGENT_BRIDGE_READER_TIMER_UNIT", "github-agent-bridge-reader.timer"),
    BridgeUnit("monitor", "service", "GITHUB_AGENT_BRIDGE_MONITOR_SERVICE_UNIT", "github-agent-bridge-monitor.service"),
    BridgeUnit("monitor", "timer", "GITHUB_AGENT_BRIDGE_MONITOR_TIMER_UNIT", "github-agent-bridge-monitor.timer"),
    BridgeUnit("feedback", "service", "GITHUB_AGENT_BRIDGE_FEEDBACK_SERVICE_UNIT", "github-agent-bridge-feedback.service"),
    BridgeUnit("feedback", "timer", "GITHUB_AGENT_BRIDGE_FEEDBACK_TIMER_UNIT", "github-agent-bridge-feedback.timer"),
    BridgeUnit("autoupdate", "service", "GITHUB_AGENT_BRIDGE_AUTOUPDATE_SERVICE_UNIT", "github-agent-bridge-autoupdate.service"),
    BridgeUnit("autoupdate", "timer", "GITHUB_AGENT_BRIDGE_AUTOUPDATE_TIMER_UNIT", "github-agent-bridge-autoupdate.timer"),
]

SYSTEMCTL_BIN_ENV = "GITHUB_AGENT_BRIDGE_SYSTEMCTL_BIN"
JOURNALCTL_BIN_ENV = "GITHUB_AGENT_BRIDGE_JOURNALCTL_BIN"


def configured_units() -> list[BridgeUnit]:
    units: list[BridgeUnit] = []
    seen: set[str] = set()
    for unit in BRIDGE_UNITS:
        name = os.getenv(unit.env, unit.default).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        units.append(BridgeUnit(unit.role, unit.kind, unit.env, name))
    return units


def allowed_unit_names() -> set[str]:
    return {unit.default for unit in configured_units()}


def systemd_status(*, systemctl_bin: str | None = None, timeout_seconds: float = 4.0) -> dict[str, Any]:
    systemctl = systemctl_bin or os.getenv(SYSTEMCTL_BIN_ENV, "systemctl")
    units: list[dict[str, Any]] = []
    errors: list[str] = []
    available = True
    for unit in configured_units():
        try:
            proc = subprocess.run(
                [
                    systemctl,
                    "--user",
                    "show",
                    unit.default,
                    "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,ActiveEnterTimestamp,ActiveEnterTimestampMonotonic,InactiveEnterTimestamp,NextElapseUSecRealtime,LastTriggerUSec,UnitFileState",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            available = False
            errors.append(f"{systemctl} not found")
            break
        except subprocess.TimeoutExpired:
            available = False
            errors.append(f"{systemctl} timed out while reading {unit.default}")
            continue
        props = _parse_systemctl_show(proc.stdout)
        if proc.returncode != 0:
            available = False
            detail = proc.stderr.strip() or proc.stdout.strip() or f"{systemctl} exited {proc.returncode}"
            errors.append(f"{unit.default}: {detail}")
        units.append(_unit_payload(unit, props, proc.returncode))
    return {"available": available, "units": units, "errors": errors}


def _parse_systemctl_show(output: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key] = value.strip()
    return props


def _unit_payload(unit: BridgeUnit, props: dict[str, str], returncode: int) -> dict[str, Any]:
    main_pid = _int_or_none(props.get("MainPID"))
    if main_pid == 0:
        main_pid = None
    active_since_monotonic = _int_or_none(props.get("ActiveEnterTimestampMonotonic"))
    return {
        "role": unit.role,
        "kind": unit.kind,
        "unit": unit.default,
        "load_state": props.get("LoadState") or "unknown",
        "active_state": props.get("ActiveState") or "unknown",
        "sub_state": props.get("SubState") or "unknown",
        "result": props.get("Result") or "unknown",
        "exec_main_status": props.get("ExecMainStatus") or None,
        "main_pid": main_pid,
        "uptime_seconds": _monotonic_age_seconds(active_since_monotonic),
        "active_enter_timestamp": props.get("ActiveEnterTimestamp") or "",
        "inactive_enter_timestamp": props.get("InactiveEnterTimestamp") or "",
        "next_elapse": props.get("NextElapseUSecRealtime") or "",
        "last_trigger": props.get("LastTriggerUSec") or "",
        "unit_file_state": props.get("UnitFileState") or "unknown",
        "ok": returncode == 0 and props.get("LoadState") not in {"not-found", "bad-setting", "error"},
    }


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def _monotonic_age_seconds(start_usec: int | None) -> int | None:
    if not start_usec:
        return None
    return max(0, int(time.monotonic() - (start_usec / 1_000_000)))


async def stream_journal_lines(unit: str, *, journalctl_bin: str | None = None, tail: int = 80) -> AsyncIterator[str]:
    journalctl = journalctl_bin or os.getenv(JOURNALCTL_BIN_ENV, "journalctl")
    proc = await asyncio.create_subprocess_exec(
        journalctl,
        "--user",
        "-u",
        unit,
        "-n",
        str(max(1, min(tail, 500))),
        "-f",
        "-o",
        "short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip("\n")
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
