"""Translation between OpenAI chat/completions and the AWS Q wire protocol.

Fork-owned. Ported from windro's Syncode fork
(``packages/opencode/src/provider/kiro/index.ts``). None of this protocol is
publicly documented, so the reference implementation is the spec.

Two directions:

* :func:`build_request_body` turns OpenAI-shaped ``messages`` + ``tools`` into
  the ``conversationState`` payload ``GenerateAssistantResponse`` expects.
* :class:`EventStreamDecoder` + :func:`translate_event` turn the AWS binary
  event-stream response back into OpenAI-shaped streaming deltas.

The event-stream framing is hand-rolled rather than pulled from botocore. It is
~60 lines, it avoids making ``boto3`` a hard requirement of this provider, and
CRC verification is deliberately skipped -- the bytes arrive over TLS, so frame
integrity is already guaranteed and re-checking it buys nothing.
"""

from __future__ import annotations

import base64
import json
import logging
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

ORIGIN = "AI_EDITOR"
CHAT_TRIGGER = "MANUAL"

#: Effort values the service accepts. Anything else is dropped rather than sent,
#: because an unrecognised value fails the whole request.
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

#: Q rejects empty strings. These are the stand-ins the reference uses.
EMPTY_USER = " "
EMPTY_ASSISTANT = "(empty)"

#: Per-message image cap enforced by the service.
MAX_IMAGES_PER_MESSAGE = 20

_IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

# Frame parsing limits. 16 MiB matches the reference's ceiling.
_MAX_FRAME = 16 * 1024 * 1024
_PRELUDE = 12  # total_len(4) + headers_len(4) + prelude_crc(4)
_TRAILER = 4  # message_crc(4)


# --- request construction --------------------------------------------------


