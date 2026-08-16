# A dev build: this checkout's code against a live-safe copy of production data

**Date:** 2026-08-15
**Type:** Added
**Branch:** `dev`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes. Find it with:
     git log --oneline -- fork/changelog/entries/2026-08-15-01-dev-build-snapshot.md -->

## Why

`2026-08-14-02` added the `dev` branch and protected `main`, but a branch only
isolates **code**. Every run still used the one real `HERMES_HOME`, so testing dev
code meant pointing it at production sessions, config and credentials — and any
schema migration dev code performed would migrate the real `state.db`. windro asked
for "a temporary copy of prod to do and test things" that cannot affect the
production session.

The insight that keeps this cheap: code and data are separate axes. The git branch
isolates code; `HERMES_HOME` isolates data. Nothing needs a second install or a
second ~3 GB venv — the repo's own `.venv` plus an env var is a complete dev build.

`hermes profile create --clone-all` was the obvious built-in and is the wrong tool
here: it deliberately excludes `state.db` and `sessions/`
(`_CLONE_ALL_HISTORY_EXCLUDE_ROOT`, `hermes_cli/profiles.py:118`) because a new
profile is meant to be a fresh workspace. Session history is exactly what we want
copied, so this clones the profile and copies history in on top rather than fighting
that intent.

## What changed

Three new fork-owned files under `fork/devbuild/`. **No production code touched**,
and nothing under `AppData\Local\hermes` was modified — the snapshot only writes
into `profiles/dev`.

- **`fork/devbuild/snapshot_prod.py`** — creates/refreshes the `dev` profile from
  production. The load-bearing decision is that **every SQLite db is copied with
  `sqlite3.Connection.backup()`, never as a file**. Production is normally running
  when you snapshot (that is the point — you snapshot mid-work), and `state.db` is
  in WAL mode, so `shutil.copy2` races the writer: recent commits live in `-wal` and
  a byte copy can capture a torn page, yielding a snapshot that opens fine and is
  quietly missing or corrupting recent rows. The source is opened **read-only via
  URI** (`file:...?mode=ro`) so the script cannot write to production even on a bug.
  `--data-only` refreshes just `state.db` + `sessions/`; `--dry-run` prints the plan.

- **`fork/devbuild/dev.sh`** — sets `HERMES_HOME` to the snapshot and execs
  `python -m hermes_cli.main` from the repo's `.venv`. Prints branch + data path on
  every run so you can never be unsure which data you are on. Refuses to run if
  `.venv` is missing rather than falling back to a system python, because that would
  silently run a *different* (pip-installed) hermes against the dev profile and look
  like the dev build working. Warns when the branch is `main`.

- **`fork/devbuild/README.md`** — the workflow, the copy/skip table with a reason per
  item, and the gotchas.

**Exclusions that are load-bearing, not tidiness.** `backups/`,
`state-snapshots/`, `*.bak` and `*.pre-update-emergency-*` are excluded because they
are snapshots of *production* state: copying them means "restore backup" inside the
dev profile would resurrect prod data, defeating the entire isolation. `*.lock` /
`*.pid` / `gateway_state.json` are excluded because a copied lock makes the dev
instance reason about production's locks.

**Workflow correction.** windro proposed "push to dev → build dev → test → push to
prod". That inverts the useful order: the code is already on disk, so building *from*
the pushed branch means `dev` accumulates commits nobody has run. Documented order is
edit → test in the dev build → push to `dev` → CI → PR to `main`. Push is
publication, not a build step.

## Verified

The dry run against the real profile is what found three of the four gaps — a first
draft listed only `state.db` as a live db:

```bash
.venv/Scripts/python.exe fork/devbuild/snapshot_prod.py --dry-run
# -> revealed kanban.db, projects.db, verification_evidence.db as separate live
#    SQLite files, plus five state.db*.bak copies and three *.lock files
```

Snapshot taken with production live (this very session, gateway pid 9280):

```bash
.venv/Scripts/python.exe fork/devbuild/snapshot_prod.py     # -> ✓ in 148.5s
```

All four copies are structurally sound and complete:

```
state.db                   integrity=ok  tables=21
kanban.db                  integrity=ok  tables=8
projects.db                integrity=ok  tables=4
verification_evidence.db   integrity=ok  tables=4
sessions rows — prod: 8   dev: 8
no .bak copied: True      no lock/pid copied: True
```

Isolation proven by writing, not by reading config. Inserted a marker row into the
dev `state.db`, then read production read-only:

```
before —  prod: 8  dev: 8
after  —  prod: 8  dev: 9
marker leaked into prod: False
cleaned —  prod: 8  dev: 8
```

Launcher runs real commands against the snapshot:

```bash
bash fork/devbuild/dev.sh profile list
# -> banner shows branch: dev, data: ...\profiles\dev
# -> "◆dev" marked active; config (model) inherited from prod
```

**Not verified:** the desktop app path (`dev.sh desktop`). The Electron app needs a
renderer build and I did not launch a second GUI instance while prod's was running —
the CLI path is what I exercised. Also did not test running the dev gateway
concurrently with prod's; token locks are expected to refuse the second one, which is
correct behaviour but unproven here.

## Risk / watch for

- **The snapshot is a point in time and silently ages.** There is no staleness
  warning. If a dev test depends on a session created after the snapshot, it will not
  be there. Re-run the script.
- **A full refresh overwrites dev-side edits** to `config.yaml`, skills, etc.
  `--data-only` exists for that; the README says so, but nothing enforces it.
- **`_SQLITE_DBS` is a hardcoded list.** If a future Hermes version adds a new db to
  the profile root, it will be copied as a plain file and inherit the WAL tearing
  risk. `_WAL_SIDECARS` is derived from that list, so adding an entry is a one-line
  fix — but nothing detects the omission. A `*.db` glob with an allow-list check
  would be more robust.
- **`_prod_home()` duplicates the HERMES_HOME default** rather than importing
  `hermes_constants.get_hermes_home()`. That is deliberate — the script must resolve
  *production* even when `HERMES_HOME` is already overridden to the dev profile (as
  it is inside `dev.sh`), and `get_hermes_home()` reads the env var. If Hermes ever
  changes the default location, this needs updating too.
- **Nothing stops `snapshot_prod.py` running while dev code holds the dev db open.**
  It deletes and rewrites `profiles/dev/state.db`; doing that under a live dev
  session would confuse it. Close the dev build before refreshing.

## Follow-ups

- **A `--stale-after` warning**, or printing the snapshot's age in the `dev.sh`
  banner, would remove the main footgun (testing against data you think is current).
- **`dev.sh` has no PowerShell/cmd equivalent.** Fine while the workflow is
  git-bash, but the desktop app is launched from Windows shells in normal use.
- **Verify the desktop path end to end** — build the renderer and launch
  `dev.sh desktop` while prod is closed, to confirm the Electron app honours
  `HERMES_HOME` for its own `userData` (`updates.json`, window state) and not just
  for the backend.
