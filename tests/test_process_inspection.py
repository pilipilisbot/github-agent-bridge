from __future__ import annotations

from pathlib import Path

from github_agent_bridge.process_inspection import (
    cgroup_pids,
    direct_children,
    inspect_process,
    process_cgroup,
    process_identity_matches,
)


def write_proc(root: Path, pid: int, *, ppid: int, cmd: str, cpu_user: int = 1, cpu_system: int = 2, read_bytes: int = 3, write_bytes: int = 4) -> None:
    proc = root / str(pid)
    proc.mkdir(parents=True)
    fields = ["S", str(ppid), str(pid), str(pid), "0", "0", "0", "0", "0", "0", "0", str(cpu_user), str(cpu_system)]
    fields.extend(["0"] * 6)
    fields.append(str(pid * 100))
    (proc / "stat").write_text(f"{pid} ({cmd}) {' '.join(fields)}\n", encoding="utf-8")
    (proc / "cmdline").write_bytes(cmd.encode("utf-8") + b"\0--flag")
    (proc / "io").write_text(f"read_bytes: {read_bytes}\nwrite_bytes: {write_bytes}\n", encoding="utf-8")


def test_inspect_process_reads_command_cpu_io_and_children(tmp_path):
    write_proc(tmp_path, 10, ppid=1, cmd="executor", cpu_user=5, cpu_system=7)
    write_proc(tmp_path, 11, ppid=10, cmd="openclaw agent", read_bytes=100, write_bytes=200)

    sample = inspect_process(10, proc_root=tmp_path, include_children=True)

    assert sample is not None
    assert sample["pid"] == 10
    assert sample["cmd"] == "executor --flag"
    assert sample["cpu_ticks"] == 12
    assert sample["children"][0]["pid"] == 11
    assert sample["children"][0]["io_bytes"] == {"read_bytes": 100, "write_bytes": 200}


def test_direct_children_ignores_missing_or_unrelated_processes(tmp_path):
    write_proc(tmp_path, 20, ppid=1, cmd="executor")
    write_proc(tmp_path, 21, ppid=20, cmd="worker")
    write_proc(tmp_path, 22, ppid=2, cmd="other")

    assert [child["pid"] for child in direct_children(20, proc_root=tmp_path)] == [21]


def test_process_identity_matches_pid_birth_and_parent(tmp_path):
    write_proc(tmp_path, 30, ppid=10, cmd="openclaw")

    assert process_identity_matches(30, 3000, expected_ppid=10, proc_root=tmp_path) is True
    assert process_identity_matches(30, 2999, expected_ppid=10, proc_root=tmp_path) is False
    assert process_identity_matches(30, 3000, expected_ppid=11, proc_root=tmp_path) is False


def test_process_cgroup_reads_unified_v2_path(tmp_path):
    proc = tmp_path / "40"
    proc.mkdir()
    (proc / "cgroup").write_text(
        "1:name=systemd:/legacy\n"
        "0::/user.slice/user-1000.slice/app.slice/job.scope\n",
        encoding="utf-8",
    )

    assert process_cgroup(40, proc_root=tmp_path) == (
        "/user.slice/user-1000.slice/app.slice/job.scope"
    )


def test_cgroup_pids_reads_only_exact_valid_path(tmp_path):
    group = tmp_path / "user.slice" / "job.scope"
    group.mkdir(parents=True)
    (group / "cgroup.procs").write_text("42\n41\n42\ninvalid\n", encoding="utf-8")

    assert cgroup_pids("/user.slice/job.scope", cgroup_root=tmp_path) == [41, 42]
    assert cgroup_pids("../job.scope", cgroup_root=tmp_path) == []
