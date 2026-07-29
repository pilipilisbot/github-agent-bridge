from __future__ import annotations

from pathlib import Path

from github_agent_bridge import monitor_alert


def make_config(tmp_path: Path) -> monitor_alert.AlertConfig:
    return monitor_alert.AlertConfig(
        bridge_bin="/tmp/github-agent-bridge",
        openclaw_bin="/tmp/openclaw",
        db="/tmp/bridge.sqlite3",
        policy="/tmp/policy.json",
        channel="telegram",
        target="43532269",
        state_dir=tmp_path,
        resend_seconds=900,
        auto_unlock_seconds=900,
        pending_warn_seconds=300,
        review_running_warn_seconds=600,
        work_running_warn_seconds=900,
        progress_warn_seconds=600,
        kill_stale_children=False,
        terminate_grace_seconds=1,
        proc_idle_seconds=240,
    )


def test_should_send_alert_persists_and_throttles(tmp_path):
    state_file = tmp_path / "monitor-alert.state"

    assert monitor_alert.should_send_alert(state_file, "boom", resend_seconds=900, now=1000) is True
    assert monitor_alert.should_send_alert(state_file, "boom", resend_seconds=900, now=1500) is False
    assert monitor_alert.should_send_alert(state_file, "boom", resend_seconds=900, now=2001) is True


def test_load_state_accepts_legacy_shell_format(tmp_path):
    state_file = tmp_path / "monitor-alert.state"
    state_file.write_text("LAST_HASH='abc'\nLAST_TS=123\n", encoding="utf-8")

    assert monitor_alert.load_state(state_file) == ("abc", 123)


def test_maybe_unlock_stale_blocks_when_executor_has_no_children(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: False)

    def fake_run(args, check=False):
        calls.append(args)
        return type("Proc", (), {"stdout": '{"blocked":[7],"count":1}\n'})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)

    output = monitor_alert.maybe_unlock_stale(config, "running job 7 owner/repo#1 age 1200s > 900s")

    assert output == '{"blocked":[7],"count":1}\n'
    assert "block-running" in calls[0]
    assert calls[0][-2:] == ["--job-id", "7"]


def test_maybe_unlock_stale_blocks_no_child_running_detail_ids(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: False)

    def fake_run(args, check=False):
        calls.append(args)
        return type("Proc", (), {"stdout": '{"blocked":[568,570],"count":2}' + "\n"})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)

    output = monitor_alert.maybe_unlock_stale(
        config,
        "\n".join(
            [
                "running jobs exist but executor has no child process",
                "- running detail: job=568 key=owner/repo#1 age=1200s",
                "- running detail: job=570 key=owner/repo#2 age=1200s",
            ]
        ),
    )

    assert output == '{"blocked":[568,570],"count":2}\n'
    assert "block-running" in calls[0]
    assert calls[0][-4:] == ["--job-id", "568", "--job-id", "570"]


def test_maybe_unlock_stale_uses_no_child_alert_code_for_detail_ids(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: False)

    def fake_run(args, check=False):
        calls.append(args)
        return type("Proc", (), {"stdout": '{"blocked":[568],"count":1}\n'})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)

    output = monitor_alert.maybe_unlock_stale(
        config,
        "\n".join(
            [
                "- [monitor.running_no_executor_child] executor has no available worker child",
                "- running detail: job=568 key=owner/repo#1 age=1200s",
            ]
        ),
    )

    assert output == '{"blocked":[568],"count":1}\n'
    assert "block-running" in calls[0]
    assert calls[0][-2:] == ["--job-id", "568"]


def test_maybe_unlock_stale_kills_children_and_blocks_jobs_when_enabled(tmp_path, monkeypatch):
    base = make_config(tmp_path)
    config = monitor_alert.AlertConfig(
        bridge_bin=base.bridge_bin,
        openclaw_bin=base.openclaw_bin,
        db=base.db,
        policy=base.policy,
        channel=base.channel,
        target=base.target,
        state_dir=base.state_dir,
        resend_seconds=base.resend_seconds,
        auto_unlock_seconds=base.auto_unlock_seconds,
        pending_warn_seconds=base.pending_warn_seconds,
        review_running_warn_seconds=base.review_running_warn_seconds,
        work_running_warn_seconds=base.work_running_warn_seconds,
        progress_warn_seconds=base.progress_warn_seconds,
        kill_stale_children=True,
        terminate_grace_seconds=base.terminate_grace_seconds,
        proc_idle_seconds=base.proc_idle_seconds,
    )
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: True)
    monkeypatch.setattr(monitor_alert, "child_pids", lambda pid: [456])
    monkeypatch.setattr(monitor_alert, "sample_executor_activity", lambda config, main_pid=None, now=None: "proc sample\n")
    monkeypatch.setattr(monitor_alert, "load_proc_state", lambda path: {"active_since_last_sample": False, "idle_seconds": 300})
    monkeypatch.setattr(monitor_alert, "terminate_process_group", lambda pid, grace: f"pid {pid}: killed")

    def fake_run(args, check=False):
        return type("Proc", (), {"stdout": "{\"blocked\":[7],\"count\":1}\n"})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)

    output = monitor_alert.maybe_unlock_stale(config, "running job 7 owner/repo#1 age 1200s > 900s")

    assert "pid 456: killed" in output
    assert '{"blocked":[7],"count":1}' in output
    assert "requeued" not in output


