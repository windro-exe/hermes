# The connected GitHub account is now the agent's GitHub credential

**Date:** 2026-08-04
**Type:** Fixed
**Branch:** `main`

## Why

windro connected GitHub in the desktop, then asked the agent to push to a private repo.
It went looking for the `gh` CLI. Correct behaviour for the code as shipped, and a gap
in what had been built.

The integration was **renderer-only**: the token sits encrypted in the Electron main
process for the project dialog, and nothing on the Python side could read it — grepping
`tools/`, `agent/`, `hermes_cli/` and `tui_gateway/` for the token file and for
`Roaming/Hermes` returns no hits. So the agent fell through its own chain in
`tools/skills_hub.py:348`:

```
1. GITHUB_TOKEN / GH_TOKEN env var
2. `gh auth token` subprocess
3. GitHub App JWT
4. Unauthenticated
```

No `GITHUB_TOKEN` was set and `gh` is not installed, so it hunted for the CLI.

## What changed

**`apps/desktop/electron/main.ts`** — `githubAgentEnv()`, spread into the environment at
**both** gateway spawn sites, after `...process.env` so a stale inherited token cannot
win. Returns `{}` when no account is connected, leaving behaviour unchanged rather than
inventing auth.

```
GITHUB_TOKEN             the connected account's token
GIT_CONFIG_COUNT         '2'
GIT_CONFIG_KEY_0/VALUE_0 'credential.helper' / ''            <- resets the helper list
GIT_CONFIG_KEY_1/VALUE_1 'credential.https://github.com.helper' / helper
```

Passed through the **environment** rather than `git config`: no global or system config
is modified, nothing lands in `.git/config`, the token never appears in
`git remote -v`, and it applies only to this process tree. The helper reads
`$GITHUB_TOKEN` at call time, so it stays correct after a re-login without a respawn.

## The first version was wrong, and I said it was fixed

Adding a helper is not enough. **Git runs every configured helper in order and takes the
first answer**, and this machine has `credential.helper=store` globally, reading a
plaintext token from `~/.git-credentials`. Measured with `git credential fill`: that old
token answered first, so `git push` would have kept using it.

Entry 0 is now an empty unscoped `credential.helper`, which clears the list git has
assembled so far. The reset must be **unscoped**, because the global `store` entry is
unscoped — a github.com-scoped reset does not clear it.

## Verified

Against a *competing* helper, not in isolation: with a local helper configured to return
a different secret, the injected env still wins (`password=connected-account-token`, the
competitor's value absent). Removing the reset fails that test.

Scoping holds: `host=gitlab.com` is supplied nothing, so a GitHub token is never offered
to another host.

12 guards in `tests/fork/test_github_agent_auth.py`, fork suite 147 passing,
mutation-checked (removing either spawn site, or the reset, fails), electron typecheck
clean.

**Three verification attempts were worthless before one worked**, which is the more
useful lesson: two used a public repo, where `ls-remote` succeeds without auth either
way; the third looked right but `~/.git-credentials` already held a github.com token and
was answering the probe. Only `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`
isolated it.

## Risk / watch for

- **The token is now readable by anything the agent spawns**, which is wider than
  "main process only". That is the price of the requirement that the connected account
  works for every GitHub operation, not only the ones the dialog implements.
- **Inside the agent's process tree the global `store` helper is disabled for all
  hosts.** GitHub is covered; a non-GitHub HTTPS remote that relied on `store` would now
  prompt.
- **SSH remotes are untouched.** A `git@github.com:` remote uses the SSH key, not this.
- A real PAT was printed in full during verification and must be rotated. Avoid echoing
  credential-helper output; redact before printing.
