"""Local OpenAI-compatible front end for the AWS Q endpoint Kiro uses.

Fork-owned. This exists purely as a protocol adapter. Kiro speaks AWS Q
``GenerateAssistantResponse`` with binary event-stream responses, which is none
of Hermes' five transports. Rather than add a sixth transport to core -- new
``api_mode``, new transport module, edits threaded through ``run_agent`` -- this
translates to plain ``chat/completions`` on loopback, so Hermes sees an ordinary
OpenAI-compatible provider and core stays untouched.

Security posture: binds to 127.0.0.1 only, and **still authenticates every
request**. The process holds a live cloud credential, so serving any local
process that happens to find the port would be wrong. Two accepted forms:

* ``Authorization: Bearer ksk_...`` -- a Kiro programmatic key, used as-is. The
  caller already holds the secret, so there is nothing to protect.
* ``Authorization: Bearer <session-secret>`` -- authorises use of the SSO token
  belonging to an installed Kiro. The secret is generated at startup and written
  0600 to the state file, so only this user can read it.

Anything else gets 401.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from . import client, wire
from .auth import API_KEY_PREFIX, KiroAuthError, resolve_token
from .catalog import context_limit_for, static_model_ids

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
#: Preferred fixed port so the persisted ``base_url`` stays valid across restarts.
#: Falls back to an OS-assigned port if this one is taken; the real port is always
#: recorded in the state file.
DEFAULT_PORT = 8779

_STATE_DIRNAME = "kiro-proxy"
_STATE_FILENAME = "proxy.json"


def state_path() -> Path:
    """Where the proxy publishes its port and session secret.

    Uses ``HERMES_HOME`` when set so profiles stay isolated, matching the
    convention the rest of the tree follows.
    """
    home = os.environ.get("HERMES_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".hermes"
    return base / _STATE_DIRNAME / _STATE_FILENAME


def read_state() -> Optional[dict[str, Any]]:
    """Load a running proxy's details, or None if there is no usable state."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("port") or not data.get("secret"):
        return None
    return data


def write_state(port: int, secret: str) -> None:
    target = state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"host": DEFAULT_HOST, "port": port, "secret": secret, "pid": os.getpid()})
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)


