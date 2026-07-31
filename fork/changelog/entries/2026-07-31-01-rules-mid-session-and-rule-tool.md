# Project rules were read once per session, so mid-session edits never landed

**Date:** 2026-07-31
**Type:** Fixed
**Branch:** `feat/project-rules`

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

windro added `.hermes/rules/rules.md` containing `- you are ochumaa its your name
nothign else` to a project, asked the agent its name, and got "Hermes". Asked what
its rules were, it said "I can't discuss that." Asked to add a rule, its reasoning
showed it planning to write a **memory** entry. Three separate defects.

### The rules were genuinely not in the prompt

Not a loader bug — `build_system_prompt`'s own docstring:

> Called **once per session** (cached on `agent._cached_system_prompt`) and only
> rebuilt after context compression events. ... Hermes never rebuilds or
> reinjects parts of it mid-session, which is the only way to keep upstream
> prompt caches warm across turns.

So project files are read exactly once, when the session's prompt is first built.
`IDEA.md` existed at that moment and the agent knew it; `rules.md` was added
afterwards and was invisible for the rest of the session.

This is the failure mode the earlier research flagged as the most-reported bug in
every tool that ships rules files. The previous entry checked that *compaction*
preserves the prompt, concluded rules were safe, and never tested an edit made
while a session was open. That was the wrong test.

### It reached for memory because rules weren't writable

Nothing told it project rules were files it could edit. It had `memory` and
generic file tools, so "add a rule" resolved to memory — which is private to the
agent and invisible in the repo, the opposite of what was asked.

### It refused to say what its rules were

The rules arrive inside the system prompt, and models are trained to deflect
questions about that. The refusal string does not appear anywhere in Hermes,
`SOUL.md`, or the proxy directory — it is applied above this codebase. Rather
than fight it, the fix removes the reason to deflect: the rules are readable as
ordinary project files, and the prompt now says so.

## What changed

- **`agent/prompt_builder.py`** — new `project_files_fingerprint(cwd)`: stats
  `.hermes/rules/*.md` and `IDEA.md` into a `name:mtime_ns:size|…` string.
  Detects add, edit and delete; stable when nothing changes; `""` with no cwd.

- **`agent/system_prompt.py`** — records that fingerprint on the agent when the
  prompt is built, and adds `refresh_project_files_if_changed(agent)`, which
  compares and calls `invalidate_system_prompt` on a difference. Returns `False`
  when no prompt has been built yet, so a fresh agent is never touched.

- **`agent/turn_context.py`** — calls it immediately before the cached-prompt
  check, so an edited rule takes effect on the very next message.

  The cost is deliberate and bounded: a handful of `stat` calls per turn, and one
  prefix-cache miss **per edit** rather than per turn. Prompt caching is treated
  as sacred in this codebase and this is the one justified exception — a file
  whose entire purpose is to steer this session cannot be ignored until the next
  one.

- **`tools/project_rule_tool.py`** (new) — a `project_rule` tool with
  `list` / `add` / `remove`. Registered into `_HERMES_CORE_TOOLS` **next to
  `memory`**, because the model only chooses correctly if it can see both, and
  the description contrasts them explicitly. `list` reads the files from disk,
  which is what makes "what are my rules?" answerable: it is a project-file read,
  not a request to recite the system prompt. Writes go through the same
  `_find_project_rules_dir` the loader uses, so tool and loader cannot disagree
  about which directory is in play.

- **`agent/prompt_builder.py`** (rules header) — now states the rules are
  windro's own files and **not confidential**, that he should be told plainly and
  quoted when he asks, and names `project_rule` as the way to change them in
  preference to `memory`.

## Verified

```bash
venv/Scripts/python.exe -m pytest tests/fork/ -q -p no:randomly   # -> 73 passed
```

`tests/fork/test_project_rules.py` grew from 29 to 53 tests. Four mutations, each
caught by exactly one test:

```
refresh never invalidates        -> 1 failed (test_edit_invalidates_the_cached_prompt)
turn path stops calling refresh  -> 1 failed (test_the_turn_path_calls_the_refresh)
tool dropped from core toolset   -> 1 failed (test_registered_alongside_memory)
header drops licensing text      -> 1 failed (test_header_says_the_rules_are_not_confidential)
```

End-to-end, against a real temp project: the agent adds a rule through the tool →
it appears in the prompt; a stub agent whose prompt was built *before* the edit
has its cache invalidated; `list` returns both rules; the header contains the
precedence, licensing and tool-pointer text.

Regression check on the touched areas, before and after, failure lists compared:

```bash
venv/Scripts/python.exe -m pytest tests/test_toolsets.py \
  tests/test_toolset_distributions.py tests/agent/test_system_prompt.py \
  tests/agent/test_system_prompt_restore.py tests/agent/test_turn_context.py \
  -q -p no:randomly
# baseline: 1 failed, 90 passed
# after:    1 failed, 90 passed   (same test, pre-existing)
```

**Not verified:**
- No live end-to-end run in the app. The gateway must be restarted and the
  desktop binary rebuilt before windro can see any of this; the packaged
  `Hermes.exe` predates all of it.
- Whether the refusal is genuinely provider-side is still an **inference**. The
  string is absent from this repo, `SOUL.md` and `~/.kiro-proxy`, but the
  decisive test — one session on a non-kiro provider — has not been run. The
  header change may make it moot by removing the reason to deflect.
- The mid-session refresh is proven by unit test, not by watching a real session
  pick up an edited rule.

## Risk / watch for

- **One extra cache miss per rule edit.** Intended. If rules are edited very
  frequently in one session, that is a series of full prompt rebuilds — visible
  as slower first tokens on those turns, not as an error.
- **The fingerprint is mtime+size.** An edit that preserves both (rewriting a
  file with identical length within the same nanosecond) would not be detected.
  Not achievable by hand or by the GUI, but worth knowing.
- **`stat` calls per turn** scale with the number of rule files, capped at 32 by
  the loader.
- **`project_rule` writes into the project folder.** It refuses filenames
  containing separators or leading dots, so it cannot escape `.hermes/rules`, but
  it does create that directory on first `add`.

## Follow-ups

- Run the non-kiro provider test and settle the refusal question.
- The GUI dialog does not yet show which rules are *currently in force* for the
  open session — with the mid-session refresh in place it now could, and that is
  the "what loaded" inspection view the research recommends.
- `SOUL.md`, project rules, `IDEA.md` and memory are still four independent
  prompt blocks with no stated relationship to each other. The rules header now
  claims precedence over persona, which is a start, but a coherent layering
  (what wins, what the agent may amend, and how it should describe itself) is
  still unbuilt — this is windro's "should feel seamless" ask.
