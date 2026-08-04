<!-- Temporary dev-phase file for windro's fork. Safe to delete with the rest of fork/. -->

# Fork changelog

Every change this fork makes to Hermes gets one file in `entries/`. This folder is
the fork's memory. When a future agent asks "why is this line different from
upstream?", the answer must be findable here without reading git history or
guessing.

Read this whole file before writing an entry. Read the index before changing code.

---

## Why this exists

This fork edits a codebase that upstream is actively rewriting — hundreds of commits
a week. Three failures happen without a log like this:

1. **An agent reverts a fix it doesn't understand.** It sees an odd-looking line,
   decides it's a mistake or a merge artifact, and "cleans it up." The fix silently
   disappears. An entry explaining *why* the line is odd prevents this.
2. **An agent re-fixes something already fixed.** It reads a stale upstream doc,
   believes a problem is open, and rebuilds a solution that already exists. This has
   already happened here: upstream's `apps/desktop/scripts/profile-typing-lag.md`
   describes the markdown re-parse as unfixed, but the fix landed in `bd4953b30`.
3. **A merge conflict gets resolved wrong.** Resolving a conflict in a fork patch
   requires knowing what the patch was *for*. Without that, the resolver keeps
   whichever side looks tidier.

## When to write an entry

Write one for anything that changes behavior, performance, or structure:

- a bug fix
- a feature or new file
- a performance change
- a dependency change
- a config or build change
- a rule change in `AGENTS.md`
- **a correction to a previous entry** (as a new entry — see Append-only below)

Skip it for: typo fixes in comments, formatting, and anything with no behavioral or
structural effect. If you're unsure, write one. A useless entry costs a minute; a
missing one costs an afternoon of archaeology.

## How to write one

1. Copy `TEMPLATE.md` into `entries/`.
2. Name it `YYYY-MM-DD-NN-short-slug.md` — date, a two-digit sequence for that day,
   then a slug that says what happened. Example:
   `2026-07-27-01-agent-fork-rules.md`. The sequence makes same-day ordering
   unambiguous.
3. Fill every section. "N/A" is an acceptable answer; a blank is not.
4. Add a row to the index table at the bottom of this file, newest first.
5. Commit the entry **in the same commit as the code change**. An entry added later
   is an entry that describes what you remember, not what you did.

---

## Hard rules

These come from mistakes already made in this fork. Each one cost real time.

### Append-only. Never rewrite an entry.

If an entry turns out to be wrong, write a **new** entry that corrects it, and mark
the old one `Superseded by <file>` in the index. Rewriting history means a future
agent reads a confident, clean, wrong document with no trace of the correction.

### Never quote raw line counts.

Do not write `+23/-0` or "changed 47 lines." Those numbers go stale on the next
commit that touches the file. In this repo the same figure went stale **three
times, each time inside the commit that was fixing the previous stale number.**

Instead describe the **shape** — "one added block, no removals" — and give the
command:

```bash
git diff $(git merge-base upstream/main HEAD)..HEAD --numstat -- path/to/file
```

Always measure from the **merge base**, never a two-dot diff against
`upstream/main`. A two-dot diff attributes upstream's own newer commits to the fork.
Measured while 66 commits behind, it reported 111 changed files instead of the real
21.

### Separate what you verified from what you believe.

Write the command you ran and the result you got. If you did not run it, say so.
"Should be faster" is not a finding. Acceptable:

> `venv/Scripts/python.exe -m pytest tests/fork/ -q` → 52 passed.
> Did not measure the renderer; would need a CPU profile.

### Name the mechanism, not just the file.

`markdown-text.tsx:58` tells a future agent nothing. "the preprocess pass ran over
the whole message on every flush, ~30 times a second" tells them what to preserve
and what to look for if it regresses.

### Record what to watch for.

Every change has a way of coming back. Write it down: the edge case you didn't
cover, the upstream file that would break this if it moves, the assumption that
holds today.

### Correct upstream when it's wrong.

If an upstream doc or comment is stale or false, say so in the entry, with evidence.
Future agents trust checked-in docs by default. Someone has to leave the note.

---

## Index

Newest first. `Superseded by` means read the newer entry instead.