def _text_of(content: Any) -> str:
    """Flatten OpenAI content (string or part list) into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        return "".join(chunks)
    return str(content)


def _images_of(content: Any, model_id: str) -> list[dict[str, Any]]:
    """Extract image parts as Q image blocks.

    ``source.bytes`` is a base64 **string**, not a byte array -- the reference
    hand-rolls AWS_JSON_1_0 rather than going through an SDK serialiser, so the
    blob is already encoded by the time it hits the wire.

    GPT models on Q reject the ``images`` field outright (REQUEST_BODY_INVALID),
    so they are dropped for those ids rather than causing a 400.
    """
    if not isinstance(content, list) or "gpt" in model_id:
        return []
    out: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        url = (part.get("image_url") or {}).get("url", "")
        if not isinstance(url, str) or not url.startswith("data:"):
            # Remote URLs are the caller's job to fetch; we cannot proxy them.
            continue
        try:
            header, payload = url.split(",", 1)
            mime = header[5:].split(";")[0].strip().lower()
        except ValueError:
            continue
        fmt = _IMAGE_FORMATS.get(mime)
        if not fmt:
            continue
        # Validate before transmitting so a malformed blob fails here, loudly,
        # rather than as an opaque service-side rejection.
        try:
            base64.b64decode(payload, validate=True)
        except Exception:
            logger.debug("kiro: dropping image with undecodable base64 (%s)", mime)
            continue
        out.append({"format": fmt, "source": {"bytes": payload}})
        if len(out) >= MAX_IMAGES_PER_MESSAGE:
            break
    return out


def _tool_specs(tools: Optional[Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Wrap OpenAI tool definitions in Q's ``toolSpecification`` envelope."""
    specs: list[dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        specs.append(
            {
                "toolSpecification": {
                    "name": name,
                    "description": fn.get("description", "") or "",
                    "inputSchema": {"json": fn.get("parameters") or {"type": "object"}},
                }
            }
        )
    return specs


def _tool_uses_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert assistant ``tool_calls`` into Q ``toolUses``."""
    uses: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except ValueError:
                parsed = {}
        elif isinstance(raw, dict):
            parsed = raw
        else:
            parsed = {}
        uses.append(
            {
                "name": fn.get("name", "") or "",
                "input": parsed,
                "toolUseId": call.get("id") or str(uuid.uuid4()),
            }
        )
    return uses


def _user_block(text: str, model_id: str, content: Any = None) -> dict[str, Any]:
    block: dict[str, Any] = {
        "content": text or EMPTY_USER,
        "modelId": model_id,
        "origin": ORIGIN,
    }
    images = _images_of(content, model_id)
    if images:
        block["images"] = images
    return block


def build_request_body(
    messages: list[dict[str, Any]],
    model_id: str,
    *,
    tools: Optional[Iterable[dict[str, Any]]] = None,
    effort: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    """Build the full ``GenerateAssistantResponse`` request body.

    Three behaviours here are non-obvious and load-bearing:

    * **Q has no system role.** System messages are joined with newlines and
      prepended to the first user turn. Sending them as their own role makes the
      service return empty completions.
    * **Trailing tool results are hoisted** out of history into
      ``currentMessage.userInputMessageContext.toolResults``.
    * **Empty content is illegal**, so blanks become a single space (user) or
      ``"(empty)"`` (assistant).
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "system":
            text = _text_of(message.get("content"))
            if text:
                system_parts.append(text)
        else:
            rest.append(message)
    system_prompt = "\n".join(system_parts)

    # Hoist the trailing run of tool results into the current message.
    trailing = 0
    for message in reversed(rest):
        if message.get("role") == "tool":
            trailing += 1
        else:
            break

    tool_results: list[dict[str, Any]] = []
    if trailing:
        history_source = rest[: len(rest) - trailing]
        for message in rest[len(rest) - trailing :]:
            tool_results.append(
                {
                    "toolUseId": message.get("tool_call_id") or "",
                    "content": [{"text": _text_of(message.get("content")) or EMPTY_USER}],
                    "status": "error" if message.get("is_error") else "success",
                }
            )
        current_text = ""
        current_content: Any = None
    else:
        history_source = rest[:-1] if rest else []
        last = rest[-1] if rest else None
        if last is not None and last.get("role") != "user":
            # Assistant prefill: keep it in history and send a blank current turn
            # rather than silently dropping the turn.
            history_source = rest
            current_text = ""
            current_content = None
        else:
            current_text = _text_of(last.get("content")) if last else ""
            current_content = last.get("content") if last else None

    history = _build_history(history_source, model_id, system_prompt)

    # With no history, the system prompt rides on the current message instead.
    if not history and system_prompt:
        current_text = f"{system_prompt}\n{current_text}" if current_text else system_prompt

    context: dict[str, Any] = {}
    specs = _tool_specs(tools)
    if specs:
        context["tools"] = specs
    if tool_results:
        context["toolResults"] = tool_results

    current_block = _user_block(current_text, model_id, current_content)
    if context:
        current_block["userInputMessageContext"] = context

    state: dict[str, Any] = {
        "conversationId": conversation_id or str(uuid.uuid4()),
        "currentMessage": {"userInputMessage": current_block},
        "history": history,
        "chatTriggerType": CHAT_TRIGGER,
    }

    body: dict[str, Any] = {"conversationState": state}

    effort = (effort or "").strip().lower()
    if effort in VALID_EFFORTS:
        # Claude wants output_config.effort; GPT rejects that shape and wants
        # reasoning.effort. Sending the wrong one 400s the entire request.
        body["additionalModelRequestFields"] = (
            {"reasoning": {"effort": effort}} if "gpt" in model_id else {"output_config": {"effort": effort}}
        )
    return body


def _build_history(
    messages: list[dict[str, Any]], model_id: str, system_prompt: str
) -> list[dict[str, Any]]:
    """Turn prior turns into Q history, prepending the system prompt to turn 1."""
    history: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            history.append({"userInputMessage": _user_block(_text_of(message.get("content")), model_id, message.get("content"))})
        elif role == "assistant":
            block: dict[str, Any] = {"content": _text_of(message.get("content")) or EMPTY_ASSISTANT}
            uses = _tool_uses_of(message)
            if uses:
                block["toolUses"] = uses
            history.append({"assistantResponseMessage": block})
        elif role == "tool":
            # A tool result that is not part of the trailing run still needs a
            # carrier turn, since Q models results as user input.
            history.append(
                {
                    "userInputMessage": {
                        "content": EMPTY_USER,
                        "modelId": model_id,
                        "origin": ORIGIN,
                        "userInputMessageContext": {
                            "toolResults": [
                                {
                                    "toolUseId": message.get("tool_call_id") or "",
                                    "content": [{"text": _text_of(message.get("content")) or EMPTY_USER}],
                                    "status": "success",
                                }
                            ]
                        },
                    }
                }
            )

    if system_prompt and history:
        first = history[0]
        if "userInputMessage" in first:
            existing = first["userInputMessage"].get("content", "")
            first["userInputMessage"]["content"] = f"{system_prompt}\n{existing}".strip() or EMPTY_USER
        else:
            history.insert(0, {"userInputMessage": _user_block(system_prompt, model_id)})
    return history


# --- response decoding -----------------------------------------------------


@dataclass
class Frame:
    """One decoded event-stream message."""

    headers: dict[str, str]
    payload: bytes

    @property
    def message_type(self) -> str:
        return self.headers.get(":message-type", "")

    @property
    def event_type(self) -> str:
        return self.headers.get(":event-type", "")

    def json(self) -> dict[str, Any]:
        if not self.payload:
            return {}
        try:
            data = json.loads(self.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def _parse_headers(raw: bytes) -> dict[str, str]:
    """Parse the AWS event-stream header block.

    Only the string type (7) is decoded, because every header this protocol
    uses in practice is a string. Other types are skipped by length so an
    unexpected one cannot desynchronise the parse.
    """
    headers: dict[str, str] = {}
    offset = 0
    size = len(raw)
    while offset < size:
        name_len = raw[offset]
        offset += 1
        name = raw[offset : offset + name_len].decode("utf-8", "replace")
        offset += name_len
        value_type = raw[offset]
        offset += 1
        if value_type == 7:  # string
            (value_len,) = struct.unpack_from(">H", raw, offset)
            offset += 2
            headers[name] = raw[offset : offset + value_len].decode("utf-8", "replace")
            offset += value_len
        elif value_type in (0, 1):  # bool true/false, no payload
            headers[name] = str(value_type == 0)
        elif value_type == 2:
            offset += 1
        elif value_type == 3:
            offset += 2
        elif value_type == 4:
            offset += 4
        elif value_type in (5, 8):
            offset += 8
        elif value_type == 6:  # byte array
            (value_len,) = struct.unpack_from(">H", raw, offset)
            offset += 2 + value_len
        elif value_type == 9:  # uuid
            offset += 16
        else:
            # Unknown type and unknown width -- stop rather than guess.
            break
    return headers


class EventStreamDecoder:
    """Incremental AWS binary event-stream frame splitter.

    Feed it arbitrary chunks; it yields whole :class:`Frame` objects as they
    complete. Safe across chunk boundaries, which is the whole point.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[Frame]:
        if chunk:
            self._buffer.extend(chunk)
        while True:
            if len(self._buffer) < _PRELUDE:
                return
            (total_len,) = struct.unpack_from(">I", self._buffer, 0)
            if total_len < _PRELUDE + _TRAILER or total_len > _MAX_FRAME:
                raise ValueError(f"kiro: implausible event-stream frame length {total_len}")
            if len(self._buffer) < total_len:
                return
            (headers_len,) = struct.unpack_from(">I", self._buffer, 4)
            if headers_len > total_len - _PRELUDE - _TRAILER:
                raise ValueError("kiro: event-stream header length overruns frame")
            header_start = _PRELUDE
            payload_start = header_start + headers_len
            payload_end = total_len - _TRAILER
            frame = Frame(
                headers=_parse_headers(bytes(self._buffer[header_start:payload_start])),
                payload=bytes(self._buffer[payload_start:payload_end]),
            )
            del self._buffer[:total_len]
            yield frame

    @property
    def pending_bytes(self) -> int:
        """Bytes buffered but not yet a complete frame (diagnostics)."""
        return len(self._buffer)


@dataclass
class StreamState:
    """Accumulator across a single response stream.

    Note what is *absent*: Q does not report token counts. Measured against the
    live service, the only accounting it sends is
    ``contextUsageEvent {"contextUsagePercentage": 2.06}`` and
    ``meteringEvent {"unit":"credit","usage":0.0187}``. So ``input_tokens`` is an
    estimate derived from the percentage against a known context limit, and
    ``output_tokens`` is estimated from emitted characters. Both are clearly
    approximations -- see :func:`estimate_usage`.
    """

    tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_order: list[str] = field(default_factory=list)
    errored: bool = False
    error_message: str = ""
    stop_reason: str = ""
    context_usage_percent: Optional[float] = None
    credits: Optional[float] = None
    emitted_chars: int = 0


@dataclass
class Delta:
    """One normalised piece of output, ready to become an SSE chunk."""

    kind: str  # "text" | "reasoning" | "tool_start" | "tool_delta" | "tool_end" | "usage" | "error"
    text: str = ""
    tool_id: str = ""
    tool_name: str = ""


def translate_event(frame: Frame, state: StreamState) -> list[Delta]:
    """Map one Q frame onto zero or more normalised deltas.

    The trap here: ``assistantResponseEvent`` is **overloaded by field
    presence**, not by event name. The same event type carries text, tool
    starts, tool stops, tool input and usage depending on which key is set.
    Dispatching on ``:event-type`` alone silently loses tool calls.
    """
    if frame.message_type in ("error", "exception"):
        state.errored = True
        state.error_message = frame.payload.decode("utf-8", "replace")[:2000]
        return [Delta(kind="error", text=state.error_message)]
    if frame.message_type and frame.message_type != "event":
        return []

    payload = frame.json()
    event = frame.event_type
    out: list[Delta] = []

    if event == "reasoningContentEvent":
        text = payload.get("text")
        if isinstance(text, str) and text:
            state.emitted_chars += len(text)
            out.append(Delta(kind="reasoning", text=text))
        return out

    if event == "initial-response":
        # Only carries an (often empty) conversationId. Nothing to surface.
        return out

    if event == "metadataEvent":
        # The authoritative finish reason, e.g. END_TURN / TOOL_USE / MAX_TOKENS.
        reason = payload.get("stopReason")
        if isinstance(reason, str) and reason:
            state.stop_reason = reason
        return out

    if event in ("assistantResponseEvent", "toolUseEvent"):
        if "content" in payload:
            content = payload.get("content")
            if isinstance(content, str) and content:
                state.emitted_chars += len(content)
                out.append(Delta(kind="text", text=content))
            return out

        tool_id = str(payload.get("toolUseId") or "")

        if "stop" in payload and payload.get("stop"):
            if tool_id and tool_id in state.tool_calls:
                out.append(Delta(kind="tool_end", tool_id=tool_id))
            return out

        if "input" in payload:
            chunk = payload.get("input")
            if tool_id and isinstance(chunk, str):
                entry = state.tool_calls.get(tool_id)
                if entry is None:
                    entry = {"name": str(payload.get("name") or ""), "input": ""}
                    state.tool_calls[tool_id] = entry
                    state.tool_order.append(tool_id)
                    out.append(Delta(kind="tool_start", tool_id=tool_id, tool_name=entry["name"]))
                entry["input"] += chunk
                out.append(Delta(kind="tool_delta", tool_id=tool_id, text=chunk))
            return out

        if "name" in payload:
            name = str(payload.get("name") or "")
            if tool_id and tool_id not in state.tool_calls:
                state.tool_calls[tool_id] = {"name": name, "input": ""}
                state.tool_order.append(tool_id)
                out.append(Delta(kind="tool_start", tool_id=tool_id, tool_name=name))
            return out

        if "usage" in payload:
            # Seen on meteringEvent as a credit float; tolerated here in case the
            # service ever attaches it to a response event too.
            _absorb_credits(payload.get("usage"), state)
            return out
        return out

    if event == "meteringEvent":
        _absorb_credits(payload.get("usage"), state)
        return out

    if event == "contextUsageEvent":
        percent = payload.get("contextUsagePercentage")
        if isinstance(percent, (int, float)):
            state.context_usage_percent = float(percent)
        return out

    return []


def _absorb_credits(value: Any, state: StreamState) -> None:
    """Record billed credits. This is consumption, not tokens."""
    if isinstance(value, (int, float)):
        state.credits = float(value)


#: Rough characters-per-token ratio. Only used because Q reports no token counts
#: at all; good enough for context-budget decisions, wrong for billing.
_CHARS_PER_TOKEN = 4


def estimate_usage(state: StreamState, context_limit: int = 0) -> dict[str, int]:
    """Best-effort OpenAI-shaped ``usage`` block.

    **These are estimates, not measurements.** Q reports neither prompt nor
    completion tokens. What it does report is the share of the context window the
    prompt consumed, which is a real signal, so ``prompt_tokens`` is derived from
    that against the model's known limit. ``completion_tokens`` is derived from
    the characters actually streamed.

    Returning zeros instead would be more "honest" but actively harmful: Hermes
    drives context compression off these numbers, and a permanent zero would stop
    compression from ever triggering and let the conversation overflow.
    """
    prompt_tokens = 0
    if state.context_usage_percent is not None and context_limit > 0:
        prompt_tokens = int(round((state.context_usage_percent / 100.0) * context_limit))
    completion_tokens = max(1, state.emitted_chars // _CHARS_PER_TOKEN) if state.emitted_chars else 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def finish_reason(state: StreamState) -> str:
    """Map Q's ``stopReason`` onto the OpenAI vocabulary."""
    if state.errored:
        return "error"
    if state.tool_order:
        return "tool_calls"
    mapping = {
        "END_TURN": "stop",
        "TOOL_USE": "tool_calls",
        "MAX_TOKENS": "length",
        "STOP_SEQUENCE": "stop",
        "CONTENT_FILTERED": "content_filter",
    }
    return mapping.get(state.stop_reason.upper(), "stop")


def finalize_tool_calls(state: StreamState) -> list[dict[str, Any]]:
    """Assemble OpenAI ``tool_calls`` from whatever the stream accumulated.

    Emits even for a truncated stream, so a consumer never sees a
    ``finish_reason: tool_calls`` with no matching call attached.
    """
    calls: list[dict[str, Any]] = []
    for index, tool_id in enumerate(state.tool_order):
        entry = state.tool_calls.get(tool_id) or {}
        calls.append(
            {
                "index": index,
                "id": tool_id,
                "type": "function",
                "function": {"name": entry.get("name", ""), "arguments": entry.get("input", "") or "{}"},
            }
        )
    return calls
