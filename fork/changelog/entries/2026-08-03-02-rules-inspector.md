# You can now see which project rules the agent is actually running on

**Date:** 2026-08-03
**Type:** Added
**Branch:** `main`

<!-- Commit sha omitted: ships in the commit it describes. Find it with:
     git log --oneline -- <path to this file> -->

## Why

windro saved a rule, asked the agent about it, and got nothing. The rule was on
disk and correct. The cause turned out to be that the session's prompt had been
built before the file was saved (fixed separately in
`2026-07-31-01-rules-mid-session-and-rule-tool.md`), but the reason it took a
debugging session to establish is the problem this entry addresses:

**there was no way to look.** The context panel had a "Rules" row showing a token
count, and a token count answers "how much", never "did my rule land". Those are
different questions, and only the second one comes up when a rule appears to be
ignored.

Worse, four distinct causes were indistinguishable from outside:

| what's wrong | what you saw |
|---|---|
| rule switched off | a token count |
| rule is path-scoped (parsed, not honoured yet) | a token count |
| a different project folder was resolved | a token count |
| the session's prompt predates the file | a token count |

And the panel itself was unreachable when you most needed it — see below.

## What changed

### The Rules row expands

- **`agent/context_breakdown.py`** — new `project_rules_detail(prompt_fingerprint)`
  returning the resolved `cwd`, the `.hermes/rules` directory in use, whether an
  `IDEA.md` was found, a **flat list** of every rule with a state of `live` /
  `off` / `scoped`, and a `stale` flag. `compute_session_context_breakdown`
  returns it as `rules_detail`.

  `off` and `scoped` are deliberately separate: one is a toggle you can flip, the
  other is a feature that does not exist yet. Collapsing them would send you
  hunting for a switch that would not help.

- **`apps/desktop/src/app/shell/context-usage-panel.tsx`** — the Rules row gets a
  show/hide toggle listing the rules, non-live ones struck through and labelled,
  plus a `changed` badge when the running prompt is behind the files on disk.

- **`apps/desktop/src/types/hermes.ts`** — `ProjectRuleDetail`,
  `ProjectRulesDetail`, and `ContextBreakdown.rules_detail`.

Rules are **one flat list, not grouped by file.** windro asked why multiple `.md`
files were needed at all, and he was right: a single `rules.md` is the intended
shape for a solo project. Splitting only pays for path-scoping (unimplemented),
group toggles, or team merge conflicts. The UI no longer implies otherwise.

### Inspection no longer eats the staleness signal

`compute_session_context_breakdown` calls `build_system_prompt_parts` to measure
the prompt, and that call **records** a project-files fingerprint. Left alone,
merely opening the panel would have marked the rules fresh and suppressed the
rebuild the next turn was going to do — an inspection with a side effect, and the
side effect is "your rule silently stops arriving".

So the fingerprint is snapshotted before the rebuild and restored after. This bug
would have been invisible in manual testing and is guarded explicitly.

### The panel is reachable before you send a prompt

- **`apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx`** — the
  `context-usage` status item was `hidden: !contextUsage`, and that label is
  empty until a session reports token usage (`context_used/context_max`, or a
  total above zero). So the one surface that shows what went into your prompt was
  invisible until *after* you had already sent one — exactly backwards for
  checking whether a rule is live.

  Now it is `hidden: !activeSessionId`, with an idle label (`context`) when there
  are no numbers yet. The panel fetches its own breakdown, so it has something to
  show immediately.

## Verified

```bash
venv/Scripts/python.exe -m pytest tests/fork/test_context_breakdown_rules.py -q
# -> 16 passed
venv/Scripts/python.exe -m pytest tests/fork/ -q
# -> 111 passed

cd apps/desktop
npm run typecheck                                  # -> clean (3 tsconfigs)
npx vitest run --project ui src/__fork__/          # -> 62 passed
npx vitest run --project ui                        # -> 2597 passed, 1 failed
```

That one full-suite failure is **pre-existing and unrelated**:
`use-prompt-actions/utils.test.ts` expects `1,234,567` but this machine's locale
is en-IN, so `toLocaleString` produces `12,34,567` (lakh grouping). It fails
identically with and without these changes.

Mutation-checked, backend:

| mutation | result |
|---|---|
| scoped rules reported as `off` | 2 failed |
| staleness never reported | 2 failed |
| fingerprint not restored (inspection eats the signal) | 1 failed |

Mutation-checked, frontend:

| mutation | result |
|---|---|
| stale badge removed | 1 failed |
| `scoped` and `off` rendered identically | 1 failed |

**Not verified:** none of this has been seen in the packaged app — it needs a
rebuild. The staleness badge in particular has only been exercised through unit
tests, not by saving a rule mid-session and watching the badge appear.

## Risk / watch for

- **The panel's token counts describe a fresh build, not the running prompt.**
  `compute_session_context_breakdown` rebuilds the parts to measure them, so the
  Rules count reflects disk *now*. That was already true before this change; the
  `changed` badge is what tells you the two differ.
- **The rules inspector is English-only.** Its strings are optional in
  `Translations` so the fork did not have to translate them into five locales;
  the component merges over an English fallback. Same for `contextUsageIdle`.
  Make them required once other locales carry them.
- **Every non-empty line becomes a rule row**, so a rule file with prose or
  headings will show those as rules. Fine for the bullet-list shape the tool
  writes; worth revisiting if someone hand-writes paragraphs.
- **`hidden: !activeSessionId`** means the item now appears in states that
  previously had no status entry. If a surface has an active session but no
  gateway, the panel will show its empty state rather than nothing at all.

## Follow-ups

- The `changed` badge tells you the prompt is behind but cannot refresh it — the
  rebuild happens on the next message. A "reload rules now" affordance would
  close that loop.
- `project_rules_detail` re-reads and re-parses every rule file on each call.
  Irrelevant at this scale (a handful of small files), but it is called every time
  the panel opens.
