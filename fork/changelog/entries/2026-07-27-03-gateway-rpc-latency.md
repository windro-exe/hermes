# Two gateway RPC patches: stop blocking the reader thread and stop hauling dead blobs

**Date:** 2026-07-27
**Type:** Performance
**Branch:** `perf/ui-latency`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes, so any sha here would be a guess or go stale on amend/rebase.
     Find it with: git log --oneline -- <path to this file> -->

## Why

windro reports the desktop UI feeling laggy and slow to load. This entry covers
the two backend contributors that hold up UI requests. Both are cases where
upstream already established the right pattern and one call site missed it.

**`session.history` ran inline on the reader thread.** The gateway routes slow
RPCs onto a small thread pool via `_LONG_HANDLERS`, so a slow handler cannot
block the socket that also carries `prompt.submit` and `session.interrupt`. That
set contains `session.resume`, `session.list`, `session.branch`,
`session.compress`, `session.usage`, `session.active_list` — but not
`session.history`, which does comparable work.

Measured on windro's real 13MB `state.db` (against a copy, live DB untouched):

| session size | DB read | `_history_to_messages` | total |
|---|---|---|---|
| 878 messages | 22.4ms | 93.2ms | **115.6ms** |
| 365 messages | 5.5ms | 43.9ms | 49.4ms |

The conversion, not the query, is the cost — so it scales with transcript length
and no index can help. Inline that is ~116ms of head-of-line blocking per call.
Upstream's own comment on `session.active_list` notes the same handler class can
"stall for tens of seconds" under agent GIL pressure, which is the amplification
argument for pooling something that looks cheap in isolation.

**The project tree fetched every `system_prompt` and threw it away.**
`_project_tree_inputs` calls `list_sessions_rich` without `compact_rows=True`,
so the query selects `s.*`. `_project_tree_row` then projects to a small shape —
and its docstring says it "drops the heavy columns (system_prompt,
model_config, ...) so the tree payload stays lean". The intent was already lean;
only the query never got the memo. Every other list caller (`session.list`,
`session.most_recent`, the dashboard) passes the flag.

Measured on the same DB: 13 rows carry 390.9KB of `system_prompt` (largest
102.1KB alone); the project-tree query hauled 241.9KB for 5 rows. Time 1.09ms →
0.44ms. Small in absolute terms at this data size — the point is that it grows
with session count and runs on every sidebar refresh.

## What changed

- **`tui_gateway/server.py`** — two additions, no removals, no modifications.
  One entry added to the `_LONG_HANDLERS` frozenset; one keyword argument added
  to the existing `list_sessions_rich` call in `_project_tree_inputs`. Both
  carry a comment with the measurement, because a bare line looks arbitrary and
  is the kind of thing a future agent deletes while tidying.

- **`tests/fork/test_perf_ui_latency.py`** (new, fork-owned) — guards. Both
  patches are one line inside a very large upstream file, so an upstream merge
  can drop either with no error and no other test failing: the code still works,
  just slower. The guards also assert the *assumptions*, not just the patches —
  that the sibling handlers are still pooled, that `compact_rows` still excludes
  `system_prompt`, and that `_project_tree_row` still doesn't read it. If
  upstream changes any of those, the reasoning needs revisiting, not just the
  line.

### Rejected: `read_only=True` on cross-profile DB handles

Investigated and backed out. `_db_for_profile` opens a writable `SessionDB` for
non-launch profiles, and read-only opens measured meaningfully faster (1.86ms vs
4.66ms median for open+list, and the writable path spiked to 8.28ms).

Two reasons it was dropped:

1. **It does nothing for normal use.** `_db_for_profile` only constructs a
   dedicated handle when `_profile_home(profile)` is not None — i.e. only for
   cross-profile RPCs in app-global remote mode. For a single-profile install
   the function returns the shared `_get_db()` handle and never reaches that
   line.
