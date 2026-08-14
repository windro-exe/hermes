<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# Projects nest: a project's identity is now a path of slugs, to unlimited depth

**Date:** 2026-08-14
**Type:** Added
**Branch:** main

## Why

windro asked for project paths shaped like `official/os-projects` and
`official/os-projects/nettacker`, with unlimited depth. Projects were flat: one
`projects` row, `slug TEXT NOT NULL UNIQUE`, no parent. Everything a person would
call a hierarchy — a group of client projects, an umbrella with sub-repos — had to
be flattened into sibling rows with names doing the work of structure.

The trigger was concrete. `26cd4d9a7` made session membership sticky because a
session started in "Os-Projects" (`C:\wnx-projects\official\os-contributions`)
silently left the project when the agent `cd`'d into the SIBLING checkout
`nettacker`. Sticky membership stopped the session escaping, but it did not give
`nettacker` anywhere to live. It is its own workspace, related to os-projects, and
flat projects had no way to say that.

The important design point, in windro's words: the path is **not** the filesystem.
On his disk `nettacker` and `os-contributions` are siblings under
`C:\wnx-projects\official`. In the namespace, `nettacker` sits *inside*
`official/os-projects`. Deriving the hierarchy from folder layout was considered and
rejected for exactly this reason — it cannot express what he asked for.

`os-contributions` was only his example. Nothing here is hardcoded to it.

Note for whoever reads the index next: the six commits before this one
(`a049a75ec`, `d51cdcbdd`, `26cd4d9a7`, `440f28480`, `feccf8c19`, `7327e8067`)
landed with no changelog entry. That is a lapse against this folder's own rules; the
project-related three are described from the outside here because this change builds
directly on them.

## What changed

Backend — the store:

