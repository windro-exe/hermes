# Agent-facing fork rules, read before upstream's guide

**Date:** 2026-07-27
**Type:** Added
**Branch:** `docs/fork-agent-rules`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes, so any sha here would be a guess or go stale on amend/rebase.
     Find it with: git log --oneline -- <path to this file> -->

## Why

Any AI agent opening this repo read upstream's `AGENTS.md` and concluded it was
working on `NousResearch/hermes-agent`. Nothing told it otherwise. The concrete
failures available to it:

- push to the wrong remote, or open a pull request against upstream
- "helpfully" sync upstream while doing something else — an automated sync used to
  run on this machine and was removed on purpose, so an agent finding the leftover
  scripts could reasonably rebuild it
- commit straight to `main`, which windro's workflow forbids
- run `tests/hermes_cli/test_cmd_update.py`, which on this machine spawns real
  `hermes gateway run` processes and leaks them — it once left 56 strays behind
- edit files under `AppData\Local\hermes\`, which holds windro's sessions, profiles,
  config and auth
- forget that this directory is windro's **live install**, so a renderer change with
  no rebuild looks like a change that did nothing

windro asked for this explicitly, scoped to the dev phase, and asked that it be
removable later.

## What changed

- **`AGENTS.md`** (upstream file) — one added block at the very top, no removals,
  no modifications. Position is the point: agents auto-load this file, and content
  at the top is read before upstream's ~1400 lines of guide. The block is wrapped in
  `FORK-RULES: START` / `FORK-RULES: END` HTML comment markers so that when upstream
  edits the top of the file and it conflicts, the correct resolution is obvious —
  keep both sides.

  Touching an upstream file was deliberate here. A fork-owned file would not work,
  because the requirement is "the first thing the AI reads", and no fork-owned
  filename is auto-loaded by agents. `AGENTS.md` is the only cross-agent convention
  — Codex, Cursor and Syncode all read it.

  Cost, measured before choosing: `AGENTS.md` is ~1400 lines with 102 commits and
  was last edited 22 hours before this change. It is the hottest file in the repo,
  and 8 of the last 40 commits touching it changed lines near the top. So conflicts
  here are expected, not hypothetical — the markers exist because of that.

- **`CLAUDE.md`** (new, fork-owned) — one paragraph pointing at the block. Claude
  Code auto-loads this name. Upstream does not have the file, so it cannot conflict.

- **`.github/copilot-instructions.md`** (new, fork-owned) — same pointer, for GitHub
  Copilot and GitHub's coding agent. Upstream does not have the file. `.github/`
  itself exists upstream but contains no instruction file.

## Verified

Removal is clean — the block can be deleted later with no residue:

```bash
python -c "
import re, subprocess
src = open('AGENTS.md', encoding='utf-8').read()
stripped = re.sub(r'<!-- =+ FORK-RULES: START.*?FORK-RULES: END =+ -->\n\n', '', src, flags=re.S)
orig = subprocess.run(['git','show','HEAD:AGENTS.md'], capture_output=True, text=True, encoding='utf-8').stdout
print(stripped == orig)
"
# -> True
```

Diff shape — additions only, nothing removed or modified in `AGENTS.md`:

```bash
git diff --numstat HEAD~1
# -> additions only for all three files, zero deletions
```

Path availability checked before choosing filenames: `CLAUDE.md`,
`.github/copilot-instructions.md`, `.github/instructions`, `.github/prompts`,
`.github/agents.md` — none exist upstream.

**Not verified:** whether every agent windro might use actually auto-loads
`AGENTS.md`. Confirmed by convention and documentation for Codex, Cursor and
Syncode; not tested by running each one against this repo.

## Risk / watch for

- **`AGENTS.md` will conflict on upstream merges.** Expected. The resolution is
  always "keep the `FORK-RULES` block and take upstream's changes below it." If a
  future merge drops the block silently, agents lose the rules with no error — worth
  a glance after every upstream merge.
- **The block will go stale.** It names the current branch (`perf/ui-latency`) and
  states that upstream's `profile-typing-lag.md` is out of date. Both are true on
  2026-07-27. Whoever changes either should update the block.
- **Pointer files are only as good as the block.** If `AGENTS.md` is ever replaced
  wholesale by an upstream merge, `CLAUDE.md` and `copilot-instructions.md` will
  point at something that no longer exists.

## Follow-ups

- The GitHub repo description / About section is still upstream's. That text is not
  in git, so it cannot conflict, and it is the first thing a human or agent sees on
  the repo page. `gh` is not installed on this machine, so it was not set.
- `main` on `origin` still holds the old 35-commit fork history (`09b41bc41`) while
  local `main` is stock upstream. They have fully diverged. Publishing this branch
  will need that resolved.
