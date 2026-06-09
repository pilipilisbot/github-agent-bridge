from github_agent_bridge import cli
from github_agent_bridge.autoupdate import load_update_state
from github_agent_bridge.queue import JobQueue


def test_parser_program_uses_invoked_binary_name(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["/usr/local/bin/gab", "status"])
    parser = cli.build_parser()
    assert parser.prog == "gab"


def test_feedback_rules_cli_lists_rules(tmp_path, capsys):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    cli.main(["--db", str(db), "feedback-rules", "--scope", "repo:gisce/erp"])

    captured = capsys.readouterr()
    assert '"rules": []' in captured.out


def test_update_cli_can_record_pending_reload_state(tmp_path, capsys, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    def fake_plan(*args, **kwargs):
        return {
            "installed_version": "1.2.3",
            "installed_tag": "v1.2.3",
            "target": {"tag_name": "v1.2.4", "source": "github_release"},
            "decision": "stage_full_reload",
            "executor_reload_pending": False,
            "blocked_reason": "",
            "queue": {"active_counts": {"pending": 0, "running": 0, "waiting_approval": 0}, "active_total": 0},
            "classification": {"risk": "executor_or_queue", "migration_files": [], "risky_files": ["src/github_agent_bridge/executor.py"]},
            "warnings": [],
        }

    monkeypatch.setattr("github_agent_bridge.cli.plan_update", fake_plan)

    cli.main(["--db", str(db), "update", "--record", "--json"])

    captured = capsys.readouterr()
    assert '"decision": "stage_full_reload"' in captured.out
    assert load_update_state(JobQueue(db))["target"]["tag_name"] == "v1.2.4"


def test_update_cli_apply_runs_configured_install_command(tmp_path, capsys, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    calls = []

    def fake_plan(*args, **kwargs):
        return {
            "installed_version": "1.2.3",
            "installed_tag": "v1.2.3",
            "target": {"tag_name": "v1.2.4", "source": "explicit_target"},
            "up_to_date": False,
            "decision": "stage_dashboard_reload",
            "executor_reload_pending": False,
            "blocked_reason": "",
            "queue": {"active_counts": {"pending": 0, "running": 1, "waiting_approval": 0}, "active_total": 1},
            "classification": {"risk": "dashboard_only", "migration_files": [], "risky_files": []},
            "service_plan": {
                "immediate": [
                    {"command": "try-restart", "unit": "dashboard.service", "reason": "dashboard-only update can reload independently"}
                ]
            },
            "warnings": [],
        }

    def fake_apply(plan, **kwargs):
        calls.append(kwargs)
        return {"applied": True, "blocked": [], "commands": [{"kind": "install", "command": kwargs["install_command"]}]}

    monkeypatch.setattr("github_agent_bridge.cli.plan_update", fake_plan)
    monkeypatch.setattr("github_agent_bridge.cli.apply_update_plan", fake_apply)

    assert cli.main([
        "--db",
        str(db),
        "update",
        "--apply",
        "--install-command",
        "python -m pip install pkg",
        "--json",
    ]) == 0

    captured = capsys.readouterr()
    assert '"execution": {' in captured.out
    assert calls[0]["install_command"] == ["python", "-m", "pip", "install", "pkg"]


def test_update_cli_complete_pending_uses_recorded_state(tmp_path, capsys, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    calls = []

    def fake_complete(db_path, **kwargs):
        calls.append((db_path, kwargs))
        return {"completed": True, "blocked": [], "commands": [{"kind": "systemd"}]}

    monkeypatch.setattr("github_agent_bridge.cli.complete_pending_reload", fake_complete)

    assert cli.main([
        "--db",
        str(db),
        "update",
        "--complete-pending",
        "--systemctl-bin",
        "systemctl-test",
        "--json",
    ]) == 0

    captured = capsys.readouterr()
    assert '"completion": {' in captured.out
    assert calls == [(str(db), {"systemctl_bin": "systemctl-test"})]


def test_feedback_rule_add_cli_creates_rule(tmp_path, capsys):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    cli.main([
        "--db",
        str(db),
        "feedback-rule-add",
        "--scope",
        "repo:gisce/erp",
        "--type",
        "style_preference",
        "--rule",
        "Answer with concrete evidence.",
        "--confidence",
        "0.8",
    ])

    captured = capsys.readouterr()
    assert '"rule": {' in captured.out
    assert "Answer with concrete evidence." in captured.out


def test_feedback_rule_add_cli_reacts_to_source_events(tmp_path, capsys, monkeypatch):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)
    reacted = []
    monkeypatch.setattr(cli.feedback, "react_to_feedback_event", lambda *args, **kwargs: reacted.append((args, kwargs)) or True)

    cli.main([
        "--db",
        str(db),
        "feedback-rule-add",
        "--scope",
        "repo:gisce/erp",
        "--type",
        "style_preference",
        "--rule",
        "Answer with concrete evidence.",
        "--confidence",
        "0.8",
        "--source-event",
        "event-1",
        "--gh-bin",
        "gh-test",
    ])

    captured = capsys.readouterr()
    assert '"reacted": 1' in captured.out
    assert reacted == [((str(db), "event-1"), {"gh_bin": "gh-test"})]


def test_feedback_events_cli_lists_events(tmp_path, capsys):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    cli.main(["--db", str(db), "feedback-events", "--scope", "repo:gisce/erp"])

    captured = capsys.readouterr()
    assert '"events": []' in captured.out


def test_feedback_learn_cli_uses_policy_and_lists_result(tmp_path, capsys, monkeypatch):
    db = tmp_path / "q.sqlite3"
    policy = tmp_path / "policy.json"
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "feedback_classifier.md").write_text("custom classifier {event_json}\n")
    JobQueue(db)
    policy.write_text('{"feedbackLearning": {"enabled": true, "maxEventsPerRun": 2, "autoApproveConfidence": 0.85}, "promptOverrides": {"rules": {"feedback_classifier": "prompts/feedback_classifier.md"}}}')

    def fake_learn(**kwargs):
        assert kwargs["limit"] == 2
        assert kwargs["auto_approve_confidence"] == 0.85
        assert kwargs["prompt_template"] == "custom classifier {event_json}\n"
        return {"processed": 0, "approved": 0, "proposed": 0, "rejected": 0, "errors": 0, "proposals": []}

    monkeypatch.setattr("github_agent_bridge.feedback.learn_from_events", lambda *args, **kwargs: fake_learn(**kwargs))

    cli.main(["--db", str(db), "--policy", str(policy), "feedback-learn"])

    captured = capsys.readouterr()
    assert '"processed": 0' in captured.out


def test_feedback_proposals_cli_lists_proposals(tmp_path, capsys):
    db = tmp_path / "q.sqlite3"
    JobQueue(db)

    cli.main(["--db", str(db), "feedback-proposals"])

    captured = capsys.readouterr()
    assert '"proposals": []' in captured.out