def test_maybe_unlock_stale_blocks_jobs_when_executor_is_inactive(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "0")

    def fake_run(args, check=False):
        calls.append(args)
        return type("Proc", (), {"stdout": '{"blocked":[7],"count":1}\n'})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)

    output = monitor_alert.maybe_unlock_stale(config, "running job 7 owner/repo#1 age 1200s > 900s")

    assert output == '{"blocked":[7],"count":1}\n'
    assert "block-running" in calls[0]
    assert "unlock-stale" not in calls[0]


def test_process_mismatch_restarts_complete_executor_cgroup_and_blocks_without_age_gate(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    calls = []
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")

    def fake_run(args, check=False):
        calls.append(args)
        if "restart" in args:
            return type("Proc", (), {"stdout": "", "returncode": 0})()
        return type("Proc", (), {"stdout": '{"blocked":[7],"count":1}\n', "returncode": 0})()

    monkeypatch.setattr(monitor_alert, "_run", fake_run)
    output = monitor_alert.maybe_unlock_stale(
        config,
        "\n".join(
            [
                "- [monitor.running_process_mismatch] running job 7 process ownership mismatch: PID 456 is dead",
                "- running detail: job=7 key=owner/repo#1",
            ]
        ),
    )

    assert calls[0] == ["systemctl", "--user", "restart", "github-agent-bridge.service"]
    assert "block-running" in calls[1]
    assert calls[1][calls[1].index("--older-than") + 1] == "0"
    assert "executor cgroup restart rc=0" in output
    assert '"blocked":[7]' in output


def test_sample_executor_activity_tracks_all_executor_children(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: True)
    monkeypatch.setattr(monitor_alert, "child_pids", lambda pid: [456, 789] if str(pid) == "123" else [])
    monkeypatch.setattr(monitor_alert, "process_exists", lambda pid: True)
    monkeypatch.setattr(monitor_alert, "proc_cmd", lambda pid: f"cmd {pid}")
    monkeypatch.setattr(monitor_alert, "proc_cpu_ticks", lambda pid: {456: 10, 789: 20}[pid])
    monkeypatch.setattr(monitor_alert, "proc_io_bytes", lambda pid: {456: 100, 789: 200}[pid])

    output = monitor_alert.sample_executor_activity(config, main_pid="123", now=1000)

    state = monitor_alert.load_proc_state(config.proc_state_file)
    assert output == "proc sample: root_pids=456,789 active=True cpu_ticks=30 io_bytes=300\n"
    assert state["root_pids"] == [456, 789]
    assert state["pids"] == [456, 789]


def test_process_sample_active_detects_second_child_activity():
    previous = {
        "root_pids": [456, 789],
        "pids": [456, 789],
        "cpu_ticks": 30,
        "io_bytes": 300,
    }
    current = {
        "root_pids": [456, 789],
        "pids": [456, 789],
        "cpu_ticks": 31,
        "io_bytes": 300,
    }

    assert monitor_alert.process_sample_active(previous, current) is True


def test_process_sample_active_accepts_legacy_single_root_state():
    previous = {
        "root_pid": 456,
        "pids": [456],
        "cpu_ticks": 30,
        "io_bytes": 300,
    }
    current = {
        "root_pids": [456],
        "pids": [456],
        "cpu_ticks": 30,
        "io_bytes": 300,
    }

    assert monitor_alert.process_sample_active(previous, current) is False


def test_maybe_unlock_stale_does_not_kill_active_child(tmp_path, monkeypatch):
    base = make_config(tmp_path)
    config = monitor_alert.AlertConfig(
        bridge_bin=base.bridge_bin,
        openclaw_bin=base.openclaw_bin,
        db=base.db,
        policy=base.policy,
        channel=base.channel,
        target=base.target,
        state_dir=base.state_dir,
        resend_seconds=base.resend_seconds,
        auto_unlock_seconds=base.auto_unlock_seconds,
        pending_warn_seconds=base.pending_warn_seconds,
        review_running_warn_seconds=base.review_running_warn_seconds,
        work_running_warn_seconds=base.work_running_warn_seconds,
        progress_warn_seconds=base.progress_warn_seconds,
        kill_stale_children=True,
        terminate_grace_seconds=base.terminate_grace_seconds,
        proc_idle_seconds=240,
    )
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: True)
    monkeypatch.setattr(monitor_alert, "sample_executor_activity", lambda config, main_pid=None, now=None: "proc sample\n")
    monkeypatch.setattr(monitor_alert, "load_proc_state", lambda path: {"active_since_last_sample": True, "idle_seconds": 0})

    output = monitor_alert.maybe_unlock_stale(config, "running job 7 owner/repo#1 age 1200s > 900s")

    assert output == "proc sample\n"


def test_maybe_unlock_stale_skips_child_kill_when_disabled(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(monitor_alert, "get_main_pid", lambda unit="github-agent-bridge.service": "123")
    monkeypatch.setattr(monitor_alert, "has_child_processes", lambda pid: True)

    output = monitor_alert.maybe_unlock_stale(config, "running job 7 owner/repo#1 age 1200s > 900s")

    assert output == ""


def test_maybe_unlock_stale_skips_without_running_job_message(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    called = False

    def fail(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unlock should not run")

    monkeypatch.setattr(monitor_alert, "_run", fail)

    output = monitor_alert.maybe_unlock_stale(config, "pending queue oldest age 999s > 300s")

    assert output == ""
    assert called is False


def test_maybe_unlock_stale_skips_running_detail_without_stalled_alert(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    called = False

    def fail(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unlock should not run")

    monkeypatch.setattr(monitor_alert, "_run", fail)

    output = monitor_alert.maybe_unlock_stale(config, "- running detail: job=7 key=owner/repo#1 age=1200s semantic=claimed/10s")

    assert output == ""
    assert called is False
