# Session-load path: investigated, no change — the claims were already done or unsafe

**Date:** 2026-07-27
**Type:** Docs
**Branch:** `perf/ui-latency`

<!-- No code change. This entry exists so the four rejected claims are not
     re-chased. Find related commits with: git log --oneline -- <this file> -->

## Why

The perf plan listed four session-load fixes. All four were checked against the
live code and windro's real 13MB `state.db`. None resulted in a change: three
are already implemented upstream, and the fourth is unsafe as specified. Recorded
here because "re-fix something already fixed" is the exact failure this changelog
exists to prevent — and the resume path is dense with deliberate, commented
optimizations that look re-doable but aren't.

## The four claims

### "Limit `getSessionMessages`" — rejected, would truncate transcripts

The REST endpoint (`hermes_cli/web_server.py::get_session_messages`) already
accepts and clamps `limit` to 500. No desktop caller passes one — correctly. A
session **resume** needs the whole transcript to paint it; a limit would silently
drop the top of the conversation. The endpoint's `limit` exists for pagination by
choice, not as a default the callers forgot.

### "Drop `reasoning` / `api_content` columns" — rejected, the UI renders reasoning

Measured column bytes over 1733 real messages: `content` 61%, `tool_calls` 24%,
`reasoning` 6%, `reasoning_content` 6%, everything else ≤1% (`api_content` is
~20KB, 1%). The four big consumers are all read by the desktop `SessionMessage`
type (`src/types/hermes.ts`): `content`, `tool_calls`, `reasoning`,
`reasoning_content`, `reasoning_details`, `codex_reasoning_items`. Reasoning is
rendered as the thinking blocks — dropping it breaks resume. `api_content` is
unused by the desktop type but is 1% of payload, not worth a REST-only projection
that diverges from the shared `get_messages`.

Note `get_messages` is shared server-side infrastructure (`SELECT *`, many
callers: gateway resume, compression, audit). Narrowing its columns globally
would risk those paths for a ~2% payload win on this one.

`reasoning` and `reasoning_content` measured byte-identical (194.9KB each), which
smells like duplicate storage — but both are in the consumed type, and the
frontend's fallback logic between them is unknown, so collapsing them is a
backend data-model change, out of scope for a perf pass.

### "Stop awaiting both round trips" — rejected, already concurrent

`use-session-actions/index.ts` starts the REST prefetch (line 846) and the
`session.resume` RPC (line 848) **before awaiting either**, so they run
concurrently — wall time is `max(prefetch, resume)`, not the sum. The
sequential-looking `await prefetch` then `await resume` just collects two
already-in-flight promises. The code comment says exactly this.

Better: on a prefetch hit the resume payload conversion is **skipped entirely**
(lines 896-919), with a comment that on a 1000+-message session "that second
conversion plus the deep equivalence compare costs over a second of main-thread
time." The expensive half is already gone. There is nothing to fix here.

### "Artifacts N+1" — real, but out of scope for a safe local change

`src/app/artifacts/index.tsx:125` does
`Promise.allSettled(sessions.map(s => getSessionMessages(...)))` over up to 30
sessions — 30 full-transcript REST reads to scan for artifacts. On this data,
with sessions of 878 and 365 messages, that pulls a large fraction of the whole
DB every time the Artifacts page opens.

This one is genuine. But it is a **secondary page**, not the "session feels slow
to open" path windro reported, and the correct fix is a server-side
artifact-extraction endpoint (scan and return only artifact rows) rather than a
client limit — artifacts can appear anywhere in a transcript, so limiting or
paging would miss them. That is a feature addition, higher risk than its payoff
in this pass. Left for a dedicated change.

## Verified

- `get_session_messages` endpoint: reads `limit`, clamps to 500 — source.
- No desktop caller passes `limit` — grep of all `getSessionMessages(` sites.
- Column byte breakdown — `SUM(LENGTH(...))` over all 1733 messages on a copy of
  the real `state.db`.
- Desktop `SessionMessage` field set — `src/types/hermes.ts:489`.
- Concurrency and prefetch-hit skip — read `index.ts:838-919` directly, comments
  included.

**Not verified:** what actually makes a large session feel slow to open on this
machine, end to end. Not profiled in the running app. The evidence says it is not
these four REST/DB specifics; the likelier candidates are cold-load render/paint
(addressed in entries 05 and 06) and the initial transcript layout, none of which
this phase measured live.

## Follow-ups

- **Artifacts page N+1** is the one real inefficiency found. A
  `GET /api/artifacts?limit=N` that scans server-side and returns only artifact
  rows would replace 30 full-transcript reads with one bounded response. Worth
  doing as its own change.
- The `reasoning` / `reasoning_content` duplicate storage is worth an upstream
  question — if one is derivable from the other, the messages table is ~6%
  larger than it needs to be.
