"""HTTP client for the AWS Q endpoint Kiro talks to.

Fork-owned. Endpoints, headers and the target name are ported from windro's
Syncode fork; none of it is publicly documented.

One host, one path, everything dispatched by the ``X-Amz-Target`` header:

* ``AmazonCodeWhispererStreamingService.GenerateAssistantResponse`` -- chat
* ``AmazonCodeWhispererService.ListAvailableModels`` -- region probe / catalog

Note the two targets use *different* service prefixes against the same host.
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterator, Optional

from .auth import (
    CANDIDATE_REGIONS,
    DEFAULT_REGION,
    KiroAuthError,
    ResolvedCredential,
    _validate_region,
)

logger = logging.getLogger(__name__)

CHAT_TARGET = "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"
MODELS_TARGET = "AmazonCodeWhispererService.ListAvailableModels"

#: The ``aws-sdk-js/1.0.27`` prefix is load-bearing -- the service inspects it.
#: Only the trailing client identifier is ours to set. If requests start being
#: rejected, suspect a change here first.
_UA = (
    "aws-sdk-js/1.0.27 ua/2.1 os/{platform} lang/js "
    "api/codewhispererstreaming#1.0.27 m/E hermes-kiro"
)

_CONNECT_TIMEOUT = 30.0
_STREAM_CHUNK = 16384

# Cached region probe result, keyed by nothing: it is a property of the account,
# and re-probing on every request costs a round trip for no benefit.
_region_cache: dict[str, str] = {}


class KiroApiError(Exception):
    """Non-2xx from the Q endpoint."""

    def __init__(self, status: int, body: str):
        super().__init__(f"Kiro API error {status}: {body[:500]}")
        self.status = status
        self.body = body


def build_headers(credential: ResolvedCredential, target: str) -> dict[str, str]:
    """Headers for a Q call.

    ``tokentype: API_KEY`` is required for ``ksk_`` programmatic keys and must be
    absent for SSO bearer tokens.
    """
    headers = {
        "Authorization": f"Bearer {credential.token}",
        "Content-Type": "application/x-amz-json-1.0",
        "X-Amz-Target": target,
        "User-Agent": _UA.format(platform=sys.platform),
        "x-amz-user-agent": "aws-sdk-js/1.0.27 hermes-kiro",
        "x-amzn-codewhisperer-optout": "true",
        "x-amzn-kiro-agent-mode": "vibe",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        # The service is told there is exactly one attempt; this client does not
        # retry, matching the reference implementation.
        "amz-sdk-request": "attempt=1; max=1",
    }
    if credential.is_api_key:
        headers["tokentype"] = "API_KEY"
    return headers


def endpoint(region: str) -> str:
    return f"https://q.{_validate_region(region)}.amazonaws.com/"


def probe_region(credential: ResolvedCredential, region: str, *, timeout: float = 15.0) -> bool:
    """True when this credential can reach Q in ``region``."""
    request = urllib.request.Request(
        endpoint(region),
        data=json.dumps({"origin": "AI_EDITOR"}).encode("utf-8"),
        method="POST",
        headers=build_headers(credential, MODELS_TARGET),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        # A 4xx here still proves the region answers for this account only when
        # it is not an auth failure; treat auth failures as "wrong region".
        return exc.code not in (401, 403, 404)
    except Exception:
        return False


def resolve_region(credential: ResolvedCredential, *, explicit: str = "") -> str:
    """Pick the Q region, probing candidates once and caching the answer."""
    if explicit:
        return _validate_region(explicit)
    if credential.region:
        return _validate_region(credential.region)
    cached = _region_cache.get("api")
    if cached:
        return cached
    for candidate in CANDIDATE_REGIONS:
        if probe_region(credential, candidate):
            _region_cache["api"] = candidate
            logger.debug("kiro: using region %s", candidate)
            return candidate
    _region_cache["api"] = DEFAULT_REGION
    return DEFAULT_REGION


def stream_chat(
    credential: ResolvedCredential,
    body: dict[str, Any],
    *,
    region: str = "",
    timeout: float = _CONNECT_TIMEOUT,
) -> Iterator[bytes]:
    """POST a chat request and yield raw response bytes as they arrive.

    Yields the undecoded AWS event-stream; framing is the caller's job (see
    :class:`wire.EventStreamDecoder`). Streaming is the only mode -- there is no
    non-streaming endpoint.
    """
    resolved = resolve_region(credential, explicit=region)
    request = urllib.request.Request(
        endpoint(resolved),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=build_headers(credential, CHAT_TARGET),
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            pass
        if exc.code in (401, 403):
            raise KiroAuthError(
                f"Kiro rejected the credential (HTTP {exc.code}). {detail[:300]}".strip(),
                code="kiro_unauthorized",
                relogin_required=True,
            ) from exc
        raise KiroApiError(exc.code, detail) from exc
    except Exception as exc:
        raise KiroApiError(0, f"could not reach the Kiro endpoint: {exc}") from exc

    with response:
        while True:
            chunk = response.read(_STREAM_CHUNK)
            if not chunk:
                return
            yield chunk


def list_models(credential: ResolvedCredential, *, region: str = "", timeout: float = 15.0) -> Optional[list[str]]:
    """Ask Q which models this account may use.

    Returns ``None`` on any failure so callers can fall back to the static
    catalog rather than showing an empty picker.
    """
    resolved = resolve_region(credential, explicit=region)
    request = urllib.request.Request(
        endpoint(resolved),
        data=json.dumps({"origin": "AI_EDITOR"}).encode("utf-8"),
        method="POST",
        headers=build_headers(credential, MODELS_TARGET),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("kiro: ListAvailableModels failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    # The response shape is not documented; accept the plausible spellings and
    # fall back to None rather than guessing wrong.
    for key in ("models", "availableModels", "modelSummaries"):
        entries = payload.get(key)
        if isinstance(entries, list):
            ids: list[str] = []
            for entry in entries:
                if isinstance(entry, str):
                    ids.append(entry)
                elif isinstance(entry, dict):
                    value = entry.get("modelId") or entry.get("id") or entry.get("name")
                    if isinstance(value, str) and value:
                        ids.append(value)
            if ids:
                return ids
    return None