2. **It broke three upstream tests.** `test_session_list_honors_params_profile_opens_profile_db`,
   `test_session_most_recent_honors_params_profile` and
   `test_session_delete_honors_params_profile_sessions_dir` monkeypatch
   `hermes_state.SessionDB` with fakes whose `__init__` takes only `db_path`, so
   *any* added keyword breaks them — including on the delete path, which was
   never meant to change. Fixing that means patching upstream test files, which
   is merge-conflict surface for a benefit windro cannot observe.

Also note `_profile_db` backs `session.delete` (`delete_session`,
`set_session_title`), so the flag could never be an unconditional default there.

### Rejected: index on `sessions(archived, message_count)`

The listing queries filter on `archived` and `message_count`. An index was
considered and dropped: windro's `state.db` holds **13 sessions and 1733
messages**. SQLite scans that in less time than it would take to consult an
index, and adding one means a schema change interacting with upstream's
declarative schema reconciliation for zero measurable gain. Revisit if session
count reaches the thousands.

## Verified

Same three test files before and after, `-p no:randomly` for determinism:

```bash
venv/Scripts/python.exe -m pytest tests/tui_gateway/ tests/test_tui_gateway_server.py \
    tests/test_hermes_state.py -q -p no:randomly
# baseline (patches stashed): 53 failed, 1386 passed
# with patches:               52 failed, 1386 passed
```

Failure lists diffed, not just counts. The only difference is one *fewer*
failure — `test_compute_host_phase1.py::test_append_log_record_single_write_lines`
passed on the later run. It is flaky, unrelated to these patches, and passes in
isolation.

**The 52 failures are pre-existing and environmental, not real.** They are test
pollution from shared module state in `tui_gateway.server`: every one of them
passes when run alone. Confirmed on
`test_session_list_honors_params_profile_opens_profile_db` — fails in the
full-file run, `1 passed in 2.85s` on its own, both before and after this change.

Fork guards, including a mutation check that they actually catch removal:

```bash
venv/Scripts/python.exe -m pytest tests/fork/ -q      # -> 5 passed
# with both patch lines stripped out of server.py     # -> 2 failed, 3 passed
```

**Not verified:** no end-to-end measurement of the UI. Nobody profiled the
renderer or timed a session open in the running app before and after. These are
measured backend costs removed from the request path; the felt improvement is
inferred, not observed. The `session.history` win in particular only shows up
when another RPC is queued behind it.

## Risk / watch for

- **`session.history` now runs on the pool**, so it no longer serializes against
  other RPCs on the reader thread. The handler reads session state via
  `_sess_nowait` and `_session_db`; it takes no locks and writes nothing, which
  is why this is safe. If a future change makes it mutate session state, pooling
  it becomes a concurrency question rather than a free win.
- **`compact_rows=True` omits `system_prompt`.** If `_project_tree_row` or
  anything downstream of `build_tree` ever starts reading it, the field will be
  `None` rather than raising — a silent wrong-value bug. The guard test watches
  for exactly this.
- **Line endings.** This repo uses LF. Editing `server.py` with Python's
  `write_text` on Windows silently rewrote all 18,526 lines to CRLF and turned a
  two-line diff into a whole-file diff. Caught and fixed; use `write_bytes`, or
  check `git diff --numstat` before committing.

## Follow-ups

- The 52 pre-existing failures in `tests/test_tui_gateway_server.py` are worth
  reporting upstream: they are order-dependent, not genuine. Not this fork's
  problem to fix, but they make the suite useless as a regression signal without
  a baseline diff every time.
- Remaining planned perf work, none of it done here: the model picker's ~4s open
  (a live provider probe per open plus a cache key that includes the session id),
  the session-open payload (an unbounded REST read carrying `reasoning` and
  `api_content`), the streaming preprocess that walks the whole message on every
  flush, the settled-text concat in `assistant-message.tsx`, the footnote case
  that disables incremental markdown parsing, three CSS effects that repaint
  every frame, the 28.5MB single-chunk bundle, and `use-stick-to-bottom` calling
  `getComputedStyle` on every wheel event and every programmatic scroll write.
