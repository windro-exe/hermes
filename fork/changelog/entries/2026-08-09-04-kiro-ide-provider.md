<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# Kiro split into two providers so each lands on the right settings tab

**Date:** 2026-08-09
**Type:** Added
**Branch:** feat/kiro-ide-provider


## Why

`entries/2026-08-09-01-kiro-provider.md` shipped one `kiro` provider covering both
credential sources -- a pasted `ksk_` key and the sign-in an installed Kiro IDE
leaves in `~/.aws/sso/cache/`. A two-option menu in `hermes model` chose between
them.

That worked in the terminal and could not work in the desktop. `provider_catalog`
routes a provider to the API-keys tab or the Accounts tab purely on `auth_type`,
and `auth_type` describes exactly one credential source. Declaring `api_key` (to
get the free auto-wiring) meant the GUI could only ever render a text box: no way
to offer "reuse the installed Kiro", because there is no hook for a custom flow on
the keys tab. windro asked where the Kiro IDE option was in Accounts; the honest
answer was that it could not be there.

So the fix is to stop fighting `auth_type` and give each credential source its own
provider. Two entries in the picker is also clearer than one entry hiding a
submenu.

## What changed

Two providers from one plugin, sharing all the implementation:

- **`kiro`** -- `auth_type="api_key"`, API-keys tab. A `ksk_` key from
  app.kiro.dev. Aliases `kiro-api`, `amazon-kiro`, `kiro-q`.
- **`kiro-ide`** -- `auth_type="external_process"`, Accounts tab. Reuses an
  installed Kiro IDE/CLI sign-in. Aliases `kiro-cli`, `kiro-desktop`.
  `kiro-ide` was previously an *alias* of `kiro` and had to be freed.

**No proxy change was needed**, which is the part that made the split cheap: the
translator already selects the credential from the bearer it is handed. A `ksk_`
prefix is used as-is; anything matching the session secret authorises reading the
SSO token from disk. Both providers point at the same loopback base URL.

`KIRO_IDE_TOKEN` holds that session secret, written by the setup flow. It is not a
Kiro credential and the user never types it -- the real credential stays where the
IDE put it and never passes through Hermes.

Setup flows split: `_model_flow_kiro` is key-only, `_model_flow_kiro_ide` scans and
reports. Both share `_kiro_select_and_save`, so the persist order exists once.
`kiro-ide` distinguishes three outcomes -- not installed, installed but not signed
in, signed in -- because each needs a different action, and each message names the
other provider as the alternative.

**Five registries had to be wired, and every one was found by running a prompt,
not by a failing assertion.** Worth recording, because the pattern will repeat for
the next non-api-key provider:

1. `CANONICAL_PROVIDERS` (`models.py`) -- the auto-inject block skips
   `external_process` ("non-api-key flows need bespoke picker UX"), so without an
   explicit `ProviderEntry` the provider is registered yet invisible in
   `hermes model`, `provider_catalog()` and the Accounts tab.
2. `HERMES_OVERLAYS` + `_LABEL_OVERRIDES` (`providers.py`) -- else
   `resolve_provider_full` returns None and a saved provider is discarded.
3. `PROVIDER_REGISTRY` (`auth.py`) -- auto-extend covers **api-key providers only**.
   Missing here produces `Unknown provider 'kiro-ide'` at inference time.
4. `get_external_process_provider_status` / `resolve_external_process_provider_credentials`
   (`auth.py`) -- both are copilot-acp-specific despite generic names, so
   `kiro-ide` branches out before the `copilot` binary probe.
5. `resolve_runtime_provider` (`runtime_provider.py`) -- `external_process` is not
   resolved generically; `copilot-acp` has its own branch and this needs the same.
   Missing here leaves `api_key` empty, surfacing as "No LLM provider configured".

Also added `_kiro_ide_status()` and an explicit Accounts card in `web_server.py`.
The provider would appear without it (the catalog union covers every accounts-tab
provider) but with generic defaults and no probe, so the card could not say whether
Kiro was installed or signed in. Unlike `copilot-acp`'s read-only card this
reports real state, because the probe is a cheap offline file read.

## Two bugs found while verifying, both mine

**The proxy's port fallback was silently wrong, and the guard I first wrote was
dead code on Windows.** The provider's base URL is pinned to `127.0.0.1:8779` in
`HERMES_OVERLAYS`, so it cannot follow the server elsewhere. The original code fell
back to an OS-assigned port when 8779 was taken -- guaranteeing that Hermes kept
dialling 8779, reached a foreign proxy, and got `HTTP 401: invalid bearer token`.
Measured exactly that with a second Hermes on its own `HERMES_HOME` running
alongside the desktop backend.

