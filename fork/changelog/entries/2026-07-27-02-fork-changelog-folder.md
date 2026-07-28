# A changelog folder so future agents know why the fork differs

**Date:** 2026-07-27
**Type:** Added
**Branch:** `docs/fork-agent-rules`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes, so any sha here would be a guess or go stale on amend/rebase.
     Find it with: git log --oneline -- <path to this file> -->

## Why

The fork rules added in
[entry 01](./2026-07-27-01-agent-fork-rules.md) tell an agent how to *behave*. They
don't tell it what this fork has already *done*. Without that, two failures repeat:

1. **An agent reverts a fork fix it doesn't understand.** It finds a line that looks
   wrong or looks like a merge artifact, tidies it up, and the fix is gone with no
   error and no test failure to catch it.
2. **An agent re-solves a solved problem.** This already happened in this session.
   Upstream's `apps/desktop/scripts/profile-typing-lag.md` has a section titled
   "Not fixed: Streamdown markdown re-parse cost (the elephant)", so the natural
   conclusion is that streaming re-parses the whole message per token. It doesn't —
   incremental block lexing landed in `bd4953b30` on 2026-07-18 and the doc was never
   updated. I stated the stale version as fact before checking the code.

Git history alone doesn't fix this. A commit message explains a diff; it doesn't say
"this line looks strange on purpose, here is the constraint that forces it."

## What changed

All new, all fork-owned. Upstream has never had `fork/`, `changelog/`, `CHANGELOG.md`
or `docs/changelog` — checked with `git log --all --diff-filter=A` before choosing
the path, so none of this can ever conflict on an upstream merge.

- **`fork/changelog/README.md`** — why the folder exists, when an entry is required
  and when it isn't, how to name one, and the hard rules. Ends with the index table
  that agents read first.

- **`fork/changelog/TEMPLATE.md`** — the skeleton. Sections: Why, What changed,
  Verified, Risk / watch for, Follow-ups. "N/A" is allowed; blank is not.

- **`fork/changelog/entries/`** — one file per change, named
  `YYYY-MM-DD-NN-slug.md`. The two-digit sequence makes same-day ordering
  unambiguous, which matters because several changes a day is normal here.

- **`AGENTS.md`** — the `FORK-RULES` block gains two rules: log every change here and
  commit the entry with the code, and read the index at the start of a task before
  changing files. Still one contiguous block, still additions only.

### Why individual files instead of one `CHANGELOG.md`

A single file would conflict with itself constantly — every branch appends to the
same region, so every merge of two fork branches collides. Separate files never
collide. It also means an agent can read one entry instead of scrolling a growing
document, and the filename alone carries the date and subject.

### The hard rules, and where each came from

Each of these is in the README because it already cost time in this fork:

- **Append-only; corrections go in a new entry.** Rewriting an entry leaves a
  confident, clean, wrong document with no trace that it was ever corrected.
- **Never quote raw line counts.** The same `+N/-M` figure went stale three times in
  one session, each time inside the commit that was fixing the previous stale number
  — once because adding `flush=True` wrapped a print onto a second line. Describe the
  shape, give the command.
- **Measure from the merge base, never two-dot against `upstream/main`.** A two-dot
  diff attributes upstream's newer commits to the fork. Measured while 66 commits
  behind, it reported 111 changed files against a real 21.
- **Separate verified from assumed.** An audit this session found a claim of "your
  customizations are intact" printed on a path where the tree state was genuinely
  unknown.
- **Name the mechanism, not just the file.** A line number tells a future agent
  nothing about what to preserve.
- **Correct upstream when it's wrong.** Checked-in docs are trusted by default.

## Verified

Path availability, before choosing `fork/changelog/`:

```bash
git log --all --oneline --diff-filter=A -- "fork/*" "changelog/*" "CHANGELOG*"
# -> only commits from windro's own archived fork branch (fork-archive-20260727);
#    never upstream
```

`AGENTS.md` still removable with no residue after the new rules were added:

```bash
python -c "... strip FORK-RULES block, compare to upstream/main:AGENTS.md ..."
# -> matches upstream/main exactly: True   (block is 65 lines)
```

`AGENTS.md` diff is additions only, zero deletions:

```bash
git diff --numstat AGENTS.md   # -> 18  0
```

**Not verified:** that agents will actually comply. This is documentation — it
depends on the agent reading and following it. No mechanism enforces it; a CI check
that fails when a code commit has no entry would, and does not exist.

## Risk / watch for

- **The index is maintained by hand.** An agent that writes an entry and forgets the
  index row makes the entry nearly undiscoverable, since the index is what gets read
  first. If entries and index drift, trust `ls entries/`.
- **Entries can rot like any doc.** They describe a moment. An entry that says a
  patch lives at some file is wrong the moment upstream renames it. Hence the rule
  about naming mechanisms rather than only locations.
- **This folder is dev-phase scaffolding.** windro plans to remove it later. Deleting
  `fork/` and the `FORK-RULES` block restores stock exactly — but it also deletes the
  reasoning behind every fork patch still in the tree. Worth exporting before then.

## Follow-ups

- No enforcement. A pre-commit hook or CI job that requires a `fork/changelog/entries/`
  file whenever tracked code changes would turn this from a convention into a rule.
  Not built — it would fire on every upstream merge commit too, so it needs a carve-out.
- The GitHub About/description for the repo is still upstream's text. Not in git, so
  it cannot conflict, and it is the first thing seen on the repo page. `gh` is not
  installed here.
