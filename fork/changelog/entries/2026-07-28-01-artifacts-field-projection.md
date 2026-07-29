# Artifacts page: fetch a column projection instead of 30 full transcripts

**Date:** 2026-07-28
**Type:** Performance
**Branch:** `perf/ui-latency`

**Supersedes the artifacts half of**
`entries/2026-07-27-07-session-load-investigation.md`, which listed this as a
follow-up and assumed the fix was a server-side artifact endpoint. It isn't — see
below. windro asked "is that the proper and efficient fix"; the answer was no, so
this took the other route.

<!-- Commit sha omitted: ships in the commit it describes. -->

## Why

`src/app/artifacts/index.tsx` scans up to 30 sessions for artifacts:

```ts
Promise.allSettled(sessions.map(s => getSessionMessages(s.id, s.profile)))
```

Each of those was a full transcript over REST — every column of every message.
On windro's data one session alone is 878 messages / 1.8MB, so opening the
Artifacts page pulled a large fraction of the whole database.

**Why not a server-side artifacts endpoint** (the original plan): artifact
detection is a stack of TypeScript heuristics — `collectArtifactsFromMessage`,
`collectStringValues`, `looksLikeArtifact`, `looksLikePathOrUrl`, `KEY_HINT_RE`,
`FILE_EXT_RE`, `parseMaybeJson`, `artifactKind` / `artifactHref` /
`artifactLabel`. Reimplementing that in Python would duplicate the logic in two
languages and guarantee divergence — the exact trap this codebase warns about
elsewhere (`build_aux_picker_rows`' docstring is about a version of the same
mistake). Detection stays single-sourced in TS.

What the extractor actually reads is small: message text (`content`),
`tool_calls`, `role`, and `timestamp`. Everything else in the row —
`reasoning`, `reasoning_content`, `reasoning_details`, `api_content`,
`codex_*_items`, `display_metadata`, token counts — is dead weight for this scan.

## What changed

- **`hermes_state.py`** — `get_messages` takes an optional `fields` list. The
  names are whitelisted against the real `messages` columns (parsed from
  `SCHEMA_SQL`, cached in a classvar), so a caller-supplied name can never be
  interpolated into SQL; unknown names are dropped. `id` and `session_id` are
  always carried for stable identity. If nothing valid remains it falls back to
  `SELECT *` rather than returning an unusable row. **`fields=None` (every
  existing caller) is byte-for-byte the old behaviour.**

- **`hermes_cli/web_server.py`** — `GET /api/sessions/{id}/messages` accepts a
  comma-separated `fields` query param and passes it through. Omitted = all
  columns, so the resume prefetch is untouched.

- **`apps/desktop/src/hermes.ts`** — `getSessionMessages(id, profile?, fields?)`.
  Switched to `URLSearchParams` (the old manual `?profile=` concat couldn't carry
  a second param).

- **`apps/desktop/src/app/artifacts/artifact-utils.ts`** — exports
  `ARTIFACT_MESSAGE_FIELDS = ['role', 'content', 'tool_calls', 'timestamp']`,
  deliberately next to the extractor: if the extractor starts reading another
  column it must be added here, or the value silently arrives `undefined`.

- **`apps/desktop/src/app/artifacts/index.tsx`** — passes it.

## Verified

Measured on a copy of windro's real `state.db`, largest session (878 messages):

```
messages:            full=878  projected=878        (identical count)
keys per message:    23 -> 6   (content, tool_calls, role, timestamp, id, session_id)
payload:             1803 KB -> 1411 KB             (22% smaller)
```

Safety, on the same real data:

```
fields=['role','bogus','content; DROP TABLE messages']  -> keys: id, role, session_id
  (unknown + injection attempt dropped; messages table still readable afterwards)
fields=['nope']                                          -> falls back to all 23 keys
content decoded to str, tool_calls decoded to list       -> projection keeps decoding
```

```bash
venv/Scripts/python.exe -m pytest tests/test_hermes_state.py \
    tests/hermes_cli/test_web_server.py -q -p no:randomly -k "message or session"
# -> 333 passed, 1 skipped
cd apps/desktop
npm run typecheck                                         # -> clean
npx vitest run --project ui src/app/artifacts/ src/hermes.test.ts   # -> 22 passed
npx vitest run --project ui src/__fork__/                 # -> 13 passed
venv/Scripts/python.exe -m pytest tests/fork/ -q          # -> 20 passed
```

Fork guards added both sides (5 Python: projection shape, default-returns-all,
injection dropped, all-unknown fallback, decoding preserved; 3 TS: the field list
is exactly the four columns, the page passes it, `getSessionMessages` still
defaults to all). Mutation-checked: disabling the projection fails 3 of the
Python guards.

**Not verified:**
- No end-to-end timing of the Artifacts page opening, before or after. The 22%
  payload cut is measured at the DB layer; the felt page-open improvement is
  inferred. It is also 22% per session across ~30 requests, not a 22% cut to a
  single wait.
- The 30 REST round-trips themselves are unchanged — this makes each one smaller,
  it does not reduce their number.

## Risk / watch for

- **The field list must track the extractor.** If `collectArtifactsFromMessage`
  or `messageText` starts reading a new column and `ARTIFACT_MESSAGE_FIELDS`
  isn't updated, that column arrives `undefined` and artifacts silently stop
  being detected — no error. The guard asserts the exact four-column list, so
  changing the extractor without changing the list will fail a test, but the
  guard can't know a *new* read was added. Comment at the constant says so.
- **`messageText`'s `message.text` fallback is not a DB column**, so it is
  correctly absent from the projection. If `text` ever becomes a real column it
  must be added.
- **`get_messages` is shared server-side infrastructure** (gateway resume,
  compression, audit). The projection is strictly opt-in and the default path is
  unchanged — that is what keeps this safe. Do not make `fields` default to
  anything.

## Follow-ups

- The 30 round-trips remain. A single `GET /api/artifacts/messages?sessions=…`
  returning the projected rows for many sessions in one response would cut request
  overhead without moving detection into Python. Worth it only if the page still
  feels slow after this.
- Role filtering is available for free at the SQL layer and not used: the
  extractor skips everything that isn't `assistant`/`tool`, so a `roles` param
  would cut the payload further. Deliberately left out to keep this change to one
  mechanism.