| Date | Type | Entry | Status |
|---|---|---|---|
| 2026-08-04 | Fixed | [A restructured transcript could kill the thread render permanently](entries/2026-08-04-02-thread-sync-hardening.md) | current |
| 2026-08-04 | Fixed | [One session rendered under two lanes (branch lane id)](entries/2026-08-04-01-branch-lane-id.md) | current |
| 2026-08-03 | Added | [See which project rules the agent is actually running on](entries/2026-08-03-02-rules-inspector.md) | current |
| 2026-08-03 | Fixed | [A toast pushed off screen never learned it was gone](entries/2026-08-03-01-notification-eviction.md) | current |
| 2026-08-02 | Fixed | [`hermes update` no longer resets away unpushed commits](entries/2026-08-02-03-update-unpushed-guard.md) | current |
| 2026-08-02 | Fixed | [Update toast fired, then a routine action silently dismissed it](entries/2026-08-02-02-update-toast-dismissal-reason.md) | current |
| 2026-08-02 | Fixed | [Update toast fired and was silently dismissed](entries/2026-08-02-01-update-toast-invisible.md) | superseded by 02 |
| 2026-08-02 | Changed | [Installer points at the fork, so pushing is the update mechanism](entries/2026-08-02-01-installer-points-at-fork.md) | current |
| 2026-07-31 | Fixed | [Project rules were read once per session; added the project_rule tool](entries/2026-07-31-01-rules-mid-session-and-rule-tool.md) | current |
| 2026-07-29 | Added | [Project rules and IDEA.md, editable entirely in the UI](entries/2026-07-29-02-project-rules-ui.md) | current |
| 2026-07-29 | Added | [Per-project rules directory and IDEA.md loading](entries/2026-07-29-01-project-rules-loader.md) | current |
| 2026-07-29 | Fixed | [Composer glass was invisible — dark-on-dark needs a brightness lift](entries/2026-07-29-02-composer-glass-visibility.md) | current |
| 2026-07-29 | Performance | [Scroll jank: timeline tracker measured every message every frame (21 -> 44 fps)](entries/2026-07-29-01-timeline-scroll-offsets.md) | current |
| 2026-07-28 | Changed | [Composer glass: less transparent by default, user-adjustable lever](entries/2026-07-28-02-composer-glass-setting.md) | corrected by 2026-07-29-02 |
| 2026-07-28 | Performance | [Artifacts page: column projection instead of 30 full transcripts](entries/2026-07-28-01-artifacts-field-projection.md) | current |
| 2026-07-27 | Performance | [Scroll jank fixed: patch-package + use-stick-to-bottom getComputedStyle removal](entries/2026-07-27-10-scroll-patch-applied.md) | current |
| 2026-07-27 | Docs | [Scroll-library getComputedStyle — real, blocked on a patch-package decision](entries/2026-07-27-09-scroll-getcomputedstyle-investigation.md) | superseded by 10 |
| 2026-07-27 | Docs | [Bundle splitting investigated — no change, wrong optimization for local-disk Electron](entries/2026-07-27-08-bundle-investigation.md) | current |
| 2026-07-27 | Docs | [Session-load path investigated — no change, claims already done or unsafe](entries/2026-07-27-07-session-load-investigation.md) | current (artifacts half superseded by 2026-07-28-01) |
| 2026-07-27 | Performance | [Code-card glow: opacity cross-fade instead of box-shadow](entries/2026-07-27-06-code-card-glow.md) | current |
| 2026-07-27 | Performance | [Streaming renderer: visibleGroups + content-text memo (three claims rejected)](entries/2026-07-27-05-streaming-renderer.md) | current |
| 2026-07-27 | Performance | [Model picker: memoize credential-pool reads, warm caches at startup](entries/2026-07-27-04-model-picker-latency.md) | current |
| 2026-07-27 | Performance | [Gateway RPC latency: pool session.history, compact project-tree rows](entries/2026-07-27-03-gateway-rpc-latency.md) | current |
| 2026-07-27 | Added | [This changelog folder](entries/2026-07-27-02-fork-changelog-folder.md) | current |
| 2026-07-27 | Added | [Agent-facing fork rules](entries/2026-07-27-01-agent-fork-rules.md) | current |
