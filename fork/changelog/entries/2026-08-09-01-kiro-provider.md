<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# Kiro (Amazon Q) as a provider, without adding a transport to core

**Date:** 2026-08-09
**Type:** Added
**Branch:** feat/kiro-provider


## Why

Kiro is not a supported Hermes provider and cannot become one the usual way: it
has no OpenAI-compatible API. It speaks AWS Q `GenerateAssistantResponse`,
dispatched by an `X-Amz-Target` header with `Content-Type:
application/x-amz-json-1.0`, and answers in the AWS **binary event-stream**
framing protocol. Hermes recognises five wire protocols and the set is closed:

```python
_VALID_API_MODES = {"chat_completions", "codex_responses", "anthropic_messages",
                    "bedrock_converse", "codex_app_server"}
```

Kiro is none of them, and it is specifically **not** `bedrock_converse` — a
different service with a different body shape.

That left two options. Add a sixth transport: a new `agent/transports/kiro.py`,
an entry in `_VALID_API_MODES`, a `CANONICAL_PROVIDERS` row, a dispatch branch, a
setup flow. Six-plus upstream files, all of it permanent core surface for one
provider, straight against the Footprint Ladder. Or translate at the edge.

This is the second. A loopback server converts `chat/completions` to AWS Q and
back, so Hermes sees an ordinary `chat_completions` provider on `127.0.0.1` and
core is untouched. What windro asked for was "a proxy", aimed at authentication;
the proxy turned out to be the right shape for a different reason — protocol
isolation. Authentication needed no proxy at all (see below).

**The auth premise was wrong and worth recording.** The two paths windro
described — paste an API key, or detect an installed Kiro and go "through its
proxy" — are not two transports. Both end as a plain bearer token against the
same endpoint. The only wire-level difference is that `ksk_` programmatic keys
additionally require a `tokentype: API_KEY` header. "Detect the IDE" reduces to
"read the token file the IDE already wrote."

## What changed

New, fork-owned, all under `plugins/model-providers/kiro/` — no upstream file
touched by any of it:

- **`auth.py`** — credential resolution and install detection. Precedence:
  explicit key, then `KIRO_API_KEY`, then the AWS SSO token an installed Kiro
  writes to `~/.aws/sso/cache/kiro-auth-token.json`. Refresh is the SSO-OIDC
  `CreateToken` call, which is REST-JSON with **camelCase** keys (`grantType`,
  `refreshToken`) — not form-encoded OAuth 2.0; the OAuth spelling is rejected.
  Detection probes platform install paths plus the `bin/` CLI shim and `PATH`,
  and reads the version from `resources/app/package.json` rather than executing
  the binary. Two interpolation guards: region must match
  `^[a-z]{2}-[a-z]+-\d+$` before going into a URL, and `clientIdHash` must match
  `^[a-zA-Z0-9_-]+$` before going into a filename.
- **`wire.py`** — the translation, plus a hand-rolled event-stream frame
  splitter. `botocore` was the obvious choice and was rejected: it is only an
  optional `bedrock` extra, and the splitter is ~60 lines. CRC fields are
  skipped deliberately — the bytes arrive over TLS, so frame integrity is
  already established and re-checking buys nothing.
- **`client.py`** — the HTTPS client, region probe, and `ListAvailableModels`.
- **`proxy.py`** — the loopback server. Runs as an in-process daemon thread, not
  a subprocess: Hermes is already a Python process, so a second one adds an
  orphan to reap and a PID file to go stale for no gain. Dies with Hermes.
- **`catalog.py`** — model ids and context limits.
- **`__init__.py`** — the `ProviderProfile`.

Upstream files, kept as small as possible:

- **`hermes_cli/main.py`** — one `elif selected_provider == "kiro":` branch plus
  an import. It must stay ahead of the `_is_profile_api_key_provider` catch-all
  at the end of the chain, which would otherwise swallow it.
- **`hermes_cli/model_setup_flows.py`** — `_model_flow_kiro`, the two-option
  menu, plus a `_kiro_modules()` helper. Modelled on `_model_flow_copilot`.
- **`hermes_cli/providers.py`** — a `HERMES_OVERLAYS["kiro"]` entry and a label
  override. Without the overlay `resolve_provider_full("kiro")` returns None and
  a saved `provider: kiro` is silently discarded in favour of env auto-detect —
  the exact bug `tests/hermes_cli/test_upstage_provider.py` guards against.

The design decision that keeps this small: the profile declares
`auth_type="api_key"`. That is accurate for the pasted key, and it auto-wires the
provider registry, `OPTIONAL_ENV_VARS`, `CANONICAL_PROVIDERS` and the desktop
keys tab with no further edits. The installed-Kiro path is reached through the
explicit dispatch branch, which runs first. Declaring a bespoke auth type instead
would have cost three more upstream edits and required a matching
`/api/providers/oauth` entry to keep `test_provider_parity` passing.

## Verified

Offline, in the real env (`uv sync` + `uv sync --extra dev`, python 3.11.15):

```
.venv/Scripts/python.exe -m pytest tests/providers/test_kiro_provider.py -q
  -> 62 passed
.venv/Scripts/python.exe -m pytest tests/providers/ -q
  -> 187 passed          (no regressions in the existing provider suite)
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_provider_parity.py -q
  -> 3 passed            (the desktop tab-coverage contract)
.venv/Scripts/python.exe -m pytest tests/hermes_cli/test_upstage_provider.py \
    tests/hermes_cli/test_fireworks_provider.py -q
  -> 26 passed
```

