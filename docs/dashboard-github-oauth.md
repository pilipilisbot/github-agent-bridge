# Dashboard GitHub OAuth Setup

This guide configures GitHub login for the optional
`github-agent-bridge-dashboard` service.

The dashboard is an operator tool over the bridge SQLite database. Keep it
loopback-only unless it is behind HTTPS and an authenticated reverse proxy.

## Prerequisites

- A running bridge database at
  `~/.local/state/github-agent-bridge/bridge.sqlite3`.
- The dashboard extra installed, or a checkout that can run
  `github-agent-bridge-dashboard`.
- Permission to create a GitHub OAuth App in the account or organization that
  owns the deployment.
- A stable dashboard origin. For local-only operation, use
  `http://127.0.0.1:8765`.

## Create the GitHub OAuth App

1. Open GitHub developer settings:
   - user app: `https://github.com/settings/developers`;
   - organization app: `https://github.com/organizations/ORG/settings/applications`.
2. Choose **New OAuth App**.
3. Set **Application name** to a clear operator-facing name, for example
   `GitHub Agent Bridge Dashboard`.
4. Set **Homepage URL** to the dashboard origin:

   ```text
   http://127.0.0.1:8765
   ```

   If the dashboard is published through a reverse proxy, use its external
   HTTPS origin instead.
5. Set **Authorization callback URL** to:

   ```text
   http://127.0.0.1:8765/auth/callback
   ```

   For a reverse proxy, keep the same path on the external origin, for example
   `https://bridge.example.com/auth/callback`.
6. Create the app, then copy the **Client ID**.
7. Generate a **Client secret** and copy it into the private environment file.

The dashboard requests `read:user` by default. It also requests `read:org` when
access or admin mode is granted by GitHub organization or team membership,
especially for private organization and team membership.

## Configure the Dashboard Environment

Add the dashboard settings to `~/.config/github-agent-bridge/env`:

```bash
cat >> ~/.config/github-agent-bridge/env <<'EOF'
GITHUB_AGENT_BRIDGE_DASHBOARD_SECRET_KEY=replace-with-random-secret
GITHUB_OAUTH_CLIENT_ID=replace-with-github-oauth-client-id
GITHUB_OAUTH_CLIENT_SECRET=replace-with-github-oauth-client-secret
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_USERS=your-github-login
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_ORGS=
GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_TEAMS=
GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS=your-github-login
GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS=
GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL=
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PUBLIC_KEY=
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PRIVATE_KEY=
GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_CONTACT=mailto:admin@example.com
EOF
chmod 600 ~/.config/github-agent-bridge/env
```

Generate a strong session signing secret:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

Use at least one authorization allowlist:

- `GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_USERS`: comma-separated GitHub logins.
- `GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_ORGS`: comma-separated GitHub
  organizations whose members may access the dashboard.
- `GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_TEAMS`: comma-separated GitHub teams in
  `org/team-slug` form whose members may access the dashboard.
- `GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS`: comma-separated GitHub logins
  allowed to run guarded dashboard actions such as retrying a job.
- `GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS`: comma-separated GitHub teams in
  `org/team-slug` form whose members may run guarded dashboard actions.
- `GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL`: optional external dashboard
  origin, for example `https://bridge.example.com`, used in shareable dashboard
  links when the service runs behind nginx or another reverse proxy.
- `GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PUBLIC_KEY`: browser Push API VAPID public
  key served to signed-in dashboard users.
- `GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_PRIVATE_KEY`: matching VAPID private key
  used by the executor to send job completion pushes.
- `GITHUB_AGENT_BRIDGE_WEB_PUSH_VAPID_CONTACT`: VAPID `sub` claim, usually a
  `mailto:` contact for the operator.

If all allowlists are empty, any authenticated GitHub user is accepted. That is
only appropriate for isolated local development.

Admin allowlists also grant dashboard read access. A user or team listed in
`GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_USERS` or
`GITHUB_AGENT_BRIDGE_DASHBOARD_ADMIN_TEAMS` can sign in even when it is not
listed in an `ALLOWED_*` variable.

Per-repository dashboard scopes are part of the issue #4 architecture but are
not implemented in the current dashboard backend.

## Browser Push Notifications

Completion notifications use the browser Push API, not GitHub mention comments.
Run the dashboard behind HTTPS, set `GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL`
to that external origin, and configure the VAPID key pair above. Each GitHub user
must sign in to the dashboard and enable the bell control once; the subscription
is stored against their GitHub login and the executor targets that login when a
job they triggered finishes as `done` or `blocked`.

## Start the Service

Run it manually:

```bash
set -a
. ~/.config/github-agent-bridge/env
set +a

github-agent-bridge-dashboard \
  --db ~/.local/state/github-agent-bridge/bridge.sqlite3 \
  --host 127.0.0.1 \
  --port 8765
```

Or enable the packaged user service after copying the systemd units:

```bash
systemctl --user daemon-reload
systemctl --user enable --now github-agent-bridge-dashboard.service
```

Check the service:

```bash
curl http://127.0.0.1:8765/api/health
systemctl --user status github-agent-bridge-dashboard.service
```

Open `http://127.0.0.1:8765/`. The dashboard redirects unauthenticated users
through GitHub and then back to `/auth/callback`.

## Reverse Proxy Checklist

When exposing the dashboard beyond localhost:

- terminate TLS before the dashboard;
- set the OAuth App homepage and callback URL to the public HTTPS origin;
- keep cookies `Secure`, `HttpOnly`, and `SameSite=Lax`;
- restrict proxy access to the intended operator network where possible;
- do not publish the raw SQLite file or arbitrary filesystem paths;
- rotate `GITHUB_OAUTH_CLIENT_SECRET` and
  `GITHUB_AGENT_BRIDGE_DASHBOARD_SECRET_KEY` if they were ever committed,
  pasted into logs, or shared in chat.

## Troubleshooting

`oauth_not_configured`
: The secret key, client ID, or client secret is missing from the dashboard
  process environment.

`oauth_state_mismatch`
: The callback did not include the expected OAuth state cookie. Retry from
  `/auth/login`, and check that the browser is using the same hostname and
  scheme for login and callback.

`not_authorized`
: The GitHub login is not in `GITHUB_AGENT_BRIDGE_DASHBOARD_ALLOWED_USERS` and
  is not a member of an allowed org visible to the OAuth token.

Org membership does not work
: Confirm the app requested `read:org`, the user authorized that scope, and the
  org has not blocked OAuth App access.
