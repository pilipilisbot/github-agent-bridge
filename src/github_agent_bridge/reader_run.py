from __future__ import annotations

import os
import sys

from .cli import DEFAULT_DB, DEFAULT_POLICY, main as cli_main
from .observability import configure_sentry
from .reader import imap_mailbox_arg


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def main() -> int:
    """Run one IMAP reader pass from GITHUB_AGENT_BRIDGE_* environment.

    This small wrapper keeps the systemd unit simple, especially for the
    optional --mark-seen flag, which should be omitted completely in shadow
    deployments.
    """
    configure_sentry(service="reader")
    missing = [name for name in ("GITHUB_AGENT_BRIDGE_EMAIL", "GITHUB_AGENT_BRIDGE_PASSWORD") if not env(name)]
    if missing:
        print(f"missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    argv = [
        "--db",
        env("GITHUB_AGENT_BRIDGE_DB", DEFAULT_DB),
        "--policy",
        env("GITHUB_AGENT_BRIDGE_POLICY", DEFAULT_POLICY),
        "read-imap-once",
        "--imap-host",
        env("GITHUB_AGENT_BRIDGE_IMAP_HOST", "imap.gmail.com"),
        "--imap-port",
        env("GITHUB_AGENT_BRIDGE_IMAP_PORT", "993"),
        "--email",
        env("GITHUB_AGENT_BRIDGE_EMAIL"),
        "--password",
        env("GITHUB_AGENT_BRIDGE_PASSWORD"),
        "--mailbox",
        imap_mailbox_arg(env("GITHUB_AGENT_BRIDGE_MAILBOX", "INBOX")),
    ]
    if env("GITHUB_AGENT_BRIDGE_MARK_SEEN") in {"1", "true", "TRUE", "yes", "YES", "--mark-seen"}:
        argv.append("--mark-seen")
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
