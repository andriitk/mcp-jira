# Connecting MCP clients

mcp-jira speaks MCP over Streamable HTTP at `<BASE_URL><BASE_API_PATH>/mcp`, for example
`http://localhost:8711/mcp`. Any client that supports OAuth for remote MCP servers discovers the sign-in flow on its
own.

## Claude Code

```bash
claude mcp add --transport http jira-acme http://localhost:8711/mcp
```

Then run `/mcp`, select `jira-acme`, and choose **Authenticate**.

Check that it works by asking the agent to *call whoami on jira-acme*. It should answer with your site and account.

## One site per project

Claude Code has three registration scopes:

| Scope | Visible in | Stored in |
|---|---|---|
| `local` (default) | The project you ran the command in | Your user config, keyed by project path |
| `project` | Everyone who clones the repository | `.mcp.json` committed to the repository |
| `user` | Every project on your machine | Your user config |

When you work with several Jira sites, register each instance with `local` scope in the projects that belong to
that site. The agent then sees only the right Jira in each project, and nothing is written into the repositories:

```bash
# Register jira-acme in every git repository under ~/work/acme
for repo in $(find ~/work/acme -maxdepth 2 -name .git -type d -prune | xargs -n1 dirname); do
  (cd "$repo" && claude mcp add --transport http --scope local jira-acme http://localhost:8711/mcp)
done
```

A new repository needs the same one-line command. Use `user` scope only for an instance that should be available
everywhere.

## Other clients

| Client | Setting |
|---|---|
| Any MCP client with remote HTTP + OAuth | Server URL `http://localhost:<port>/mcp`; authorization is discovered automatically |
| Clients without OAuth support | Not supported: every call must be made as a signed-in user |

If a client fails to register, see [Troubleshooting](authentication.md#troubleshooting).
