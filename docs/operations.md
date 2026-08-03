# Operations

This guide is for running and monitoring the bridge.

> **Operator rule:** prefer `gab`. The long `github-agent-bridge` entrypoint is kept only for backwards compatibility.

## Runtime layout

| Item | Typical path |
| --- | --- |
| Database | `~/.local/state/github-agent-bridge/bridge.sqlite3` |
| Policy | `~/.config/github-agent-bridge/policy.json` |
| Environment | `systemd/env.example` copied to a private env file |
| Units | `systemd/*.service`, `systemd/*.timer` |
| Reader wrapper | packaged `github-agent-bridge-reader-run` console script |
| Autoupdate wrapper | packaged `github-agent-bridge-autoupdate-run` console script |

## Production commands

The reader systemd timer uses `github-agent-bridge-reader-run`, a small packaged
wrapper around `gab read-imap-once` that reads `GITHUB_AGENT_BRIDGE_*`
environment variables, quotes Gmail mailbox names with spaces for IMAP, and
conditionally adds `--mark-seen`.


Executor pool:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  run --mode live --workers 4 --review-timeout 900 --work-timeout 3600
```

Intent-specific executor pool:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  run --mode live --workers 2 --work-intent review_only --review-timeout 900
```

Use `--work-intent` when an operator wants separate worker capacity for cheap
review/comment follow-ups and heavier implementation jobs. Allowed values are
`review_only`, `work_allowed`, and `all`; the flag may be repeated or
comma-separated. Omitting it or passing `all` keeps the default all-intents
executor behavior.

A common production topology is:

| Pool | Example command | Purpose |
| --- | --- | --- |
| General | `gab ... run --mode live --workers 2` | Claims any pending job. Keeps the system simple when the queue is small. |
| Review-only | `gab ... run --mode live --workers 2 --work-intent review_only` | Keeps replies, reviews, and analysis moving even when implementation work is backed up. |
| Work-only | `gab ... run --mode live --workers 1 --work-intent work_allowed` | Caps concurrent state-changing work so long-running edits do not consume all executor slots. |

Intent filters apply only while claiming pending jobs. The queue still enforces
the per-`work_key` guard, so two pools cannot run two jobs for the same
issue/PR/thread concurrently. If every running executor is filtered to
`review_only`, `work_allowed` jobs remain pending until a general or work-only
executor is available; `gab monitor` will report old pending jobs normally.

For systemd installs, set `GITHUB_AGENT_BRIDGE_WORK_INTENT` in the private env
file read by the service:

```bash
# ~/.config/github-agent-bridge/env
GITHUB_AGENT_BRIDGE_MODE=live
GITHUB_AGENT_BRIDGE_WORKERS=2
GITHUB_AGENT_BRIDGE_WORK_INTENT=review_only
```

Reader timer job:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  read-imap-once \
  --email "$GITHUB_AGENT_BRIDGE_EMAIL" \
  --password "$GITHUB_AGENT_BRIDGE_PASSWORD" \
  --mailbox "$GITHUB_AGENT_BRIDGE_MAILBOX" \
  --mark-seen
```

## Health checks

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 monitor
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 monitor --json
```

The monitor exits:

| Exit code | Meaning |
| --- | --- |
| `0` | healthy |
| `2` | alert detected |

It checks:

- executor service active;
- reader timer active;
- last reader service result;
- blocked jobs;
- pending jobs older than 300 seconds;
- running jobs older than review/work thresholds.

By default the monitor command also writes bounded observability rows to the
bridge database:

- `process_samples`: recent executor child process trees, total CPU ticks, total
  I/O bytes, running job ids and whether the sample changed since the previous
  monitor run;
- `job_progress`: compact per-job semantic and visible progress heartbeats,
  separate from the longer audit/worklog timeline;
- `alerts`: active and resolved monitor alert observations with first/last seen
  timestamps and observation counts.

