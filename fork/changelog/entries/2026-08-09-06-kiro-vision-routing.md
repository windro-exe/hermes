<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# Kiro models silently lost image input

**Date:** 2026-08-09
**Type:** Fixed
**Branch:** fix/provider-vision-capability


## Why

Attaching an image to a Kiro model produced a reply saying it could not see one.
The image was never sent.

`agent/image_routing.py::_lookup_supports_vision` resolves capability in order:
the user's `supports_vision` override in config.yaml, then models.dev, then an
Ollama probe. models.dev only carries public catalog models, and Kiro's ids
(`claude-opus-5`, `gpt-5.6-sol`, ...) are not in it, so the lookup returned
`None`. `decide_image_input_mode` turns unknown into `"text"`, which routes the
image to a side model or drops it rather than sending it natively.

Reproduced before the fix:

```
kiro/claude-opus-5      supports_vision=None   image_mode=text
kiro/claude-sonnet-4.5  supports_vision=None   image_mode=text
```

The provider profile declared `supports_vision=True` the whole time. **Nothing in
the chain read it.** This is not Kiro-specific: any plugin provider with model
names models.dev has never heard of loses image input the same way.

## What changed

- **`agent/image_routing.py`** — after models.dev comes back empty, ask the
  registered provider plugin via an optional `supports_vision_for_model(model)`
  hook. Placed after models.dev because that data is authoritative for models it
  knows, and before the Ollama probe because a declaration beats a network guess.
  A profile returning `None` leaves the answer unknown, exactly as before.
- **`plugins/model-providers/kiro/__init__.py`** — `KiroProfile` implements the
  hook, delegating to `catalog.supports_vision(model)`. `KiroIdeProfile` inherits
  it.

## The bug I nearly shipped instead

The first version also fell back to the profile-wide `supports_vision` flag when
no hook existed. Checking before pushing showed why that is wrong:

```
providers declaring supports_vision=False: 35
  including anthropic, gemini, bedrock, openrouter, vertex, xai
```

All of those plainly support vision. On those profiles the flag means "not
declared", not "no vision". Trusting it would have converted an honest `None`
into a hard `False` and silently killed image input for **any** model models.dev
has not caught up with — a new Anthropic release, any OpenRouter model missing
from the catalog. That is a much worse bug than the one being fixed, and it would
have been invisible until someone attached an image to a new model.

So only the explicit per-model hook is consulted. Providers that do not implement
it are left unknown, precisely as they were.

## Verified

```
pytest tests/providers/test_kiro_provider.py -q                    -> 91 passed
pytest tests/agent/test_image_routing.py -q                        -> 105 passed, 7 pre-existing failures
pytest tests/agent/test_custom_providers_vision.py
       tests/agent/test_vision_routing_31179.py
       tests/agent/test_vision_resolved_args.py
       tests/gateway/test_image_input_routing_runtime.py
       tests/tools/test_computer_use_vision_routing.py -q           -> 64 passed
pytest tests/providers/ test_provider_parity.py
       test_fork_upstream_disconnect.py -q                          -> 242 passed
ruff check                                                          -> All checks passed
```

The 7 failures are `TestExtractImageRefs` path-extraction cases, confirmed
pre-existing by stashing this branch and re-running against clean `main` —
identical 7. They look like POSIX path assumptions failing on Windows; untouched
here.

Routing decisions after the fix, and the non-regression that matters:

```
kiro/claude-opus-5            native     kiro/gpt-5.6-sol       text
kiro-ide/claude-opus-5        native
anthropic/some-future-model   None (unknown, unchanged)
openrouter/vendor/unknown     None (unknown, unchanged)
anthropic/claude-sonnet-4-5   True (still from models.dev)
```

End to end against the live AWS Q endpoint with a generated 240x240 PNG — solid
blue with a white horizontal band:

```
claude-opus-5 -> "A solid blue square with a horizontal white band running
                  across it, set slightly below center."
gpt-5.6-sol   -> no image attached; replies that it cannot see one
```

That second line is correct rather than a failure: Q rejects images on every
`gpt-*` id with REQUEST_BODY_INVALID, so `wire.build_request_body` drops them and
the routing layer agrees. A test now asserts those two halves cannot drift apart —
if they did, images would either be lost on Claude or 400 the request on GPT.

## Risk / watch for

- **Never widen this to the profile-wide flag.** A guard test
  (`test_profile_wide_flag_is_NOT_used_as_a_fallback`) pins it, with the 35
  providers as the reason. If that test starts failing, read this entry before
  "fixing" it.
- The hook is consulted on every image turn, so a slow or throwing implementation
  would hurt. Exceptions are caught and degrade to unknown
  (`test_a_broken_profile_cannot_break_routing`), but a plugin should keep it a
  cheap local lookup — Kiro's is a dict read.
- Unknown Kiro ids default to vision-capable via `catalog.info_for`. If Amazon adds
  a text-only model, the first image request to it will fail until the catalog
  learns about it. The alternative — defaulting to no-vision — silently degrades
  every genuinely capable new model, which is worse.
- `models.dev` still wins for models it knows. If Kiro's ids ever appear there
  with wrong capability data, that data takes precedence over the plugin.

## Follow-ups

- The 7 `TestExtractImageRefs` failures on Windows are real and unrelated. Worth a
  look on their own; they are not a vision-routing problem.
- Other plugin providers with private model names have the same latent gap and
  could implement the hook: nothing else in `plugins/model-providers/` does yet.
