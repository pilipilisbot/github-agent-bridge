from __future__ import annotations

from types import SimpleNamespace

from github_agent_bridge import observability
from github_agent_bridge.observability import configure_sentry


def reset_sentry_state(monkeypatch):
    monkeypatch.setattr(observability, "_SENTRY_INITIALIZED", False)
    monkeypatch.setattr(observability, "_SENTRY_LAST_RESULT", None)


def test_configure_sentry_noops_without_dsn(monkeypatch):
    reset_sentry_state(monkeypatch)

    result = configure_sentry(service="executor", env={})

    assert result == {"enabled": False, "reason": "missing_dsn"}


def test_configure_sentry_does_not_require_sdk(monkeypatch):
    reset_sentry_state(monkeypatch)

    def missing_sdk(name: str):
        if name == "sentry_sdk":
            raise ImportError("missing")
        raise AssertionError(name)

    monkeypatch.setattr(observability.importlib, "import_module", missing_sdk)

    result = configure_sentry(service="executor", env={"GITHUB_AGENT_BRIDGE_SENTRY_DSN": "https://dsn.example/1"})

    assert result == {"enabled": False, "reason": "sentry_sdk_missing"}


def test_configure_sentry_initializes_sdk_with_bridge_env(monkeypatch):
    reset_sentry_state(monkeypatch)
    calls: dict[str, object] = {"tags": []}

    fake_sdk = SimpleNamespace(
        init=lambda **kwargs: calls.update({"init": kwargs}),
        set_tag=lambda key, value: calls["tags"].append((key, value)),
    )
    monkeypatch.setattr(observability.importlib, "import_module", lambda name: fake_sdk)

    result = configure_sentry(
        service="dashboard",
        env={
            "GITHUB_AGENT_BRIDGE_SENTRY_DSN": "https://dsn.example/1",
            "GITHUB_AGENT_BRIDGE_SENTRY_ENVIRONMENT": "production",
            "GITHUB_AGENT_BRIDGE_SENTRY_RELEASE": "github-agent-bridge@9.9.9",
            "GITHUB_AGENT_BRIDGE_SENTRY_TRACES_SAMPLE_RATE": "0.25",
            "GITHUB_AGENT_BRIDGE_SENTRY_PROFILES_SAMPLE_RATE": "0.5",
        },
    )

    assert result == {
        "enabled": True,
        "service": "dashboard",
        "release": "github-agent-bridge@9.9.9",
        "environment": "production",
    }
    assert calls["init"] == {
        "dsn": "https://dsn.example/1",
        "release": "github-agent-bridge@9.9.9",
        "environment": "production",
        "send_default_pii": False,
        "traces_sample_rate": 0.25,
        "profiles_sample_rate": 0.5,
    }
    assert calls["tags"] == [("service", "dashboard"), ("component", "github-agent-bridge")]


def test_configure_sentry_ignores_invalid_sample_rates(monkeypatch):
    reset_sentry_state(monkeypatch)
    calls: dict[str, object] = {}
    fake_sdk = SimpleNamespace(init=lambda **kwargs: calls.update({"init": kwargs}), set_tag=lambda *_: None)
    monkeypatch.setattr(observability.importlib, "import_module", lambda name: fake_sdk)

    configure_sentry(
        service="executor",
        env={
            "GITHUB_AGENT_BRIDGE_SENTRY_DSN": "https://dsn.example/1",
            "GITHUB_AGENT_BRIDGE_SENTRY_TRACES_SAMPLE_RATE": "never",
            "GITHUB_AGENT_BRIDGE_SENTRY_PROFILES_SAMPLE_RATE": "2",
        },
    )

    assert "traces_sample_rate" not in calls["init"]
    assert "profiles_sample_rate" not in calls["init"]
