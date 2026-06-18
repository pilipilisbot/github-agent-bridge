---
name: github-agent-bridge
description: Operate and maintain the github-agent-bridge deployment: inspect queue health, reader/executor status, policy and routing, shadow/canary rollout, monitor alerts, retries, stale locks, autoupdate planning, feedback rules, and dashboard/systemd services. Use when working on bridge operations, production maintenance, rollout, troubleshooting, or safe release updates.
---

# GitHub Agent Bridge

Use this skill when operating `github-agent-bridge`, the GitHub notification to OpenClaw agent bridge.

## Operator Stance

- Prefer the short CLI name: `gab`.
- Default to safe inspection, `shadow`, `dry-run`, or `--no-systemd` commands unless the user explicitly asks for live production action.
- Do not pass `--mark-seen` while testing or shadowing IMAP; it mutates the mailbox.
- Keep secrets in the private environment file, never inline in commands or committed files.
- Treat `enabledRepos` as the live canary allowlist when it is present.
- Before unlocking or retrying jobs, inspect enough context to avoid duplicating active agent work.

## Runtime Paths

Typical deployment paths:

```bash
DB=~/.local/state/github-agent-bridge/bridge.sqlite3
POLICY=~/.config/github-agent-bridge/policy.json
ENV=~/.config/github-agent-bridge/env
```

Repository docs to load as needed:

- Install or reconfigure a deployment: `../../../docs/installation.md`
- Operate production, monitor, update, dashboard services: `../../../docs/operations.md`
- Configure trust, actions, routing, roles, and models: `../../../docs/policy-reference.md`
- Roll out safely through replay, shadow, dry-run, and canary: `../../../docs/shadow-canary.md`
- Diagnose known failures: `../../../docs/failure-modes.md`
- Understand architecture and invariants: `../../../docs/architecture.md`
- Develop or test bridge changes: `../../../docs/development.md`
- Release and changelog behavior: `../../../docs/releases.md`

## First Checks

Start with read-only state:

```bash
gab --db "$DB" status
gab --db "$DB" jobs --limit 20
gab --db "$DB" monitor --no-systemd
gab --db "$DB" monitor --json
```

For systemd deployments:

```bash
systemctl --user status github-agent-bridge.service
systemctl --user status github-agent-bridge-reader.timer
systemctl --user status github-agent-bridge-monitor.timer
journalctl --user -u github-agent-bridge.service -n 120 --no-pager
```

## Safe Validation

Use an isolated DB for local or canary checks:

```bash
gab --db /tmp/github-agent-bridge-shadow.sqlite3 init-db
gab --db /tmp/github-agent-bridge-shadow.sqlite3 --policy "$POLICY" run --mode shadow --once
gab --db /tmp/github-agent-bridge-shadow.sqlite3 jobs --limit 50
```

Read IMAP without mailbox mutation:

```bash
set -a
. "$ENV"
set +a

gab --db /tmp/github-agent-bridge-shadow.sqlite3 \
  --policy "$POLICY" \
  read-imap-once \
  --email "$GITHUB_AGENT_BRIDGE_EMAIL" \
  --password "$GITHUB_AGENT_BRIDGE_PASSWORD" \
  --mailbox "$GITHUB_AGENT_BRIDGE_MAILBOX"
```

Only add `--mark-seen` when the bridge should consume those GitHub notifications.

## Live Operation

Executor pool:

```bash
gab --db "$DB" --policy "$POLICY" \
  run --mode live --workers 4 --review-timeout 900 --work-timeout 3600
```

One-shot reader job:

```bash
gab --db "$DB" --policy "$POLICY" \
  read-imap-once \
  --email "$GITHUB_AGENT_BRIDGE_EMAIL" \
  --password "$GITHUB_AGENT_BRIDGE_PASSWORD" \
  --mailbox "$GITHUB_AGENT_BRIDGE_MAILBOX" \
  --mark-seen
```

The packaged `github-agent-bridge-reader-run` wrapper is preferred for systemd reader timers because it reads the private env file and handles IMAP mailbox quoting.

## Queue Recovery

Inspect before acting:

```bash
gab --db "$DB" jobs --status blocked --limit 20
gab --db "$DB" jobs --status running --limit 20
gab --db "$DB" monitor --json
```

Typical recovery commands:

```bash
gab --db "$DB" retry <job-id>
gab --db "$DB" dismiss <job-id>
gab --db "$DB" unlock-stale --older-than 3600
gab --db "$DB" unlock-stale --job-id <job-id>
```

Use `unlock-stale` only after confirming no live OpenClaw process is still working on that job.

## Updates

Plan before applying:

```bash
gab --db "$DB" update --repo-dir /path/to/github-agent-bridge --json
gab --db "$DB" update --repo-dir /path/to/github-agent-bridge --record --json
```

Apply only the safe immediate subset:

```bash
gab --db "$DB" update --repo-dir /path/to/github-agent-bridge --record --apply --json
```

Complete a deferred executor reload after the active queue drains:

```bash
gab --db "$DB" update --complete-pending --json
```

For non-standard services or install sources, prefer environment variables in the private env file or documented CLI flags over hardcoded command edits.

## Policy And Feedback

Validate policy shape before live runs:

```bash
python3 -m json.tool "$POLICY" >/dev/null
gab --db "$DB" --policy "$POLICY" monitor --no-systemd
```

Inspect learned feedback rules that affect agent prompts:

```bash
gab --db "$DB" feedback-rules
gab --db "$DB" rules pilipilisbot/github-agent-bridge
gab --db "$DB" feedback-events --limit 20
gab --db "$DB" feedback-proposals --limit 20
```

## Development Checks

When changing the bridge repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q
```

For CLI behavior changes, also exercise the relevant command against a temporary DB.