The first fix raised on bind failure. That is useless here: `HTTPServer` sets
`allow_reuse_address`, and on Windows `SO_REUSEADDR` permits binding a port already
in `LISTEN`. The bind *succeeds*, two servers race for connections, and the guard
never fires on the platform that needs it. Now `ensure_running` probes the port
**before** binding, adopts if the state file yields the secret, and otherwise
raises with the `KIRO_PROXY_PORT` / `KIRO_BASE_URL` escape hatch spelled out.

**The token file was read as strict `utf-8`.** Kiro owns that file and we do not
control its encoding; a BOM made `json.loads` fail with "Unexpected UTF-8 BOM",
which surfaced to the user as "not signed in" and would send them to
re-authenticate for nothing. Now `utf-8-sig`, which accepts both.

## Verified

```
pytest tests/providers/test_kiro_provider.py -q              -> 86 passed
pytest tests/providers/ test_provider_parity.py
       test_fork_upstream_disconnect.py
       test_runtime_provider_resolution.py -q                -> 389 passed
ruff check                                                   -> All checks passed
```

Tab routing and the Accounts card, asserted and observed:

```
kiro      tab='keys'      label='Kiro'
kiro-ide  tab='accounts'  label='Kiro IDE'
card: "Kiro IDE 1.0.288 (C:\...\Kiro.exe) - installed, not signed in"
kiro-ide in /api/providers/oauth: True    kiro (keys tab) absent from it: True
```

End to end against the live AWS Q endpoint:

```
kiro      -z "...one word: alpha"  -> alpha
kiro-ide  -z "...one word: beta"   -> beta
adoption: desktop holds 8779; a terminal with the same HERMES_HOME adopts it
          (no second server, secret recovered from the state file)
```

**How `kiro-ide` was tested without a subscription, and why that is not a full
verification.** There is no Kiro subscription on this machine, so the IDE writes no
SSO token. The full path was exercised by writing a temporary
`~/.aws/sso/cache/kiro-auth-token.json` whose `accessToken` was windro's `ksk_`
key -- a valid bearer, so the file read, expiry check, session-secret handshake,
runtime resolution, proxy auth and a real AWS call all ran. The file was removed
afterwards.

What that does **not** cover: a genuine SSO access token, and therefore the OIDC
refresh at `https://oidc.{region}.amazonaws.com/token`. A `ksk_` key never expires,
so the refresh branch was not executed -- it remains reviewed-but-unrun code,
ported from the Syncode reference. Also untested: `clientIdHash` indirection, and
multi-account SSO caches.

## Risk / watch for

- **The fixed port 8779 is load-bearing.** Two Hermes instances with different
  `HERMES_HOME` values cannot share it; the second now fails loudly rather than
  producing a mystery 401. Deliberate multi-instance use needs both
  `KIRO_PROXY_PORT` and `KIRO_BASE_URL` set, and setting only one reintroduces the
  original bug.
- Same-`HERMES_HOME` instances adopt each other via the state file, so deleting
  `$HERMES_HOME/kiro-proxy/proxy.json` while an instance is live makes the next one
  refuse to start. Stop the running instance rather than deleting the file.
- **`KIRO_IDE_TOKEN` is a local secret, not a cloud credential.** It must never be
  forwarded to AWS. `KiroIdeProfile.fetch_models` deliberately ignores the
  `api_key` argument for this reason, and a test asserts it.
- The Accounts card reports "signed in" from the presence and expiry of the token
  file, not from a live call. A revoked-but-unexpired token will read as signed in
  until the first request fails.
- `get_external_process_provider_status` and
  `resolve_external_process_provider_credentials` now serve two unrelated
  providers behind generic names. A third will want them refactored rather than a
  third `if`.

## Follow-ups

- **The Accounts card has no Scan/Connect button.** windro asked for one. The three
  flow shapes are `pkce`, `device_code` and `external`; `external` renders a
  Terminal icon and, on click, shows a command to run rather than acting. A real
  click-to-scan needs a fourth flow type threaded through `types/hermes.ts`,
  `providers-settings.tsx`, `store/onboarding.ts`, the i18n strings and a server
  `/start` handler. Not attempted here. Today the card shows live status and the
  connect happens through `hermes model`.
- Exercise the SSO refresh against a real subscription. Until then that branch is
  unrun.
- If a third `external_process` provider appears, refactor the two `auth.py`
  resolvers to dispatch instead of accumulating `if provider ==` branches.
