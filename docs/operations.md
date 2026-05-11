# Operations

Initial migration target from legacy worker:

- `~/.local/bin/pilipilis_inbox_worker.py`
- `~/.local/state/pilipilis/github-worklog.jsonl`
- `~/.local/state/pilipilis/github-active.json`
- `~/.local/state/pilipilis/inbox_state.json`

Operational SLOs:

- Oldest pending GitHub job age should stay below 2 minutes.
- A blocked dispatch must not block unrelated PRs/issues.
- No UID may be advanced before its notification is durably queued or handled.
- Review-only jobs should normally finish within 15 minutes (`--review-timeout 900`).
- Implementation jobs may run for up to 60 minutes (`--work-timeout 3600`) before being marked blocked.

Monitoring checks:

```bash
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 monitor
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 monitor --json
```

The monitor exits `0` when healthy and `2` when it detects alerts. It checks:

- executor service active;
- reader timer active;
- last reader service result;
- blocked jobs;
- pending jobs older than 300s;
- running jobs older than the review/work thresholds.

Suggested production split:

```bash
# Executor pool: long-running service
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  run --mode live --workers 4 --review-timeout 900 --work-timeout 3600

# Reader: short periodic job, safe to run via systemd timer
github-agent-bridge --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  read-imap-once --email "$GITHUB_AGENT_BRIDGE_EMAIL" --password "$GITHUB_AGENT_BRIDGE_PASSWORD" --mark-seen
```
