"""Kiro model catalog.

Fork-owned. The id list was read from the live ``ListAvailableModels`` response
on 2026-08-09, which returned 19 models -- notably more than the 11 hardcoded in
the Syncode reference. Prefer the live call at runtime; this table is the
fallback for when it fails, so a failed fetch shows a usable picker instead of an
empty one.

Context limits are the part worth reading carefully. For the Claude and GPT ids
they are **empirically measured** values carried over from the Syncode fork, not
the advertised catalog numbers -- opus-4.8/4.7 and the sonnet-5 preview are
input-throttled to roughly 640K despite a "1M" label. For the ids that the live
call surfaced but Syncode never covered (deepseek, minimax, glm, qwen, haiku,
sonnet-4, auto) nothing has been measured, so they fall back to a conservative
default. Under-estimating a limit degrades gracefully; over-estimating it fails
the request outright.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

#: Used when a model id is unknown or unmeasured. Conservative on purpose.
DEFAULT_CONTEXT = 200_000
DEFAULT_OUTPUT = 64_000


class ModelInfo(NamedTuple):
    id: str
    name: str
    context: int
    output: int
    #: False for ids Q rejects images on (every gpt variant, measured).
    vision: bool = True
    #: True when the limits came from measurement rather than a default.
    measured: bool = True


# Ordered roughly strongest-first; entry 0 becomes the setup default.
MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5", "Claude Opus 5", 640_000, 128_000),
    ModelInfo("claude-sonnet-5", "Claude Sonnet 5", 640_000, 64_000),
    ModelInfo("claude-opus-4.8", "Claude Opus 4.8", 640_000, 128_000),
    ModelInfo("claude-opus-4.7", "Claude Opus 4.7", 640_000, 128_000),
    ModelInfo("claude-opus-4.6", "Claude Opus 4.6", 1_000_000, 128_000),
    ModelInfo("claude-sonnet-4.6", "Claude Sonnet 4.6", 1_000_000, 64_000),
    ModelInfo("claude-opus-4.5", "Claude Opus 4.5", 200_000, 64_000),
    ModelInfo("claude-sonnet-4.5", "Claude Sonnet 4.5", 200_000, 64_000),
    ModelInfo("gpt-5.6-sol", "GPT-5.6 Sol", 272_000, 64_000, vision=False),
    ModelInfo("gpt-5.6-terra", "GPT-5.6 Terra", 272_000, 64_000, vision=False),
    ModelInfo("gpt-5.6-luna", "GPT-5.6 Luna", 272_000, 64_000, vision=False),
    # --- surfaced by the live call; limits NOT measured, defaults applied ---
    ModelInfo("claude-sonnet-4", "Claude Sonnet 4", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("claude-haiku-4.5", "Claude Haiku 4.5", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("deepseek-3.2", "DeepSeek 3.2", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("glm-5", "GLM-5", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("minimax-m2.5", "MiniMax M2.5", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("minimax-m2.1", "MiniMax M2.1", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
    ModelInfo("qwen3-coder-next", "Qwen3 Coder Next", DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False),
)

#: Ids the service offers that this provider deliberately does NOT surface.
#:
#: ``auto`` is Kiro's server-side router. It works, but it does not disclose what
#: it routed to — ``modelId`` in the response echoes back ``"auto"`` rather than
#: the resolved model (verified live), so cost and quality cannot be attributed to
#: anything. Its window measured 1,000,000 and its per-call overhead matched
#: ``claude-opus-4.6``, but a router's target can change server-side at any time,
#: which would silently invalidate that number in the direction that fails requests
#: rather than degrading them.
#:
#: Excluded because this fork intends to do its own routing, where the chosen model
#: is known and loggable. Filtering matters as well as omitting it from ``MODELS``:
#: ``ListAvailableModels`` returns ``auto``, so a live catalog fetch would put it
#: straight back in the picker.
HIDDEN_MODEL_IDS = frozenset({"auto"})


def visible_model_ids(ids: "Iterable[str]") -> list[str]:
    """Drop hidden ids from a model list, preserving order and de-duplicating.

    Applied to the LIVE ``ListAvailableModels`` response, which is why omitting an
    entry from ``MODELS`` alone is not enough.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        model = (raw or "").strip()
        if not model or model in HIDDEN_MODEL_IDS or model in seen:
            continue
        seen.add(model)
        out.append(model)
    return out

_BY_ID = {m.id: m for m in MODELS}


def static_model_ids() -> list[str]:
    """Fallback id list for when the live catalog call fails."""
    return [m.id for m in MODELS]


def default_model() -> str:
    return MODELS[0].id


def info_for(model_id: str) -> ModelInfo:
    """Look up a model, tolerating unknown ids.

    Unknown ids get conservative defaults rather than an exception: the live
    catalog can gain entries at any time and a new model should degrade to
    "works, with a cautious context estimate" instead of breaking the provider.
    """
    found = _BY_ID.get((model_id or "").strip())
    if found is not None:
        return found
    return ModelInfo(model_id, model_id, DEFAULT_CONTEXT, DEFAULT_OUTPUT, measured=False)


def context_limit_for(model_id: str) -> int:
    """Context window used to turn Q's usage percentage into a token estimate."""
    return info_for(model_id).context


def supports_vision(model_id: str) -> bool:
    return info_for(model_id).vision
