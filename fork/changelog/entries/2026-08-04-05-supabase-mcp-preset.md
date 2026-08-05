# Supabase as an MCP preset, read-only by default

**Date:** 2026-08-04
**Type:** Added
**Branch:** `main`

## Why

windro asked for Supabase "as a supported tool". Supabase ships an official MCP server,
so wiring that is better than writing and maintaining a parallel native tool.

## What changed

- **`hermes_cli/mcp_config.py`** — `_MCP_PRESETS` gains `supabase`, running Supabase's
  own server with `--read-only`.
- **`hermes_cli/subcommands/mcp.py`** — `--preset` now has `choices`, so
  `hermes mcp add --help` lists `{codex,supabase}`. Previously presets were invisible
  unless you read the source.

**Read-only is the default**, chosen without asking. It maps to a read-only Postgres
role: the agent can read schema and query data but cannot `DROP`, `DELETE` or migrate.
His Supabase project holds real data, and an agent with unrestricted DDL on a live
database is not a recoverable mistake. Opting out is one documented command.

## Two bugs that only appeared when it was actually connected

Both surface identically as `Failed to connect: Connection closed`, with no detail.

1. **`npx` is not spawnable on Windows.** It is a `.cmd` shim and `CreateProcess` cannot
   execute it: `Popen(["npx", ...])` raises `FileNotFoundError [WinError 2]`, while
   `npx.cmd` returns `11.12.1`. Hermes spawns stdio MCP servers without a shell, so the
   preset as first committed could never have started on this machine. Now
   platform-aware.
2. **Hermes does not forward ambient env to MCP children.** Exporting
   `SUPABASE_ACCESS_TOKEN` in a shell does nothing; it must be declared on the server
   with `--env`. Without it the server prints "Please provide a personal access token"
   to stderr and exits with empty stdout. Isolated by running the server with the token
   stripped from the child env and comparing.

Both are documented at the preset with exact commands, since neither is inferable from
the error.

## Verified

Connected for real: **29 tools discovered**, saved to `HERMES_HOME/config.yaml` (outside
the repo, so the token cannot be committed). 11 guards, fork suite passing,
mutation-checked (read-only removed, choices removed, preset unwired).

## Risk / watch for

- **The token is account-wide.** `--preset` overrode an explicit `--project-ref`, so the
  server sees every project the token can. Today that is one; wrong the moment there are
  two. The scoped form is documented.
- 11 guards passed while the preset was fundamentally unable to start. Guards on
  argument construction do not tell you a process launches.