- **`hermes_cli/projects_db.py`** — `projects` gains
  `parent_id TEXT REFERENCES projects(id) ON DELETE CASCADE`, a self-referencing FK,
  so deleting a project cascades to its subtree inside SQLite rather than in
  application code. The v1 declaration `slug TEXT NOT NULL UNIQUE` had to go:
  uniqueness is now **per parent**, so `official/docs` and `personal/docs` can both
  exist. That constraint is enforced by an implicit `sqlite_autoindex` that no DDL
  statement can drop, so `_migrate_nested_projects` does the documented table
  rebuild — foreign keys off, copy into `projects_migrating`, drop, rename, foreign
  keys back on — then `POST_MIGRATION_SQL` adds
  `UNIQUE INDEX ON projects(IFNULL(parent_id, ''), slug)`. The `IFNULL` matters:
  plain `(parent_id, slug)` would not constrain root projects at all, because SQL
  treats every NULL as distinct.

  The index DDL lives outside `SCHEMA_SQL` on purpose. On a legacy DB the `projects`
  table exists without `parent_id`, so an index over that column inside the schema
  script would fail before the migration could add it and abort the whole script.

  A project's `path` and `depth` are **derived on every read** by a recursive CTE
  (`_PATHS_CTE`), never stored. That is what makes a rename or a move re-path a whole
  subtree with a single UPDATE and no chance of a stale copy. The walk is seeded from
  the roots and bounded by `MAX_DEPTH`, so a dangling parent yields a missing path
  rather than an infinite walk.

  New API: `move_project` (rejects self-parenting and moves into one's own subtree),
  `list_children`, `descendant_ids`, `subtree_ids`, `project_path`,
  `normalize_path_key`. `get_project` resolves an id, then a full path, then a bare
  slug — the last only when exactly one project answers to it, because with
  per-parent slugs a bare slug is genuinely ambiguous and guessing would file work
  in the wrong project. `archive_project`/`restore_project` now cascade to the
  subtree: a hidden parent with visible children would strand those children, whose
  paths resolve through a project the tree no longer lists. `branch_name_for` uses
  the full path, which leaves root projects' branch names byte-identical.

Backend — the tree and the RPCs:

- **`tui_gateway/project_tree.py`** — tier 1 nodes carry `parentId`, `projectPath`,
  `depth`, and `totalSessionCount`, and `_rollup_nested` accumulates subtree totals,
  `lastActive`, and previews onto each ancestor. The payload deliberately stays
  **FLAT**. A physically nested payload was written first and reverted: roughly ten
  call sites scan `$projectTree` linearly (cwd → project, the coding rail, session
  tiles, `projects.project_sessions`), and every one of them would silently stop
  finding sub-projects. Flat rows plus `parentId` keeps them all correct and moves
  only the indentation to the renderer. `path` remains the folder on disk;
  `projectPath` is the namespace.

- **`tui_gateway/server.py`** — `projects.create` takes `parent` (id, slug, or path);
  `projects.update` takes `slug`; new `projects.move` returns the whole payload
  because a move re-paths every descendant. `projects.delete` collects
  `pdb.subtree_ids` **before** deleting: the FK cascade cannot reach sessions, which
  live in a different database, so the id set has to be gathered while the
  sub-projects still exist.

- **`hermes_cli/projects_cmd.py`** — `project create --parent`, a new `project move`,
  and `project list` indents by depth (`list_projects` orders by path, so parents
  always precede their children in one pass).

- **`tools/project_tools.py`** — `project_create` takes `parent`; `project_list`
  reports `path`/`parent_id`; `_resolve` accepts a path and now returns nothing on an
  ambiguous name instead of picking the first match.

Renderer (all upstream files — there is no fork-owned sidebar to put this in):

- **`workspace-groups.ts`** — `SidebarProjectTree` gains the four nesting fields.
- **`projects/model.ts`** — `arrangeProjectRows` turns the flat list into visible
  rows: children follow their parent, a collapsed project's whole subtree is
  omitted, depth is recomputed from *visible* ancestors, a child whose parent is
  missing is promoted rather than dropped, and members of a `parentId` cycle are
  surfaced as roots. `sortProjectsForOverview` / `orderProjectsByIds` now test
  `subtreeSessionCount`, so a namespace project holding no sessions of its own does
  not sink below empty ones.
- **`projects/overview-row.tsx`** — indents by depth and reuses the one disclosure
  caret for sub-projects as well as the session preview; that caret stays visible
  (not hover-only) when it hides sub-projects, since nothing else reveals them.
- **`sessions-section.tsx`** — reads `$sidebarWorkspaceNodeOpen` to build the row
  list, and suppresses manual drag-reorder while anything is nested: the saved order
  is a flat array of ids and cannot express "between two roots, but nested".
- **`projects/project-menu.tsx`** — "New sub-project", plus the delete confirmation
  states how many sub-projects go with it.
- **`store/projects.ts`**, **`types/hermes.ts`**, **`i18n/*`** — `parent` on create,
  `parentId`/`parentLabel` on the create dialog, `parent_id`/`path`/`depth` on
  `ProjectInfo`, and four new strings translated into all five locales.

## Verified

```bash
.venv/Scripts/python.exe -m pytest tests/fork -q
# -> 191 passed   (includes the 44 new tests below)
.venv/Scripts/python.exe -m pytest tests/fork/test_nested_projects_db.py \
    tests/fork/test_nested_project_tree.py tests/fork/test_nested_projects_rpc.py -q
# -> 44 passed
.venv/Scripts/python.exe -m pytest tests/tui_gateway -q \
    --ignore=tests/tui_gateway/test_compute_host.py \
    --ignore=tests/tui_gateway/test_compute_host_phase1.py
# -> 533 passed
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_projects_cli.py \
    tests/hermes_cli/test_kanban_project_link.py tests/tools/test_kanban_tools.py -q
# -> all passed
cd apps/desktop && node ../../node_modules/typescript/bin/tsc -p . --noEmit
# -> clean
cd apps/desktop && node ../../node_modules/eslint/bin/eslint.js src/app/chat/sidebar \
    src/store/projects.ts src/types/hermes.ts src/i18n
# -> 0 errors (4 pre-existing warnings in i18n/context.test.tsx)
cd apps/desktop && npx vitest run --project ui src/__fork__/nested-projects.test.ts
# -> 10 passed
```

The legacy-schema rebuild was exercised end to end against a hand-written v1
`projects.db`: folders, `primary_path`, and the `project_meta` active pointer all
survive, `PRAGMA foreign_key_check` is empty, `PRAGMA foreign_keys` is back to 1,
the `UNIQUE` autoindex is gone, and a second `connect()` is a no-op.

The CLI was driven for real against a scratch `HERMES_HOME`:

```
$ hermes project create "Nettacker" --parent official/os-projects
Created project official/os-projects/nettacker (p_a3081a9b)
$ hermes project move official/os-projects/nettacker
Moved official/os-projects/nettacker -> nettacker
```

**Not verified.** `tests/hermes_cli/test_projects_db.py` has 7 failures — they are
POSIX path assumptions (`'/a/scanned'` vs `'C:\a\scanned'`) and fail identically on
`7327e8067` with this change stashed. `tests/tui_gateway/test_compute_host_phase1.py`
cannot run on Windows at all: line 359 calls `os._exit(7)`, which without `fork`
terminates the pytest process. Two files in the full desktop UI suite
(`app/skills`, `app/messaging`) failed under whole-suite parallel load and pass in
isolation both with and without this change — timeout flake, not a regression.
Nothing here was exercised in the running desktop app: **the renderer changes are
untested against a real window**, and per
`fork/changelog/entries/…` topology notes a renderer change needs
`desktop --build-only --force-build` in the managed checkout before windro would see
it.

## Risk / watch for

- **The table rebuild is the sharp edge.** It runs once per DB, inside a write
  transaction with foreign keys off. If it is interrupted, `project_folders` can
  outlive its `projects` rows; the code logs a warning and never deletes. Anyone
  adding a column to `projects` must add it to BOTH `SCHEMA_SQL` and
  `_PROJECTS_TABLE_DDL`/`_PROJECTS_COLUMNS`, or a legacy DB migrating later will
  silently drop that column's data.
- **Flatness of the `projects.tree` payload is load-bearing.** If a future change
  nests `children` in the payload, every linear scan of `$projectTree` stops seeing
  sub-projects. The tests in `tests/fork/test_nested_project_tree.py` assert the
  payload is flat for exactly this reason.
- **A bare slug can now resolve to nothing.** `get_project(conn, "docs")` returns
  `None` when two projects are called `docs`. Callers that used to rely on globally
  unique slugs (`kanban_db.py:2915`, `projects_cmd.py`) get an honest miss instead
  of a wrong project, but a caller that treated `None` as "impossible" would now see
  it.
- **Manual project reordering is off while anything is nested.** That is a deliberate
  reduction, not a bug; restoring it needs a per-parent order model.
- Upstream owns every renderer file touched here. `sessions-section.tsx` and
  `projects/model.ts` are the two most likely to conflict.

## Follow-ups

- Entering a parent project shows only its own repos and lanes. Whether it should
  also aggregate its sub-projects' sessions is undecided — merging them would blur
  the boundary `26cd4d9a7` just made sticky.
- No drag-and-drop re-parenting in the sidebar. `projects.move` exists; only the CLI
  and the RPC call it.
- Per-parent manual ordering, to bring drag-reorder back for nested trees.
- The six commits before this one still have no changelog entries.