Process sample retention defaults to 24 hours. Override it with
`GITHUB_AGENT_BRIDGE_PROCESS_SAMPLE_RETENTION_SECONDS` or:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  monitor --process-sample-retention-seconds 21600
```

Use `--no-persist-observability` for ad hoc monitor runs that should not write
observability records.

## Sentry error reporting

Sentry is optional and disabled by default. Install the extra in the same
virtualenv used by the services:

```bash
python -m pip install 'github-agent-bridge[sentry]'
```

Then set the DSN in the private systemd environment file:

```text
GITHUB_AGENT_BRIDGE_SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
GITHUB_AGENT_BRIDGE_SENTRY_ENVIRONMENT=production
GITHUB_AGENT_BRIDGE_SENTRY_RELEASE=
GITHUB_AGENT_BRIDGE_SENTRY_TRACES_SAMPLE_RATE=
GITHUB_AGENT_BRIDGE_SENTRY_PROFILES_SAMPLE_RATE=
```

`SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`,
`SENTRY_TRACES_SAMPLE_RATE`, and `SENTRY_PROFILES_SAMPLE_RATE` are also
honored for compatibility, but prefer the `GITHUB_AGENT_BRIDGE_*` names in this
service's env file. If a DSN is set without `sentry-sdk` installed, the bridge
continues running without external error reporting.

## Safe update planning

Use `gab update` to inspect a published release and decide which reloads are
safe before touching services:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  update --repo-dir /path/to/github-agent-bridge --json
```

The command reads the latest GitHub release, compares it with the installed
package version tag, classifies changed files, and checks active queue statuses
(`pending`, `running`, and `waiting_approval`). Add `--record` to persist the
decision in the bridge `state` table so `/api/status` can show an outstanding
executor reload or migration blocker:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  update --repo-dir /path/to/github-agent-bridge --record --json
```

The planner is deliberately conservative. Dashboard-only changes can be staged
while executor jobs are active, executor/shared changes set a pending reload
when the queue is busy, and SQLite schema changes are deferred while active jobs
exist.

The JSON output also includes a `service_plan` for user-level systemd. It names
the executor, dashboard, reader, monitor, and feedback units, shows whether a
`systemctl --user daemon-reload` is needed because `systemd/` files changed,
and splits service actions into `immediate` and `deferred` lists:

- dashboard-only release: restart only the dashboard service;
- executor/shared release with active jobs: refresh the dashboard now and defer
  the executor restart until the active queue drains;
- migration release with active jobs: defer all reload work until the migration
  window;
- quiet queue: restart the dashboard and executor after package installation.

Override non-standard unit names with CLI flags or environment variables:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  update --repo-dir /path/to/github-agent-bridge --record --json \
  --executor-unit custom-bridge.service \
  --dashboard-unit custom-bridge-dashboard.service
```

The private env file can also set `GITHUB_AGENT_BRIDGE_AUTOUPDATE_REPO`,
`GITHUB_AGENT_BRIDGE_AUTOUPDATE_REPO_DIR`,
`GITHUB_AGENT_BRIDGE_EXECUTOR_UNIT`,
`GITHUB_AGENT_BRIDGE_DASHBOARD_UNIT`,
`GITHUB_AGENT_BRIDGE_READER_TIMER_UNIT`,
`GITHUB_AGENT_BRIDGE_MONITOR_TIMER_UNIT`, and
`GITHUB_AGENT_BRIDGE_FEEDBACK_TIMER_UNIT`.

