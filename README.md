# GitHub Agent Bridge

Reusable bridge between GitHub notifications and OpenClaw agents.

It is designed to replace one-off inbox scripts with a durable, auditable pipeline:

```text
IMAP reader -> SQLite queue -> executor pool -> GitHub 👀 + OpenClaw agent dispatch
                         └── per-work_key lock: owner/repo#number
```

## What it solves

- The IMAP reader is fast and never waits for agent work.
- Jobs are persisted before mailbox high-water marks advance.
- Different issues/PRs can run in parallel.
- The same issue/PR cannot run concurrently (`work_key = owner/repo#number`).
- Duplicate notifications for an active thread are coalesced.
- Dispatch timeout/failure marks one job as `blocked` without blocking unrelated work.
- OpenClaw agent runs get explicit timeouts: 900s for review-only jobs and 3600s for implementation work by default.


## Scope boundary

This project is GitHub-only. Generic email triage, calendar/status emails and personal inbox logic should live in a separate generic inbox worker. The bridge must not mutate non-GitHub messages. See `docs/scope.md`.

## CLI

```bash
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 init-db
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 read-imap-once   --email "$EMAIL" --password "$APP_PASSWORD"
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 run --mode shadow --workers 4
# live executor, explicit long-running GitHub work timeout profile
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 run --mode live --workers 4 --review-timeout 900 --work-timeout 3600
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 status
```

## Policy

By default the bridge is conservative. Provide a JSON policy with trusted repos/orgs and routes:

```json
{
  "trustedOrgs": ["gisce"],
  "actions": {
    "auto": ["archive_notification", "sync_after_merge"],
    "trustedAuto": ["reply_comment", "open_issue"],
    "ask": ["reply_comment", "open_issue"]
  },
  "orgRoutes": {
    "gisce": {"agent": "gisce-developer", "channel": "telegram", "to": "-1003972920100"}
  }
}
```

## Safe rollout

Use `replay`, `read-imap-once` without `--mark-seen`, and `run --mode shadow` before enabling `run --mode live`. See `docs/shadow-canary.md`.

## Current status

This is an implementation scaffold with reusable components and tests. It does not yet replace the production legacy worker automatically. Use the systemd units under `systemd/` for shadow/canary deployment.
