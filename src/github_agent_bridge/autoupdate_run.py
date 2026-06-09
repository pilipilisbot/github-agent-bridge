from __future__ import annotations

import os

from .cli import DEFAULT_DB, main as cli_main
from .observability import configure_sentry


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def main() -> int:
    """Run one pending autoupdate completion pass from environment settings."""
    configure_sentry(service="autoupdate")
    return cli_main(
        [
            "--db",
            env("GITHUB_AGENT_BRIDGE_DB", DEFAULT_DB),
            "update",
            "--complete-pending",
            "--systemctl-bin",
            env("GITHUB_AGENT_BRIDGE_SYSTEMCTL_BIN", "systemctl"),
            "--json",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