To apply the immediate safe subset in one operator command, add `--apply`.
This installs the target release with pip and runs only the `service_plan`
`immediate` systemd actions. Deferred actions, such as an executor restart while
jobs are active, remain recorded for a later quiet window:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  update --repo-dir /path/to/github-agent-bridge --record --apply --json
```

The default install command is:

```bash
python -m pip install git+https://github.com/pilipilisbot/github-agent-bridge.git@<target-tag>
```

Set `GITHUB_AGENT_BRIDGE_AUTOUPDATE_INSTALL_COMMAND` or pass
`--install-command` when the deployment needs a local wheelhouse, a different
Python executable, or another package source. Use `--skip-install` or
`--skip-systemd-actions` for a partial operator-controlled run.

For releases that include SQLite schema/migration changes, `--apply` stays
conservative. If the active queue is not quiet, it refuses before installing and
records `active_jobs_block_migration`. If the queue is quiet, it backs up the
SQLite database first, installs the target package, runs the packaged schema
initialization from a fresh Python subprocess, restarts the safe immediate
systemd units, and then runs post-checks for installed version, queue state, and
restarted services. Set `GITHUB_AGENT_BRIDGE_AUTOUPDATE_BACKUP_DIR` or pass
`--backup-dir` to choose where the pre-migration SQLite backups are written.
Migration or post-check failures are recorded in autoupdate state with
`degraded=true`, the backup path, command output, and the blocker that needs
operator recovery.

Dashboard admins can run the same first-step workflow from the autoupdate notice:
`Check now` refreshes and records the plan, `Apply update` runs the immediate
safe subset, and `Complete reload` runs the recorded deferred reload once the
queue is quiet. These buttons are admin-only; migration releases require a quiet
queue before `Apply update` will install or restart anything. A refresh-only
check does not arm the deferred executor completion action; the dashboard only
enables completion after an update has been applied/staged.

When a previous `--record --apply` run left `executor_reload_pending=true`,
rerun the recorded deferred actions after the active queue drains:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  update --complete-pending --json
```

This command reads the persisted autoupdate state instead of checking GitHub or
installing the package again. It exits without touching services while
`pending`, `running`, or `waiting_approval` jobs exist; once the queue is quiet
it runs the deferred systemd actions and clears the pending reload marker.
Migration-blocked updates should be retried with `--apply` or the dashboard
`Apply update` action after the queue drains so the backup, schema application,
restart, and post-check sequence can run in one quiet window.

Install and enable `github-agent-bridge-autoupdate.timer` to retry that
completion pass automatically. The timer only calls
`update --complete-pending`; it does not check GitHub, install a package, or
restart the executor while active jobs remain in the queue:

```bash
systemctl --user enable --now github-agent-bridge-autoupdate.timer
```

Running-job age is not treated as a failure signal by itself. The monitor uses
the latest semantic heartbeat, visible OpenClaw output, and persisted
CPU/I/O/PID-tree activity to decide whether an old running job looks stalled.
Use `--progress-warn-seconds` to tune how long a running job can go without a
semantic or visible progress update before the monitor considers it quiet.
The alert wrapper uses the same composite stalled-job alert before automatic
unlock or child termination. It does not unlock every old running job; it passes
only the job ids that the monitor flagged as stalled.

Each live dispatch also records its executor generation, worker id, root PID,
parent PID, process group/session ids, and Linux process start time in the job
metadata. The start time prevents PID reuse from making a dead job look alive.
When the monitor finds a running job whose registered root process is dead,
reparented, reused, or owned by another executor generation, it restarts the
complete executor systemd cgroup and leaves the affected work blocked. This
restart is deliberately broader than `killpg`: tools may create new process
groups or sessions, but they remain in the service cgroup and are therefore
terminated together. `KillMode=control-group` and `TimeoutStopSec=30s` in the
executor unit make that cleanup explicit.

Set `GITHUB_AGENT_BRIDGE_KILL_STALE_CHILDREN=1` in the private systemd env file
to let `github-agent-bridge-monitor-alert` terminate stale executor child
process groups before retrying stalled jobs. The wrapper samples every direct
executor child and its descendants, then only terminates children when the whole
sample has been idle for `GITHUB_AGENT_BRIDGE_PROC_IDLE_SECONDS` seconds. It
sends `SIGTERM`, waits `GITHUB_AGENT_BRIDGE_TERMINATE_GRACE_SECONDS`, and then
uses `SIGKILL` if the child process group is still present. Keep this disabled
unless the bridge host is allowed to clean up stuck OpenClaw runs automatically.

## Dashboard API service

`github-agent-bridge-dashboard` is a separate FastAPI service for local
dashboards and operator tooling. It is intentionally not part of the executor
path: it does not import the dispatcher, does not claim jobs, does not call
OpenClaw, and opens the SQLite database read-only for job queries.

