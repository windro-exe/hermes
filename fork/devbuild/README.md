<!-- Temporary dev-phase file for windro's fork. Safe to delete with the rest of fork/. -->

# The dev build

A dev build is **this checkout's code running against a copy of your real data.**
Nothing here can change production.

Two things are isolated, on two different axes. Confusing them is the mistake this
folder exists to prevent:

| axis | isolated by | prod | dev |
|---|---|---|---|
| **code** | git branch + which venv | `AppData\Local\hermes\hermes-agent` (own `venv/`, tracks `main`) | this repo (`.venv/`, normally on `dev`) |
| **data** | `HERMES_HOME` | `AppData\Local\hermes` | `AppData\Local\hermes\profiles\dev` |

Because they are separate, a dev build needs **no second install and no second
3 GB venv** — just an env var pointing at a snapshot profile.

## Use it

```bash
# 1. take/refresh the data snapshot (safe while prod is running)
.venv/Scripts/python.exe fork/devbuild/snapshot_prod.py

# 2. run dev code against it
bash fork/devbuild/dev.sh                 # interactive CLI
bash fork/devbuild/dev.sh desktop         # desktop app
bash fork/devbuild/dev.sh profile list    # any subcommand
bash fork/devbuild/dev.sh --              # print the env, run nothing
```

`dev.sh` prints which branch and which `HERMES_HOME` it is using every time. If
that banner ever says `data: ...\hermes` without `profiles\dev`, stop — you are
pointed at production.

## The workflow

```
edit  →  test in the dev build  →  push to dev  →  CI (Linux)  →  PR to main
```

Test **before** pushing, not after. Push is publication, not a build step: the code
is already on your disk, so a "push then build" loop only means `dev` collects
commits nobody has run yet. Testing first keeps `dev` trustworthy, which is the
whole reason it exists.

`main` is protected and requires the `All required checks pass` check, so the last
step cannot be skipped by accident — a direct push is refused by the remote.

## What the snapshot copies, and what it deliberately does not

Copied: `config.yaml`, `.env`, `auth.json`, `SOUL.md`, `skills/`, `memories/`,
`cron/`, `sessions/`, `platforms/`, and every SQLite db in the profile root
(`state.db`, `kanban.db`, `projects.db`, `verification_evidence.db`).

Not copied, each for a reason:

- **`hermes-agent/`, `node_modules/`, `bin/`** — code and heavy installs. The dev
  build runs from this repo; a copy of the managed install would be dead weight.
- **`profiles/`** — sibling profiles. Copying recursively would nest a copy of the
  dev profile inside itself on every refresh.
- **`backups/`, `state-snapshots/`, `*.bak`, `*.pre-update-emergency-*`** — these
  are snapshots of **production** state. Carrying them in means "restore backup"
  inside the dev profile would resurrect prod data, defeating the isolation.
- **`*.lock`, `*.pid`, `gateway_state.json`** — runtime ownership. A copied lock
  makes the dev instance reason about production's locks.
- **`logs/`, `cache/`, `bootstrap-cache/`, `audio_cache/`** — regenerable.
- **`hermes-setup.exe`** — the updater. The dev build must never self-update.

## SQLite is copied with the backup API, never as a file

Production is normally **running** when you snapshot (that is the point). `state.db`
is in WAL mode, so a plain file copy races the writer: recent commits live in
`-wal`, and a byte copy can capture a torn page — producing a snapshot that opens
fine and is quietly missing or corrupting recent rows. `snapshot_prod.py` uses
`sqlite3.Connection.backup()`, which takes a consistent snapshot of a live db, and
opens the source **read-only via URI** so it cannot write to production even on a
bug.

## Gotchas

- **The snapshot goes stale.** It is a point-in-time copy. Re-run
  `snapshot_prod.py` when you want current data; `--data-only` refreshes just
  `state.db` + `sessions/` and leaves any config/skill edits you made in dev alone.
- **A refresh overwrites dev edits.** Full refresh replaces `config.yaml`, skills,
  etc. from prod. Use `--data-only` to keep them.
- **Don't run both gateways with the same bot token.** Token locks
  (`acquire_scoped_lock`) will refuse the second one — correctly. Prod's gateway is
  usually already running.
- **`dev.sh` refuses to run without `.venv`.** Falling back to a system python
  would silently run a *different* (pip-installed) hermes against the dev profile
  and look like the dev build working.
- **Delete the whole thing** by removing `AppData\Local\hermes\profiles\dev`.
  Production is untouched by that.
