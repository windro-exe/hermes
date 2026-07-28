# Model picker: kill the repeated auth-store reads and warm the cache at startup

**Date:** 2026-07-27
**Type:** Performance
**Branch:** `perf/ui-latency`

<!-- Commit sha intentionally omitted: this entry ships in the commit it
     describes, so any sha here would be a guess or go stale on amend/rebase.
     Find it with: git log --oneline -- <path to this file> -->

## Why

windro reports the model picker taking around 4 seconds to open. This entry is
mostly a correction: the hypothesis going in was wrong, and profiling said so.

**The hypothesis was a network probe.** `build_model_options_payload` passes
`probe_current_custom_provider=not refresh`, so every normal picker open probes
the current custom endpoint live, and that probe went to the network with no
cache. On this machine the active provider points at a local proxy that relays to
a remote API, so that looked like the answer.

**It isn't, for this config.** Profiled with `cProfile` against the real config,
the payload build makes **no network call at all**. The active provider is
`kiro`, a registry provider whose 20 models come from a curated list, so the
custom-endpoint branch is never reached. Where the ~560ms actually went:

| cost | detail |
|---|---|
| ~282ms | module imports — 303 modules, first call only (`httpx` 128ms, `vertex_adapter` 126ms, `hermes_cli.auth` 140ms) |
| ~193ms | `_credential_pool_is_usable`, called **78 times** → `load_pool` → 80 `read_credential_pool` → **90 `_load_global_auth_store`** |
| ~120ms | 3397 `nt.stat` calls |

The 78 lookups covered only **41 distinct providers** — 37 were pure repeats —
and every single one re-read and re-parsed `auth.json` from disk. Nothing cached
it.

## What changed

### The real fix: memoize the credential-pool check per build

- **`hermes_cli/model_switch.py`** — `_credential_pool_is_usable` now consults a
  thread-local memo, and `list_authenticated_providers` resets that memo at the
  top of each build.

  Scoped to one payload build on purpose. A build cannot observe its own auth
  state changing mid-flight, so within a build the memo is always correct; and
  because nothing survives the build, an `auth add` or a newly exhausted
  credential is visible on the very next picker open. Thread-local so concurrent
  builds on the gateway's RPC pool cannot clear or read each other's memo.

  Result: `load_pool` calls 78 → 41 (exactly the distinct count — every repeat
  eliminated), warm payload build **160ms → 92ms**.

### Warm the cache before the user asks

The first payload build in a process pays the import cost. Both entry points now
start the existing `prewarm_picker_cache_async` during startup idle time, the
same way `cli.py` already did after printing its banner:

- **`tui_gateway/entry.py`** — after `gateway.ready` is written, before the read
  loop. Covers the spawned stdio gateway.
- **`hermes_cli/web_server.py`** — in the FastAPI lifespan, in an executor.
  Covers `GET /api/model/options`, the REST mirror the Desktop model pill and
  picker use. This was added after checking that `web_server` can run an
  in-process gateway rather than spawning `tui_gateway.entry`, which would have
  left the desktop path unwarmed.

- **`hermes_cli/model_switch.py`** — `prewarm_picker_cache_async` now passes
  `probe_current_custom_provider=True`. It defaulted to `False`, so a user whose
  active provider *is* a custom endpoint got no benefit from the prewarm at all:
  the first real picker open still paid the live probe. `probe_custom_providers`
  stays `False`, so offline saved endpoints are still left alone.

### The custom-endpoint cache (does not affect this machine)

- **`hermes_cli/models.py`** — new `cached_api_models()`, a disk-cached wrapper
  around `fetch_api_models` keyed by endpoint URL. `cached_provider_model_ids`
  keys on a registry slug, and a custom endpoint has no slug, so it gets a
  `custom::` namespace inside the same `provider_models_cache.json`, same 1h TTL,
  same stale-beats-empty fallback, with the api key folded into the fingerprint
  so a rotated credential invalidates.

- **`hermes_cli/model_switch.py`** — the custom-endpoint probe branch calls it
  instead of `fetch_api_models`, passing `force_refresh=refresh` so an explicit
  refresh still hits the network.

  **Stated plainly: this changes nothing for windro's current setup**, since
  `kiro` is a registry provider and that branch never runs. It is kept because
  the gap is real and verified — that path made an uncached network call on every
  picker open — and it is the fix he would need the moment he points the picker
  at a bare `base_url`.

### Rejected: dropping `sessionId` from the frontend query key

`modelOptionsQueryKey(profile, sessionId)` includes the session id, and the plan
was to remove it so sessions shared one cache entry. That would be a correctness
bug: the `model.options` handler builds its context with
`_model_picker_context(agent)`, deliberately layering the live agent's
provider/model over disk config, so the payload genuinely differs per session.
Sharing an entry would show the wrong current model.