The service also serves the built React dashboard at `/`, a dedicated system
view at `/system`, and dedicated job views at `/jobs/{id}`. Operators can share
a job URL to open the dashboard with that job's session, worklog, activity feed
and GitHub links selected. The UI is a Vite + React + TypeScript app styled with
Tailwind and operational components, using TanStack Query for API state and
Recharts for percentile charts.
The process activity API and dashboard distinguish live executor process state,
persisted process activity, semantic job progress, and visible transcript/output
progress so operators can tell whether a running job is merely alive or actually
making useful progress.
The System page lists configured user-level systemd units and lets operators
expand a unit row to follow its live journal tail in place.
When the dashboard process receives a shutdown request, active SSE streams for
job sessions and journal tails are signaled to close promptly. This lets
dashboard-only autoupdates restart the FastAPI service without waiting for
long-lived browser streams to hit the systemd stop timeout.
Timestamps stay stored and returned by the API in UTC, while the browser renders
them in the viewer's local timezone from `Intl.DateTimeFormat`; hovering a
rendered timestamp shows the UTC value.
Production serves the static bundle from
`src/github_agent_bridge/dashboard_static`.
When VAPID keys are configured and the dashboard is exposed over HTTPS, signed-in
users can enable the header bell control. The executor sends final `done` and
`blocked` job notifications through those browser push subscriptions for the
triggering GitHub actor and coalesced human actors.

The API uses GitHub OAuth sessions by default. Configure these values in
`~/.config/github-agent-bridge/env`:

```text
GITHUB_AGENT_BRIDGE_DASHBOARD_SECRET_KEY=replace-with-random-secret
GITHUB_OAUTH_CLIENT_ID=replace-with-github-oauth-client-id
GITHUB_OAUTH_CLIENT_SECRET=replace-with-github-oauth-client-secret
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_USERS=alice,bob
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_ORGS=example-org
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_TEAMS=example-org/platform
GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS=alice
GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS=example-org/bridge-admins
GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL=https://bridge.example.com
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PUBLIC_KEY=replace-with-vapid-public-key
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PRIVATE_KEY=replace-with-vapid-private-key
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_CONTACT=mailto:admin@example.com
GITHUB_AGENT_BRIDGE_GITHUB_APP_ID=your-github-app-id
GITHUB_AGENT_BRIDGE_GITHUB_APP_SLUG=
GITHUB_AGENT_BRIDGE_WEB_PUSH_ICON_URL=
```

See [`dashboard-github-oauth.md`](dashboard-github-oauth.md) for the GitHub
OAuth App creation steps, callback URL, scopes, allowlists, reverse proxy notes
and troubleshooting.

Run it manually:

```bash
github-agent-bridge-dashboard \
  --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --host 127.0.0.1 \
  --port 8765
```

Frontend development:

```bash
cd dashboard
npm install
npm run dev
```

Build the packaged UI bundle:

```bash
cd dashboard
npm run build
```

Endpoints:

```text
GET /
GET /jobs/{id}
GET /api/health
GET /api/status
GET /api/jobs?status=pending&repo=pilipilisbot/github-agent-bridge&limit=20
GET /api/jobs/{id}
GET /api/jobs/{id}/logs
GET /api/jobs/{id}/session
GET /api/jobs/{id}/session/events
GET /api/jobs/{id}/session/stream
GET /api/metrics/summary
GET /api/processes
GET /api/systemd
GET /api/systemd/journal/stream?unit=github-agent-bridge.service
GET /api/alerts
GET /api/events/stream
```

Keep it bound to `127.0.0.1` unless you put an authenticated reverse proxy in
front of it. The packaged `systemd/github-agent-bridge-dashboard.service` starts
only this dashboard API; it does not restart or depend on
`github-agent-bridge.service`.

