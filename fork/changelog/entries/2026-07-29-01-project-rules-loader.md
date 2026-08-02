# Per-project rules directory and IDEA.md loading

**Date:** 2026-07-29
**Type:** Added
**Branch:** `feat/project-rules`

<!-- Commit sha omitted: ships in the commit it describes.
     Find it with: git log --oneline -- <path to this file> -->

## Why

Two gaps, one of them a dead feature.

**There was no way to give a project standing instructions.** "Use pnpm here",
"tests need the venv activated", "no auth on purpose" — you had to repeat them
every session. `HERMES.md` exists but is a single blob, and the loader treats it
as one of four mutually exclusive alternatives (see below).

**`IDEA.md` was write-only.** The desktop's new-project dialog has a free-text
idea box, starter pills, and a 🎲 generate button that calls the model. It writes
`IDEA.md` to the project folder on create. **Nothing has ever read it.** Every
reference in the repo is a write, an i18n placeholder advertising the file
(*"What's this project about? (saved to IDEA.md)"* in five languages), or a
starter template. Zero readers in Python. For a brand-new or empty project that
file is the only context that exists, and it was being thrown away.

### What the research changed

windro asked for a four-phase feature (rules, a generated `project.md`, an
interview, and a web-research pass). Two research passes cut most of it, and the
findings are the reason this entry is small:

