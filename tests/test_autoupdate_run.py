from github_agent_bridge import autoupdate_run


def test_autoupdate_run_uses_env_and_complete_pending(monkeypatch):
    calls = []
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_DB", "/tmp/bridge.sqlite3")
    monkeypatch.setenv("GITHUB_AGENT_BRIDGE_SYSTEMCTL_BIN", "systemctl-test")
    monkeypatch.setattr(autoupdate_run, "configure_sentry", lambda **kwargs: calls.append(("sentry", kwargs)))
    monkeypatch.setattr(autoupdate_run, "cli_main", lambda argv: calls.append(("cli", argv)) or 0)

    assert autoupdate_run.main() == 0

    assert calls == [
        ("sentry", {"service": "autoupdate"}),
        (
            "cli",
            [
                "--db",
                "/tmp/bridge.sqlite3",
                "update",
                "--complete-pending",
                "--systemctl-bin",
                "systemctl-test",
                "--json",
            ],
        ),
    ]
