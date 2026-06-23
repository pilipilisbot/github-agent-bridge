# MCP server

`github-agent-bridge` includes a stdio MCP server for local agents that need
read-only access to acquired bridge knowledge.

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

- `GET /api/mcp/tokens`
- `POST /api/mcp/tokens`
- `DELETE /api/mcp/tokens/{token_id}`

## Run the server

Configure the local MCP client to launch:

```bash
gab --db ~/.local/state/github-agent-bridge/bridge.sqlite3 mcp-serve
```

`mcp-serve` rejects startup unless `GITHUB_AGENT_BRIDGE_MCP_TOKEN` or
`--token` matches an active token in the bridge database.

## Exposed capabilities

The first MCP surface is intentionally read-only:

- `list_repositories`: repositories with acquired bridge knowledge.
- `list_knowledge`: curated knowledge rules applicable to a repository.
- `gab://knowledge/{owner}/{repo}` resources: JSON knowledge snapshots.