- **[arXiv 2602.11988](https://arxiv.org/abs/2602.11988)** (ETH Zürich / SRI),
  *Evaluating AGENTS.md*, measured context files on SWE-bench with both
  LLM-generated and developer-written files: they "do not generally improve task
  success rates, while increasing inference cost by over 20% on average" — but
  with a split that decides this design. **Instructions are followed well;
  repository overviews are not helpful.** So a generated `project.md` describing
  the stack and directory layout was cut on evidence, not taste. Claude Code's
  own `/doctor` trim now removes exactly those sections and keeps conventions.
- **[Vercel's Next.js 16 evals](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)**:
  a docs *skill* scored 53% (identical to no docs) and **was never invoked in 56%
  of cases**, while an always-loaded 8 KB index scored 100%. Lazy loading that
  depends on the model deciding to load is unreliable. Glob gating is safer
  because the filesystem decides — but see the constraint below.
- **Every tool has moved from one file to a directory**: Cursor
  `.cursorrules` → `.cursor/rules/*.mdc`, Windsurf → `.devin/rules/`, Cline →
  `.clinerules/`, Copilot → `.github/instructions/`, and most recently Claude
  Code added `.claude/rules/`. Nobody has gone the other way. windro pushed back
  on my "one file" recommendation and was right.
- **The most-reported bug across all of them** is a rule that exists on disk and
  is silently never injected — Cursor shipped a regression in April 2026 where
  `alwaysApply: true` rules and `AGENTS.md` landed in a "requestable" bucket and
  were never applied. Staff confirmed it as a bug. Silence is the failure mode.

## What changed

- **`agent/prompt_builder.py`** — one block of new loaders inserted before
  `build_context_files_prompt`, plus a four-line additive change at the assembly
  point. No existing function modified.

  - `.hermes/rules/*.md` — every `.md` in the directory, sorted by name. The sort
    is load-bearing, not cosmetic: this text lands in the **cached prompt
    prefix**, so a filesystem-dependent order would produce a different prompt
    run to run and miss the cache every time.
  - `IDEA.md` / `idea.md` — loaded as intent, labelled *"what this project is
    for"*.
  - Discovery mirrors `_find_hermes_md`: cwd first, then parents up to the git
    root, and **with no git root only cwd is checked** — so a stray
    `.hermes/rules` in `/tmp` or a home directory can never be picked up.
  - Both go through `_scan_context_content`, the same prompt-injection scanner
    the other context files use, and `_truncate_content`, the same cap.
  - Frontmatter is parsed with `yaml` and **fails open**: a malformed header
    leaves the rule active rather than making it vanish. Cline documents the same
    choice, for the reason above — a rule that disappears without an error is the
    worst outcome.

  **Loaded additively, as their own section.** The existing chain
  (`HERMES.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules`) is first-match-wins,
  and I left it exactly alone. I had earlier called that a bug; the research says
  it is deliberate override precedence and opencode documents the same behaviour.
  So rules complement whichever file won rather than competing with it.

### v1 is always-on only, and the reason is structural

Glob-scoped rules are recognised and **skipped**, not silently promoted to
always-on — injecting a rule its author scoped to `*.test.ts` into every prompt
would be worse than not loading it. The skip is reported in the injected text
(*"N file-scoped rule(s) … not active yet"*) so it is never silent.

Glob gating cannot work here yet. The system prompt is built **before** the agent
reads anything, so on the first turn there is no file set to match against.
Evaluating globs later would mean rewriting the prompt mid-session, and these
files sit in the cached prefix — every re-injection would invalidate the whole
conversation's cache. Cursor and Copilot can glob-gate because they are IDE-based
and already know which files are open. A gateway agent does not.

The frontmatter keys are parsed and reserved (`mode`/`trigger`, and
`paths`/`globs`/`applyTo`, accepting both the array and comma-string spellings
the ecosystem has converged on) so turning gating on later is additive.

## Verified

Live behaviour, on a real temp project:

```
always-on style.md loaded      : True
glob-scoped testing.md SKIPPED : True
no-mode gotchas.md loaded      : True     (description-only => always-on)
IDEA.md loaded                 : True
scoped-skip note shown         : True
file order deterministic       : True     (gotchas.md before style.md)
```

`tests/fork/test_project_rules.py` — **29 tests, all passing**, and passing in a
different order too (no intra-file pollution). Mutation-checked three ways:

| mutation | result |
|---|---|
| rules never load | 14 failed / 15 passed |
| scoped rules treated as always-on | 3 failed / 26 passed |
| injection scanner bypassed | 1 failed / 28 passed |

Existing suites, baselined by stashing `agent/prompt_builder.py` and re-running:

```bash
venv/Scripts/python.exe -m pytest tests/agent/test_prompt_builder.py \
  tests/agent/test_system_prompt.py tests/agent/test_runtime_cwd.py \
  tests/hermes_cli/test_prompt_size.py -q -p no:randomly
# with change:  2 failed, 205 passed, 1 skipped
# baseline:     2 failed, 205 passed, 1 skipped   (identical)
```

Both failures are pre-existing and characterised:
`test_build_system_prompt_records_stable_prefix` passes in isolation and fails
only in the multi-file run (pollution), and
`test_coding_prompt_preserves_legacy_workspace_order` fails in isolation too — a
Windows path-separator bug, expecting `/hermes/pr` and getting `\hermes/pr`.

**Compaction is safe by construction.** `build_system_prompt_parts`
(`agent/system_prompt.py:490`) calls `build_context_files_prompt`, and
`agent/conversation_compression.py` either reuses `agent._cached_system_prompt`
or rebuilds via `agent._build_system_prompt`. Either way the rules section
survives, so the Cursor-class "rules dropped after compaction" bug does not
apply. **Not verified with a live compaction run** — this is from reading the
call paths.

**Inspection is mostly free.** `agent/context_breakdown.py` already has a
**"Rules"** category measuring the context-files text, surfaced by `/context`
(`cli.py:10775`), so the token cost of this section is already visible. The
loader also labels each file (`### style.md`) so the prompt itself shows which
rules loaded. A dedicated "what loaded and what was skipped" view is a follow-up.

**Also not verified:** no measurement of whether these rules actually change
agent behaviour on real tasks. The arXiv paper says instructions are followed;
that is borrowed evidence, not something measured on this fork.

## Risk / watch for

- **Every rule file is paid for in every session in that project**, and it sits
  in the cached prefix. That is the point (always-available beats
  lazily-maybe-loaded, per the Vercel data) but it means a bloated rules
  directory is a permanent tax. The 32-file cap and the char cap bound the
  damage; they do not make it free.
- **Editing a rule invalidates that project's prompt cache.** Unavoidable for
  anything injected. Worth knowing before tuning rules mid-session.
- **The skip note is the only signal for scoped rules.** If someone writes a
  glob-scoped rule expecting it to work, they get a line in the prompt saying it
  did not load — better than silence, but easy to miss. That is the argument for
  building the inspection view sooner rather than later.
- **`_scan_context_content` is the injection boundary.** A future refactor that
  moves rule loading outside it would open a hole the other context files are
  protected from. There is a guard test for exactly this.
- **Never `git init` a parent of pytest's `tmp_path`.** Doing so plants a git root
  at the pytest session root, which gives every later test in the run a git root
  and silently enables the parent-directory walk. My own test did this and cost a
  debugging detour; there is a comment in the file now.

## Follow-ups

- **GUI**: a rules list with a per-rule toggle and in-place editing (Cline's
  design). Toggling is the piece that makes a directory worth having before glob
  gating exists.
- **Journal**: `.hermes/journal/YYYY-MM-DD.md`, dated and timestamped, appended
  by the agent on request, **never injected**, queried by the user ("when did we
  do X"). Kept out of the prompt precisely because it grows without bound and
  every append would otherwise bust the cache.
- **A real inspection view** — "these rules loaded, these were skipped and why".
  Every mature implementation ships one because the failure mode is silence.
- **Glob gating**, if the prompt ever gains a cheap way to know the active file
  set without a mid-session rebuild.
- **Strip HTML comments before injection** — Claude Code does this, giving
  maintainers free annotations at zero token cost. Cheap, not done here.