def base_url_for(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/v1"


class _Handler(BaseHTTPRequestHandler):
    server_version = "hermes-kiro-proxy/1.0"
    # Silence per-request stderr spam; real problems go through the logger.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
        logger.debug("kiro-proxy: " + fmt, *args)

    # --- helpers ---

    @property
    def secret(self) -> str:
        return getattr(self.server, "session_secret", "")

    def _credential(self):
        """Authenticate the caller and resolve a Kiro credential.

        Returns None and writes the error response when authentication fails.
        """
        header = self.headers.get("Authorization", "") or ""
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            self._json(401, {"error": {"message": "missing bearer token", "type": "invalid_request_error"}})
            return None
        if token.startswith(API_KEY_PREFIX):
            return resolve_token(token)
        if self.secret and secrets.compare_digest(token, self.secret):
            # Authorised to use the installed Kiro's SSO token.
            try:
                return resolve_token()
            except KiroAuthError as exc:
                self._json(401, {"error": {"message": str(exc), "type": "invalid_request_error", "code": exc.code}})
                return None
        self._json(401, {"error": {"message": "invalid bearer token", "type": "invalid_request_error"}})
        return None

    def _body(self) -> Optional[dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._json(400, {"error": {"message": "empty request body", "type": "invalid_request_error"}})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"error": {"message": f"malformed JSON: {exc}", "type": "invalid_request_error"}})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": {"message": "body must be a JSON object", "type": "invalid_request_error"}})
            return None
        return payload

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse_open(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Close rather than keep-alive. Each completion is a single response with
        # no Content-Length, so on a keep-alive connection a client that keeps
        # reading after `[DONE]` blocks until its own timeout. Closing makes the
        # end of the body unambiguous.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _sse(self, obj: dict[str, Any]) -> None:
        self.wfile.write(b"data: " + json.dumps(obj).encode("utf-8") + b"\n\n")
        self.wfile.flush()

    # --- routes ---

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/health", "/v1/health"):
            self._json(200, {"status": "ok", "pid": os.getpid()})
            return
        if path in ("/v1/models", "/models"):
            credential = self._credential()
            if credential is None:
                return
            ids = client.list_models(credential) or static_model_ids()
            self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": i, "object": "model", "owned_by": "kiro"} for i in ids],
                },
            )
            return
        self._json(404, {"error": {"message": f"no route {path}", "type": "invalid_request_error"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._json(404, {"error": {"message": f"no route {path}", "type": "invalid_request_error"}})
            return
        credential = self._credential()
        if credential is None:
            return
        payload = self._body()
        if payload is None:
            return

        model = str(payload.get("model") or "claude-sonnet-4.5")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            self._json(400, {"error": {"message": "messages must be a non-empty array", "type": "invalid_request_error"}})
            return
        stream = bool(payload.get("stream"))
        effort = ""
        reasoning = payload.get("reasoning_effort") or payload.get("reasoning")
        if isinstance(reasoning, str):
            effort = reasoning
        elif isinstance(reasoning, dict):
            effort = str(reasoning.get("effort") or "")

        try:
            body = wire.build_request_body(
                messages,
                model,
                tools=payload.get("tools") or None,
                effort=effort,
                conversation_id=str(payload.get("user") or "") or "",
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._json(400, {"error": {"message": f"could not build request: {exc}", "type": "invalid_request_error"}})
            return

        if stream:
            self._stream(credential, body, model)
        else:
            self._complete(credential, body, model)

    # --- transport ---

    def _drain(self, credential, body: dict[str, Any]):
        """Yield normalised deltas from a live Q call."""
        decoder = wire.EventStreamDecoder()
        state = wire.StreamState()
        for chunk in client.stream_chat(credential, body):
            for frame in decoder.feed(chunk):
                for delta in wire.translate_event(frame, state):
                    yield delta, state
        yield None, state

    def _stream(self, credential, body: dict[str, Any], model: str) -> None:
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        opened = False
        tool_index: dict[str, int] = {}

        def envelope(delta: dict[str, Any], finish: Optional[str] = None) -> dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        try:
            for item, state in self._drain(credential, body):
                if not opened:
                    self._sse_open()
                    opened = True
                    self._sse(envelope({"role": "assistant", "content": ""}))
                if item is None:
                    calls = wire.finalize_tool_calls(state)
                    self._sse(envelope({}, wire.finish_reason(state)))
                    usage = wire.estimate_usage(state, context_limit_for(model))
                    # usage_is_estimated is non-standard on purpose: it stops a
                    # consumer treating these numbers as billing truth.
                    self._sse(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [],
                            "usage": usage,
                            "usage_is_estimated": True,
                            "kiro_credits": state.credits,
                        }
                    )
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                if item.kind == "text":
                    self._sse(envelope({"content": item.text}))
                elif item.kind == "reasoning":
                    self._sse(envelope({"reasoning_content": item.text}))
                elif item.kind == "tool_start":
                    index = len(tool_index)
                    tool_index[item.tool_id] = index
                    self._sse(
                        envelope(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": item.tool_id,
                                        "type": "function",
                                        "function": {"name": item.tool_name, "arguments": ""},
                                    }
                                ]
                            }
                        )
                    )
                elif item.kind == "tool_delta":
                    index = tool_index.get(item.tool_id, 0)
                    self._sse(
                        envelope({"tool_calls": [{"index": index, "function": {"arguments": item.text}}]})
                    )
                elif item.kind == "error":
                    self._sse(envelope({}, "error"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
        except (KiroAuthError, client.KiroApiError) as exc:
            if not opened:
                status = 401 if isinstance(exc, KiroAuthError) else 502
                self._json(status, {"error": {"message": str(exc), "type": "upstream_error"}})
            else:
                # Headers already sent; the only honest signal left is the stream.
                self._sse(envelope({}, "error"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kiro-proxy: stream failed: %s", exc)
            if not opened:
                self._json(500, {"error": {"message": str(exc), "type": "internal_error"}})

    def _complete(self, credential, body: dict[str, Any], model: str) -> None:
        chunks: list[str] = []
        reasoning: list[str] = []
        try:
            for item, state in self._drain(credential, body):
                if item is None:
                    calls = wire.finalize_tool_calls(state)
                    message: dict[str, Any] = {"role": "assistant", "content": "".join(chunks) or None}
                    if reasoning:
                        message["reasoning_content"] = "".join(reasoning)
                    if calls:
                        message["tool_calls"] = [
                            {"id": c["id"], "type": "function", "function": c["function"]} for c in calls
                        ]
                    self._json(
                        200,
                        {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [
                                {"index": 0, "message": message, "finish_reason": wire.finish_reason(state)}
                            ],
                            "usage": wire.estimate_usage(state, context_limit_for(model)),
                            "usage_is_estimated": True,
                            "kiro_credits": state.credits,
                        },
                    )
                    return
                if item.kind == "text":
                    chunks.append(item.text)
                elif item.kind == "reasoning":
                    reasoning.append(item.text)
        except KiroAuthError as exc:
            self._json(401, {"error": {"message": str(exc), "type": "upstream_error", "code": exc.code}})
        except client.KiroApiError as exc:
            self._json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kiro-proxy: completion failed: %s", exc)
            self._json(500, {"error": {"message": str(exc), "type": "internal_error"}})


class KiroProxy:
    """Owns the loopback HTTP server."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, secret: str = ""):
        self.host = host
        self.requested_port = port
        self.secret = secret or secrets.token_urlsafe(32)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        #: Set when this handle points at a proxy owned by another process, in
        #: which case there is no local server object to read the port from.
        self._external_port: Optional[int] = None

    @property
    def port(self) -> int:
        if self._external_port is not None:
            return self._external_port
        return self._server.server_address[1] if self._server else 0

    @property
    def is_live(self) -> bool:
        """True when this handle refers to a reachable proxy, ours or adopted."""
        return self._server is not None or self._external_port is not None

    @property
    def base_url(self) -> str:
        return base_url_for(self.port)

    def start(self, *, publish: bool = True) -> "KiroProxy":
        try:
            server = ThreadingHTTPServer((self.host, self.requested_port), _Handler)
        except OSError:
            if self.requested_port == 0:
                raise
            # Preferred port taken (often a previous run, or something unrelated).
            # Fall back to an OS-assigned one; the state file records the truth.
            logger.info("kiro-proxy: port %s unavailable, using an ephemeral port", self.requested_port)
            server = ThreadingHTTPServer((self.host, 0), _Handler)
        server.daemon_threads = True
        server.session_secret = self.secret  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="kiro-proxy", daemon=True)
        self._thread.start()
        if publish:
            write_state(self.port, self.secret)
        logger.info("kiro-proxy listening on %s", self.base_url)
        return self

    def stop(self) -> None:
        """Shut down a proxy we own; merely release an adopted one.

        An adopted handle points at another process's server, so stopping it here
        would be an act of vandalism against a live session.
        """
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._external_port = None

    def __enter__(self) -> "KiroProxy":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# --- in-process singleton --------------------------------------------------

_singleton: Optional["KiroProxy"] = None
_singleton_lock = threading.Lock()


def ensure_running() -> "KiroProxy":
    """Start the proxy inside this process if it is not already up.

    Runs as a daemon thread rather than a subprocess: Hermes is already a Python
    process, so there is nothing to gain from a second one and plenty to lose --
    no orphan to reap, no PID file to go stale, no lifecycle to manage. It simply
    dies with Hermes.

    Idempotent and cheap after the first call, so it is safe to call on every
    request build.
    """
    global _singleton
    if _singleton is not None and _singleton.is_live:
        return _singleton
    with _singleton_lock:
        if _singleton is not None and _singleton.is_live:
            return _singleton
        port = DEFAULT_PORT
        override = (os.environ.get("KIRO_PROXY_PORT") or "").strip()
        if override.isdigit():
            port = int(override)

        # A second Hermes process may already be serving this port. That proxy is
        # this same implementation and authenticates per request, so adopting it
        # is correct and avoids both a port clash and a redundant server.
        adopted = _adopt_existing(port)
        if adopted is not None:
            _singleton = adopted
            return _singleton

        # Reuse the previously published secret rather than minting a new one.
        # For the installed-Kiro path this secret is what gets stored as the
        # provider credential, so a fresh one on every start would invalidate the
        # saved config and silently break the provider after a restart.
        previous = read_state()
        secret = str((previous or {}).get("secret") or "")

        _singleton = KiroProxy(port=port, secret=secret).start()
        return _singleton


def _adopt_existing(port: int) -> Optional["KiroProxy"]:
    """Return a handle to an already-running proxy on ``port``, if it is ours.

    Requires both a passing health check *and* a state file agreeing on the port,
    because without the recorded secret the installed-Kiro credential path could
    not be used through it. Refusing to adopt is the safe outcome -- the caller
    then binds its own port.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{DEFAULT_HOST}:{port}/health", timeout=2.0) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    state = read_state()
    if not state or int(state.get("port") or 0) != port:
        return None
    secret = str(state.get("secret") or "")
    if not secret:
        return None

    handle = KiroProxy(port=port, secret=secret)
    handle._external_port = port
    logger.info("kiro-proxy: adopted the instance already listening on port %s", port)
    return handle


def running_base_url() -> str:
    """Base URL of the in-process proxy, starting it if needed."""
    return ensure_running().base_url


def session_secret() -> str:
    """Secret that authorises the installed-Kiro credential path."""
    return ensure_running().secret


def serve_forever(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Blocking entry point for a standalone ``hermes kiro serve``."""
    proxy = KiroProxy(host=host, port=port).start()
    print(f"kiro proxy listening on {proxy.base_url}")
    print(f"state file: {state_path()}")
    print(f"session secret (authorises the installed-Kiro token): {proxy.secret}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        proxy.stop()