Live against the real Q endpoint, using windro's `ksk_` key:

```
region probe                 -> us-east-1
chat, claude-sonnet-4.5      -> "Paris" for "capital of France"
tool call                    -> {"id":"tooluse_...","function":{"name":"read",
                                 "arguments":"{\"path\": \"/tmp/a\"}"}}
ListAvailableModels          -> 19 ids
proxy /models                -> 200, 19 models
proxy non-streaming          -> "Tokyo", finish_reason=stop
proxy streaming SSE          -> 5 chunks, "1\n2\n3", usage chunk, [DONE]
proxy streaming tool call    -> name=read, valid JSON args, finish_reason=tool_calls
proxy auth gate              -> 401 on missing token, 401 on wrong token,
                                404 on unknown route, health needs no auth
```

Full path through Hermes itself, in an isolated `HERMES_HOME` so the real config
was untouched:

```
python -m hermes_cli.main -z "Reply with exactly one word: ping" \
    --provider kiro -m claude-sonnet-4.5
  -> ping        (exit 0)
```

**Not verified — the installed-Kiro credential path end to end.** Kiro IDE 1.0.288
is installed on this machine, and detection of it *is* verified (finds the IDE,
the `bin/kiro.cmd` shim, and the version). But windro has no Kiro subscription, so
there is no sign-in and therefore no `~/.aws/sso/cache/kiro-auth-token.json`. The
token reader, expiry logic and refresh-error paths are unit-tested against
synthetic files; **`https://oidc.{region}.amazonaws.com/token` has never actually
been called.** The refresh body shape is ported from the Syncode reference, not
observed. Treat that path as untested code that has been reviewed, not as working
code.

Also not verified: multi-turn conversations beyond two turns, image attachments
against the live service (the request shape is unit-tested only), reasoning-block
round-tripping, and the `auto` router model.

## Risk / watch for

- **Token accounting is estimated, and this matters.** Measured against the live
  service, Q reports *no token counts at all*. The only accounting it sends is
  `contextUsageEvent {"contextUsagePercentage": 2.05}` and `meteringEvent
  {"unit":"credit","usage":0.0174}`. So `estimate_usage()` derives
  `prompt_tokens` from the percentage against the model's context limit, and
  `completion_tokens` from characters emitted divided by four. Both are labelled
  `usage_is_estimated: true` in the response. Returning zeros would be more
  literally honest and actively harmful — Hermes drives context compression off
  these numbers, and a permanent zero would stop compression ever triggering.
  If costs or compression behave oddly on this provider, start here.
- A ~5-word prompt reports 2.05% of context, roughly 4,100 estimated tokens.
  Q evidently injects a large prompt of its own, so expect a fixed overhead per
  call that is not visible in the messages Hermes sent.
- **Eight of the 19 live models have unmeasured context limits** and fall back to
  a conservative 200K: `claude-sonnet-4`, `claude-haiku-4.5`, `deepseek-3.2`,
  `glm-5`, `minimax-m2.5`, `minimax-m2.1`, `qwen3-coder-next`, `auto`.
  Under-estimating degrades gracefully; over-estimating fails the request, hence
  the direction of the guess. `catalog.info_for().measured` marks which is which
  and the setup flow warns when an unmeasured model is chosen.
- **`assistantResponseEvent` is overloaded by field presence, not event name** —
  the same event type carries text, tool starts, tool stops, tool input and
  usage depending on which key is set. Dispatching on `:event-type` alone
  silently loses every tool call. This is the single easiest thing to break here.
- The fixed port 8779 is what makes the persisted `base_url` stable. If it is
  taken the proxy falls back to an OS-assigned port and the saved config points
  at nothing; re-running `hermes model` repairs it. `KIRO_PROXY_PORT` overrides.
  A test asserts the overlay, the profile and `proxy.DEFAULT_PORT` all agree.
- For the installed-Kiro path the stored credential is the proxy's **session
  secret**, which is reused from the state file across restarts precisely so the
  saved config keeps working. Deleting `$HERMES_HOME/kiro-proxy/proxy.json`
  invalidates it and the provider must be re-selected.
- The `aws-sdk-js/1.0.27` User-Agent prefix appears to be load-bearing; only the
  trailing identifier was changed to `hermes-kiro`. If calls start being
  rejected, suspect that first.
- Kiro is unofficial. Nothing here is a documented API, and Amazon owes it no
  stability. Any of these endpoints or payload shapes can change without notice.

## Follow-ups

- Exercise the installed-Kiro path against a real subscription. Until then it is
  reviewed-but-unrun code, and the refresh call in particular.
- Measure real context limits for the eight unmeasured models, ideally by
  bisecting until Q returns `CONTENT_LENGTH_EXCEEDS_THRESHOLD`, the way the
  existing figures were obtained.
- Reasoning content is decoded and forwarded as `reasoning_content` deltas, but
  the signature round-trip the reference implements is not ported. Replaying an
  unsigned reasoning block is rejected by the service, so multi-turn reasoning
  may need that work before it behaves.
- No `hermes kiro serve` subcommand is wired up yet, though
  `proxy.serve_forever()` exists for it. The in-process thread covers normal use.
- `pyproject.toml` was not touched: the implementation needs no dependency beyond
  the standard library.