Current scope covers the read-only API, OAuth/session guard, job detail, logs,
summary metrics, an initial read-only React operations UI, a live `/proc`
snapshot of executor child processes plus persisted monitor sample history
through `GET /api/processes`, user-level systemd status for configured bridge
services and timers through `GET /api/systemd`, live `journalctl --user -f`
streams for those configured units through
`GET /api/systemd/journal/stream`, persistent monitor alert observations through
`GET /api/alerts`, the GitHub login that triggered a job when it can be derived
from the notification or GitHub API, and safe
OpenClaw session correlation through `GET /api/jobs/{id}/session`. New
dispatches use a deterministic `github-agent-bridge-job-{id}` OpenClaw session
id so operators can correlate a bridge job with the OpenClaw session that ran
it, and the dispatcher enables OpenClaw verbose mode for these sessions so tool
calls and command output are available to the live dashboard stream. The React route `/jobs/{id}` is a focused job detail page for sharing a
single job/session, with a link back to the generic dashboard. The dashboard
records bounded, redacted bridge-side session events when a job is claimed,
while OpenClaw stdout/stderr is emitted, dispatched and finished. The dashboard
records OpenClaw CLI output from flushed byte chunks rather than waiting for
newline-terminated lines, so partial interactive output can appear before the
OpenClaw process exits. The dashboard renders activity and transcript logs as
compact collapsible sections so long sessions can be scanned like GitHub Actions
or Copilot session output. Consecutive OpenClaw stdout/stderr chunks are grouped
into one row with a count and a one-line preview, and routine stdout groups stay
collapsed by default so plugin startup noise does not dominate the page.
Operators can read them with
`GET /api/jobs/{id}/session/events` or subscribe to
`GET /api/jobs/{id}/session/stream` for SSE updates. The stream carries new
session events and transcript entries directly, including already-recorded live
transcript entries when a browser opens the page after the job has started, with
heartbeat events and proxy buffering disabled for long-lived HTTPS connections. While a job is still
running, live redacted OpenClaw stdout/stderr is also exposed as transcript
entries. The dashboard also reads OpenClaw's live trajectory file
`github-agent-bridge-job-{id}.trajectory.jsonl`, so tool calls and tool results
can stream before OpenClaw writes the final session transcript file. The
dashboard also exposes redacted OpenClaw transcript entries for the correlated
session through `GET /api/jobs/{id}/session/transcript`. By default it looks up
`~/.openclaw/agents/github/sessions/sessions.json`, or the path set in
`GITHUB_AGENT_BRIDGE_OPENCLAW_SESSION_STORE`, and only returns entries for the
job's deterministic session id. Transcript text is secret-redacted and truncated
before it is returned to the authenticated dashboard. The process activity panel
uses persisted process samples for a compact CPU history line chart when monitor
samples exist, and falls back to the live executor snapshot otherwise.

When publishing the dashboard through nginx, disable buffering for the proxied
dashboard location so SSE events flush immediately:

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 1h;
}
```

## Operational SLOs

| Signal | Target |
| --- | --- |
| Oldest pending job | below 2 minutes |
| Review-only job | normally below 15 minutes |
| Implementation job | normally below 60 minutes |
| Blocked dispatch | must not block unrelated PRs/issues |
| Mailbox cursor | must not advance before durable queue/ignore |

## Common operator tasks

### Inspect queue status

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 status
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 jobs --limit 20
```

Jobs include `trigger_actor` and `trigger_actor_avatar_url` when the bridge can
identify the GitHub user that caused the notification. New GitHub notification
jobs first resolve the parsed issue, pull request, comment, review, commit
comment, or workflow run through the GitHub API and store that resource author.
If that lookup is unavailable, enqueue falls back to the notification sender
display name when GitHub provides a concrete login there. Existing jobs can be
backfilled from stored GitHub context:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  backfill-trigger-actors --dry-run
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  backfill-trigger-actors
```

### Detect install drift

Set `GITHUB_AGENT_BRIDGE_RELEASE_REPO` in the systemd environment file to the
repository that publishes bridge releases. `gab monitor` reports the installed
package version, fetches the latest published GitHub release, and alerts when a
newer release is available. The alert includes the release URL and the first
non-empty release-note line so operators can see what changed before updating:

```bash
GITHUB_AGENT_BRIDGE_RELEASE_REPO=pilipilisbot/github-agent-bridge \
  gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 monitor --json
```

If GitHub release lookup fails, the monitor records `latest_release_error` in
JSON output but does not fail the health check only because the network or
GitHub API is temporarily unavailable.

### Inspect feedback learning rules

Trusted actionable GitHub notifications are captured into the bridge database as
feedback candidates. Inspect candidates with:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  feedback-events --scope repo:owner/name --limit 20
```

