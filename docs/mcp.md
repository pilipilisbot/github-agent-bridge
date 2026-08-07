# MCP server

`github-agent-bridge` includes an authenticated read-only MCP server for agents
that need access to acquired bridge knowledge. The dashboard exposes the server
over HTTP so agents can connect without installing or launching `gab`.

## Create a token

Use the bridge database that the dashboard and executor already use:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 mcp-token-create --name "local agent"
```

The command prints the token once. Store it in the local agent environment, for
example:

```bash
export GITHUB_AGENT_BRIDGE_MCP_TOKEN="gab_mcp_..."
```

Token records can be listed and revoked without exposing the token secret:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 mcp-tokens
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 mcp-token-revoke <token-id>
```

Dashboard admins can manage the same records through:

- `GET /api/mcp/users`
- `GET /api/mcp/tokens`
- `POST /api/mcp/tokens`
- `PATCH /api/mcp/tokens/{token_id}`
- `DELETE /api/mcp/tokens/{token_id}`

The dashboard owner selector is built from configured dashboard users, configured
dashboard admins, the signed-in admin, and any users already linked to existing
MCP tokens. Admins can use that selector to issue new tokens for a known user or
link an existing active token to a user without exposing the token secret again.

The dashboard MCP page shows both a public dashboard URL for token management
and the public MCP endpoint URL. Behind a reverse proxy, set
`GITHUB_AGENT_BRIDGE_DASHBOARD_PUBLIC_URL` to the external origin, for example
`https://bridge.example.com`. If that variable is unset, the dashboard derives
the origin from `X-Forwarded-Proto`, `X-Forwarded-Host`, and
`X-Forwarded-Prefix` when the proxy sends them.

## Connect over HTTP

Remote agents should connect to the dashboard endpoint:

```text
https://bridge.example.com/api/mcp
```

Authenticate every MCP request with:

```text
Authorization: Bearer gab_mcp_...
```

For clients that accept JSON MCP server config:

```json
{
  "mcpServers": {
    "github-agent-bridge": {
      "url": "https://bridge.example.com/api/mcp",
      "headers": {
        "Authorization": "Bearer ${GITHUB_AGENT_BRIDGE_MCP_TOKEN}"
      }
    }
  }
}
```

## Exposed capabilities

The first MCP surface is intentionally read-only:

- `list_repositories`: repositories with acquired bridge knowledge.
- `list_knowledge`: curated knowledge rules applicable to a repository.
- `gab://knowledge/{owner}/{repo}` resources: JSON knowledge snapshots.