It is also unnecessary. `src/lib/query-client.ts` already sets a global
`staleTime: 60_000` with `refetchOnWindowFocus: false`, so remounts inside a
session do not refetch. And `gateway-event.ts` explicitly invalidates this key
when the session's model changes, which still works over a `staleTime`.

## Verified

Payload output is unchanged by the memo — full deep comparison of the serialized
payload with the memo on and off, across three flag combinations:

```
default                identical=True   memo-off  158.3ms  memo-on   92.7ms  (1.71x)
include_unconfigured   identical=True   memo-off  161.2ms  memo-on   91.4ms  (1.76x)
explicit_only          identical=True   memo-off  160.9ms  memo-on   91.8ms  (1.75x)
```

`load_pool` call count instrumented directly: **78 → 41**, distinct providers 41.

Cold vs warm in a fresh process, real config: first build 1144ms, subsequent
160ms (now 92ms) — the difference being imports and in-process warming, which is
what the startup prewarm now absorbs. The prewarm thread itself completed in
398ms in the background.

Test suites, before and after, failure lists diffed rather than counted:

```bash
venv/Scripts/python.exe -m pytest tests/hermes_cli/ -q -p no:randomly \
  -k "model or picker or provider or credential or inventory" \
  --ignore=tests/hermes_cli/test_gateway.py
# baseline: 110 failed, 2351 passed
# after:    110 failed, 2351 passed
# tests broken by this change: none (comm -13 on the sorted failure lists)
```

Those 110 are pre-existing pollution, not real: `test_web_server_profile_unification.py`
passes **34/34** when run alone, including
`test_model_options_matches_tui_safe_probe_flags` and
`test_model_options_offloads_payload_build_to_threadpool`, the two that guard the
behaviour touched here.

```bash
venv/Scripts/python.exe -m pytest tests/hermes_cli/test_web_server_profile_unification.py \
    tests/hermes_cli/test_web_server.py -q -p no:randomly
# -> 1 failed, 529 passed
```

The single failure, `test_put_honcho_first_save_merges_into_resolved_config`, is
unrelated (honcho config merging) and confirmed pre-existing by running it with
`hermes_cli/web_server.py` stashed: fails identically both ways.

Fork guards extended to 15 tests, each mutation-checked — removing any one patch
fails exactly one guard:

```
memo reset removed             -> 1 failed, 14 passed
cache reverted to raw fetch    -> 1 failed, 14 passed
prewarm flag removed           -> 1 failed, 14 passed
gateway prewarm removed        -> 1 failed, 14 passed
```

`tests/hermes_cli/test_gateway.py` cannot be collected on Windows at all — it
imports `tty`/`termios`. Pre-existing platform limitation, excluded from the runs
above.

**Not verified:**
- No end-to-end timing of the picker in the running app. The ~4s figure windro
  reported was never reproduced under a profiler as a single 4s event; what was
  measured is a 1144ms cold / 160ms warm payload build. The rest of his 4s may be
  IPC, React render, or the pricing/capabilities layers, and is unmeasured.
- The `nt.stat` 3397 calls and the ~282ms import cost were identified but not
  fixed — only moved off the first open by the prewarm.
- `cached_api_models` is covered by unit tests with a stubbed fetch; it has never
  run against a real custom endpoint here, because this config does not use one.

## Risk / watch for

- **The memo is thread-local and per build.** If a future refactor calls
  `_credential_pool_is_usable` outside `list_authenticated_providers`, it runs
  with no memo dict and falls back to the old uncached behaviour — correct, just
  slower. That is the safe failure direction.
- **If anything ever caches across builds**, auth changes would stop appearing in
  the picker until restart. The guard test
  `test_memo_does_not_leak_across_builds` exists for exactly that.
- **The prewarm now probes the current custom endpoint.** For a user whose active
  endpoint is offline, the background thread blocks on that probe's timeout. It
  is a daemon thread off the critical path, so this costs nothing user-visible,
  but it does mean one more socket attempt at startup.
- **Two prewarm call sites now exist** (`tui_gateway/entry.py`,
  `hermes_cli/web_server.py`) plus the pre-existing `cli.py`. The
  `_picker_prewarm_done` Event makes it once-per-process, so a process running
  both is harmless — but the guard only checks the two this fork added.

## Follow-ups

- The ~282ms of module imports on the first payload build is still paid, just
  earlier. `vertex_adapter` (126ms) and `httpx` (128ms) are imported for a
  credential scan that in most cases needs neither.
- 3397 `nt.stat` calls per build is unexamined. On Windows with Defender active
  those are not free.
- The 41 remaining `load_pool` calls each still read `auth.json`. Caching the
  auth-store read itself (mtime-invalidated) would collapse those to one, but it
  touches shared auth code — deliberately out of scope here.