Curated rules injected into agent prompts can be inspected with:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  feedback-rules --scope repo:owner/name --min-confidence 0.5
```

To inject the same curated rules into a non-bridge agent workflow, render them
as prompt text:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  rules --repo owner/name
```

The `rules` command uses the policy `feedbackLearning.minConfidence` threshold
unless `--min-confidence` is provided, and it includes matching `global`,
`org:owner`, and `repo:owner/name` rules.

Capture is controlled by `policy.json` `feedbackLearning.enabled`; the prompt
threshold comes from `feedbackLearning.minConfidence`.

Run an autonomous learning pass with:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  feedback-learn
```

The learning pass calls an LLM through OpenClaw, classifies unprocessed
`feedback_events`, and writes `feedback_rule_proposals`. High-confidence
reusable lessons are promoted automatically to `feedback_rules`; task-specific
comments are rejected and never reach agent prompts. It uses the dedicated
`feedbackLearning.sessionId` OpenClaw session and does not deliver chat output.
The classifier prompt defaults to packaged `prompt_rules/feedback_classifier.md`;
override it with `policy.json` `promptOverrides.rules.feedback_classifier`.

The learning pass model is independent from the model used by normal GitHub
work agents. Configure it in `policy.json`:

```json
{
  "feedbackLearning": {
    "model": "openai/gpt-5.4-mini",
    "thinking": "low",
    "sessionId": "github-agent-bridge-feedback"
  }
}
```

For one-off runs, CLI flags take precedence over policy:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --policy ~/.config/github-agent-bridge/policy.json \
  feedback-learn --model gpt-5.4-mini --thinking low
```

If no model is set by CLI or policy, OpenClaw uses its default model. The model
used for each classification is stored in `feedback_rule_proposals.model`.

Normal GitHub work agents use a separate `modelRoutes` policy section. Use it
to move low-risk actions to lighter models without changing implementation jobs:

```json
{
  "modelRoutes": {
    "byAction": {
      "sync_after_merge": {
        "model": "openai/gpt-5.4-mini",
        "thinking": "low"
      },
      "workflow_run_failed": {
        "model": "openai/gpt-5.4-mini",
        "thinking": "medium"
      }
    },
    "byIntent": {
      "review_only": {
        "model": "openai/gpt-5.4-mini",
        "thinking": "medium"
      }
    }
  }
}
```

The executor adds `--model` and `--thinking` only for the selected configured
route. Configured selections are recorded as `model_route_selected` job session
events so the dashboard can explain model choices after token-pressure or
compaction incidents.

For unattended operation, install and enable:

```bash
systemctl --user enable --now github-agent-bridge-feedback.timer
```

Manual rule insertion remains available for operator backfills:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  feedback-rule-add \
  --scope repo:owner/name \
  --type style_preference \
  --confidence 0.8 \
  --source-event github-agent-bridge-... \
  --rule 'Answer with concrete repository-specific evidence.'
```

### Retry a blocked job

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 retry <job-id>
```

Dashboard users listed in `GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS` or
`GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS` can also sign in and retry blocked,
denied, or waiting-approval jobs from the job detail page. The dashboard records
the admin login in the job worklog.

For `reply_comment` jobs, the executor checks GitHub before dispatching. If the
authenticated bot has already commented after the triggering issue comment, the
job is completed without dispatch to avoid duplicate replies.

Dismiss an obsolete blocked job after auditing it:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 dismiss <job-id> --reason "already handled"
```

### Unlock stale running jobs

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 unlock-stale --older-than 1800
```

Limit a manual unlock to audited job ids when other long-running jobs are still
making progress:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 unlock-stale --older-than 1800 --job-id 123
```

## Migration context

Initial migration target from the legacy worker:

- `~/.local/bin/pilipilis_inbox_worker.py`
- `~/.local/state/pilipilis/github-worklog.jsonl`
- `~/.local/state/pilipilis/github-active.json`
- `~/.local/state/pilipilis/inbox_state.json`
