"""Tests for the fork's Kiro provider.

Covers the three layers separately, because they fail in different ways:

* the profile and its wiring into Hermes (the "new provider is invisible" class
  of bug that ``test_upstage_provider.py`` documents),
* the wire translation, including the framing and the overloaded event dispatch,
* credential resolution and install detection.

Nothing here touches the network. The live end-to-end behaviour against AWS Q
cannot be asserted in CI without a real Kiro credential.
"""

from __future__ import annotations

import importlib
import json
import os
import struct
import sys
from pathlib import Path

import pytest


def _clear_provider_caches():
    """Force re-discovery on the next list_providers()/get_provider_profile()."""
    import providers as _pkg

    _pkg._REGISTRY.clear()
    _pkg._ALIASES.clear()
    _pkg._discovered = False
    for mod in list(sys.modules.keys()):
        if mod.startswith("plugins.model_providers") or mod.startswith("_hermes_user_provider"):
            del sys.modules[mod]


@pytest.fixture()
def profile():
    _clear_provider_caches()
    from providers import get_provider_profile

    found = get_provider_profile("kiro")
    assert found is not None, "kiro provider plugin did not register"
    return found


def _mod(name: str):
    return importlib.import_module(f"plugins.model_providers.kiro.{name}")


# --------------------------------------------------------------------------
# profile + wiring
# --------------------------------------------------------------------------


class TestProfile:
    def test_core_fields(self, profile):
        assert profile.name == "kiro"
        assert profile.api_mode == "chat_completions"
        assert profile.display_name == "Kiro"
        # api_key on purpose: it is what auto-wires the registry, env vars and the
        # desktop keys tab. The installed-Kiro path is reached through an explicit
        # dispatch branch instead of a bespoke auth_type.
        assert profile.auth_type == "api_key"
        assert profile.env_vars == ("KIRO_API_KEY", "KIRO_BASE_URL")
        assert profile.base_url.startswith("http://127.0.0.1:")

    def test_base_url_is_loopback(self, profile):
        """The provider must never point at a remote host.

        Kiro speaks AWS Q, so anything other than the local translator means the
        request would go somewhere that cannot understand it.
        """
        assert profile.base_url.startswith("http://127.0.0.1:")
        assert profile.base_url.endswith("/v1")

    def test_health_check_disabled(self, profile):
        # The translator has no /models route worth probing; a doctor probe would
        # report a working setup as broken.
        assert profile.supports_health_check is False

    def test_aliases_resolve(self):
        _clear_provider_caches()
        from providers import get_provider_profile

        for alias in ("kiro-ide", "amazon-kiro", "kiro-q"):
            assert get_provider_profile(alias) is not None, alias

    def test_fallback_models_present(self, profile):
        ids = list(profile.fallback_models)
        assert len(ids) >= 11
        assert "claude-opus-5" in ids
        assert "auto" in ids

    def test_max_tokens_known_and_unknown(self, profile):
        assert profile.get_max_tokens("claude-opus-5") == 128_000
        # An unknown id must degrade to a default rather than raise: the live
        # catalog can gain models at any time.
        assert profile.get_max_tokens("brand-new-model") == 64_000

    def test_fetch_models_survives_no_credential(self, profile, monkeypatch):
        monkeypatch.delenv("KIRO_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path(os.devnull).parent / "nonexistent"))
        # Must return None (caller falls back to the static catalog), not explode.
        assert profile.fetch_models(api_key=None) is None

    def test_build_extra_body_never_raises(self, profile, monkeypatch):
        """The per-request hook must not be able to break inference."""
        proxy = _mod("proxy")
        monkeypatch.setattr(proxy, "ensure_running", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert profile.build_extra_body(session_id="s") == {}


class TestHermesWiring:
    """The bug class from test_upstage_provider.py: registered but not resolvable."""

    def test_resolve_provider_full(self):
        from hermes_cli.providers import resolve_provider_full

        pdef = resolve_provider_full("kiro", {}, [])
        assert pdef is not None, "no HERMES_OVERLAYS entry -> saved provider gets discarded"
        assert pdef.id == "kiro"
        assert "KIRO_API_KEY" in pdef.api_key_env_vars

    def test_overlay_shape(self):
        from hermes_cli.providers import HERMES_OVERLAYS

        overlay = HERMES_OVERLAYS["kiro"]
        assert overlay.transport == "openai_chat"
        assert overlay.extra_env_vars == ("KIRO_API_KEY",)
        assert overlay.base_url_override.startswith("http://127.0.0.1:")
        assert overlay.base_url_env_var == "KIRO_BASE_URL"

    def test_overlay_and_profile_agree_on_port(self):
        """A mismatch here sends Hermes to a port nothing is listening on."""
        from hermes_cli.providers import HERMES_OVERLAYS
        from providers import get_provider_profile

        _clear_provider_caches()
        assert HERMES_OVERLAYS["kiro"].base_url_override == get_provider_profile("kiro").base_url
        assert _mod("proxy").DEFAULT_PORT == 8779

    def test_label(self):
        from hermes_cli.providers import get_label

        assert get_label("kiro") == "Kiro"

    def test_in_picker_universe(self):
        from hermes_cli.models import CANONICAL_PROVIDERS

        entry = next((p for p in CANONICAL_PROVIDERS if p.slug == "kiro"), None)
        assert entry is not None, "kiro would not appear in `hermes model`"
        assert entry.label == "Kiro"

    def test_env_vars_declared_for_gui(self):
        from hermes_cli.config import OPTIONAL_ENV_VARS

        key = OPTIONAL_ENV_VARS["KIRO_API_KEY"]
        # category=provider is load-bearing: the desktop Providers tab filters on
        # it, and test_provider_parity asserts the tabs cover CANONICAL_PROVIDERS.
        assert key["category"] == "provider"
        assert key["password"] is True
        assert OPTIONAL_ENV_VARS["KIRO_BASE_URL"]["password"] is False

    def test_setup_flow_is_importable(self):
        from hermes_cli.model_setup_flows import _model_flow_kiro

        assert callable(_model_flow_kiro)


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------


class TestCatalog:
    def test_known_model_limits(self):
        catalog = _mod("catalog")
        assert catalog.context_limit_for("claude-opus-5") == 640_000
        assert catalog.info_for("claude-opus-5").measured is True

    def test_unknown_model_defaults(self):
        catalog = _mod("catalog")
        info = catalog.info_for("who-knows")
        assert info.context == catalog.DEFAULT_CONTEXT
        assert info.measured is False

    def test_gpt_models_have_no_vision(self):
        catalog = _mod("catalog")
        # Measured: Q returns REQUEST_BODY_INVALID for images on gpt ids.
        assert catalog.supports_vision("gpt-5.6-sol") is False
        assert catalog.supports_vision("claude-opus-5") is True

    def test_default_model_is_first(self):
        catalog = _mod("catalog")
        assert catalog.default_model() == catalog.static_model_ids()[0]


# --------------------------------------------------------------------------
# wire: framing
# --------------------------------------------------------------------------


def _frame(headers: dict, payload: bytes) -> bytes:
    hb = bytearray()
    for name, value in headers.items():
        nb, vb = name.encode(), value.encode()
        hb.append(len(nb))
        hb += nb
        hb.append(7)
        hb += struct.pack(">H", len(vb))
        hb += vb
    total = 12 + len(hb) + len(payload) + 4
    return struct.pack(">I", total) + struct.pack(">I", len(hb)) + b"\0" * 4 + bytes(hb) + payload + b"\0" * 4


def _event(event_type: str, obj: dict) -> bytes:
    return _frame({":message-type": "event", ":event-type": event_type}, json.dumps(obj).encode())


class TestFraming:
    def test_single_frame(self):
        wire = _mod("wire")
        frames = list(wire.EventStreamDecoder().feed(_event("assistantResponseEvent", {"content": "hi"})))
        assert len(frames) == 1
        assert frames[0].event_type == "assistantResponseEvent"
        assert frames[0].json() == {"content": "hi"}

    def test_survives_arbitrary_chunk_boundaries(self):
        """The failure mode of a naive parser: frames split across reads."""
        wire = _mod("wire")
        blob = _event("assistantResponseEvent", {"content": "AB"}) + _event(
            "assistantResponseEvent", {"content": "CD"}
        )
        decoder = wire.EventStreamDecoder()
        seen = []
        for i in range(0, len(blob), 3):
            seen += [f.json().get("content") for f in decoder.feed(blob[i : i + 3])]
        assert seen == ["AB", "CD"]
        assert decoder.pending_bytes == 0

    def test_rejects_implausible_length(self):
        wire = _mod("wire")
        with pytest.raises(ValueError):
            list(wire.EventStreamDecoder().feed(struct.pack(">I", 3) + b"\0" * 20))

    def test_partial_frame_buffers_without_yielding(self):
        wire = _mod("wire")
        raw = _event("assistantResponseEvent", {"content": "x"})
        decoder = wire.EventStreamDecoder()
        assert list(decoder.feed(raw[:-2])) == []
        assert decoder.pending_bytes > 0
        assert len(list(decoder.feed(raw[-2:]))) == 1


class TestEventTranslation:
    """assistantResponseEvent is overloaded by field presence, not event name."""

    def _one(self, event_type, obj, state=None):
        wire = _mod("wire")
        st = state or wire.StreamState()
        return wire.translate_event(wire.Frame({":message-type": "event", ":event-type": event_type}, json.dumps(obj).encode()), st), st

    def test_content_is_text(self):
        deltas, _ = self._one("assistantResponseEvent", {"content": "hello"})
        assert [(d.kind, d.text) for d in deltas] == [("text", "hello")]

    def test_tool_lifecycle(self):
        wire = _mod("wire")
        st = wire.StreamState()
        self._one("toolUseEvent", {"toolUseId": "t1", "name": "read"}, st)
        self._one("toolUseEvent", {"toolUseId": "t1", "input": '{"p":1}'}, st)
        self._one("toolUseEvent", {"toolUseId": "t1", "stop": True}, st)
        calls = wire.finalize_tool_calls(st)
        assert calls[0]["id"] == "t1"
        assert calls[0]["function"] == {"name": "read", "arguments": '{"p":1}'}

    def test_metadata_event_sets_finish_reason(self):
        wire = _mod("wire")
        _, st = self._one("metadataEvent", {"stopReason": "END_TURN"})
        assert st.stop_reason == "END_TURN"
        assert wire.finish_reason(st) == "stop"

    def test_tool_use_stop_reason(self):
        wire = _mod("wire")
        _, st = self._one("metadataEvent", {"stopReason": "TOOL_USE"})
        assert wire.finish_reason(st) == "tool_calls"

    def test_context_usage_and_credits(self):
        wire = _mod("wire")
        st = wire.StreamState()
        self._one("contextUsageEvent", {"contextUsagePercentage": 10.0}, st)
        self._one("meteringEvent", {"unit": "credit", "usage": 0.5}, st)
        assert st.context_usage_percent == 10.0
        assert st.credits == 0.5

    def test_error_frame_flags_state(self):
        wire = _mod("wire")
        st = wire.StreamState()
        deltas = wire.translate_event(wire.Frame({":message-type": "exception"}, b"ValidationException"), st)
        assert deltas[0].kind == "error"
        assert st.errored is True
        assert wire.finish_reason(st) == "error"

    def test_usage_is_estimated_from_percentage(self):
        """Q reports no token counts; prompt tokens come from the percentage."""
        wire = _mod("wire")
        st = wire.StreamState()
        st.context_usage_percent = 10.0
        st.emitted_chars = 40
        usage = wire.estimate_usage(st, 200_000)
        assert usage["prompt_tokens"] == 20_000
        assert usage["completion_tokens"] == 10

    def test_usage_without_context_limit_is_zero_not_wrong(self):
        wire = _mod("wire")
        st = wire.StreamState()
        st.context_usage_percent = 10.0
        assert wire.estimate_usage(st, 0)["prompt_tokens"] == 0


# --------------------------------------------------------------------------
# wire: request construction
# --------------------------------------------------------------------------


class TestRequestBody:
    def test_no_system_role_is_emitted(self):
        """Q has no system role; multiple system messages return empty output."""
        wire = _mod("wire")
        body = wire.build_request_body(
            [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}], "claude-opus-5"
        )
        blob = json.dumps(body)
        assert '"role"' not in blob
        assert body["conversationState"]["currentMessage"]["userInputMessage"]["content"] == "SYS\nhi"

    def test_system_folds_into_first_history_turn(self):
        wire = _mod("wire")
        body = wire.build_request_body(
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ],
            "claude-opus-5",
        )
        state = body["conversationState"]
        assert state["history"][0]["userInputMessage"]["content"] == "SYS\none"
        assert state["currentMessage"]["userInputMessage"]["content"] == "three"

    def test_trailing_tool_results_are_hoisted(self):
        wire = _mod("wire")
        body = wire.build_request_body(
            [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "t1", "function": {"name": "read", "arguments": '{"a":1}'}}],
                },
                {"role": "tool", "tool_call_id": "t1", "content": "result"},
            ],
            "claude-opus-5",
        )
        current = body["conversationState"]["currentMessage"]["userInputMessage"]
        assert current["userInputMessageContext"]["toolResults"][0]["toolUseId"] == "t1"
        # Empty content is illegal on the wire.
        assert current["content"] == " "

    def test_empty_assistant_gets_placeholder(self):
        wire = _mod("wire")
        body = wire.build_request_body(
            [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "b"},
            ],
            "claude-opus-5",
        )
        assert body["conversationState"]["history"][1]["assistantResponseMessage"]["content"] == "(empty)"

    def test_tool_schema_envelope(self):
        wire = _mod("wire")
        body = wire.build_request_body(
            [{"role": "user", "content": "x"}],
            "claude-opus-5",
            tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        )
        spec = body["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]["tools"][0]
        assert list(spec) == ["toolSpecification"]
        assert spec["toolSpecification"]["inputSchema"] == {"json": {"type": "object"}}

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus-5", {"output_config": {"effort": "high"}}),
            ("gpt-5.6-sol", {"reasoning": {"effort": "high"}}),
        ],
    )
    def test_effort_shape_per_model_family(self, model, expected):
        """Sending the wrong shape 400s the whole request."""
        wire = _mod("wire")
        body = wire.build_request_body([{"role": "user", "content": "x"}], model, effort="high")
        assert body["additionalModelRequestFields"] == expected

    def test_invalid_effort_is_dropped_not_sent(self):
        wire = _mod("wire")
        body = wire.build_request_body([{"role": "user", "content": "x"}], "claude-opus-5", effort="turbo")
        assert "additionalModelRequestFields" not in body

    def test_images_dropped_for_gpt_models(self):
        wire = _mod("wire")
        content = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}},
        ]
        claude = wire.build_request_body([{"role": "user", "content": content}], "claude-opus-5")
        gpt = wire.build_request_body([{"role": "user", "content": content}], "gpt-5.6-sol")
        assert "images" in claude["conversationState"]["currentMessage"]["userInputMessage"]
        assert "images" not in gpt["conversationState"]["currentMessage"]["userInputMessage"]

    def test_undecodable_image_is_skipped(self):
        wire = _mod("wire")
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!not-base64!!!"}}]
        body = wire.build_request_body([{"role": "user", "content": content}], "claude-opus-5")
        assert "images" not in body["conversationState"]["currentMessage"]["userInputMessage"]


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class TestAuth:
    def test_explicit_key_wins_over_env(self, monkeypatch):
        auth = _mod("auth")
        monkeypatch.setenv("KIRO_API_KEY", "ksk_env")
        assert auth.resolve_token("ksk_explicit").source == "explicit"

    def test_env_key_used_when_no_explicit(self, monkeypatch):
        auth = _mod("auth")
        monkeypatch.setenv("KIRO_API_KEY", "ksk_env")
        credential = auth.resolve_token()
        assert credential.source == "env"
        assert credential.is_api_key is True

    def test_api_key_prefix_detection(self):
        auth = _mod("auth")
        assert auth.resolve_token("ksk_abc").is_api_key is True
        # An SSO bearer token must NOT get the tokentype header.
        assert auth.ResolvedCredential(token="eyJhb", source="kiro-ide").is_api_key is False

    def test_missing_token_file_is_actionable(self, monkeypatch, tmp_path):
        auth = _mod("auth")
        monkeypatch.delenv("KIRO_API_KEY", raising=False)
        monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "nope.json")
        with pytest.raises(auth.KiroAuthError) as exc:
            auth.read_token_file()
        assert exc.value.code == "kiro_token_missing"
        assert exc.value.relogin_required is True

    def test_malformed_token_file(self, monkeypatch, tmp_path):
        auth = _mod("auth")
        bad = tmp_path / "kiro-auth-token.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(auth.KiroAuthError) as exc:
            auth.read_token_file(bad)
        assert exc.value.code == "kiro_token_unreadable"

    def test_token_without_access_token_rejected(self, tmp_path):
        auth = _mod("auth")
        path = tmp_path / "t.json"
        path.write_text(json.dumps({"refreshToken": "r"}), encoding="utf-8")
        with pytest.raises(auth.KiroAuthError) as exc:
            auth.read_token_file(path)
        assert exc.value.code == "kiro_token_incomplete"

    def test_expiry_buffer(self):
        auth = _mod("auth")
        assert auth.StoredToken(access_token="a", expires_at="2099-01-01T00:00:00Z").is_expiring() is False
        assert auth.StoredToken(access_token="a", expires_at="2000-01-01T00:00:00Z").is_expiring() is True

    def test_missing_expiry_treated_as_expiring(self):
        """Better to attempt a refresh than send a token we cannot reason about."""
        auth = _mod("auth")
        assert auth.StoredToken(access_token="a").is_expiring() is True

    def test_region_is_validated_before_url_interpolation(self):
        auth = _mod("auth")
        for bad in ("../evil", "us-east-1/../x", "", "US-EAST-1"):
            with pytest.raises(auth.KiroAuthError):
                auth._validate_region(bad)
        assert auth._validate_region("eu-central-1") == "eu-central-1"

    def test_client_id_hash_path_traversal_rejected(self, tmp_path, monkeypatch):
        auth = _mod("auth")
        monkeypatch.setattr(auth, "sso_cache_dir", lambda: tmp_path)
        token = auth.StoredToken(access_token="a", client_id_hash="../../etc/passwd")
        with pytest.raises(auth.KiroAuthError) as exc:
            auth._resolve_client(token)
        assert exc.value.code == "kiro_token_unreadable"

    def test_refresh_without_refresh_token_is_actionable(self):
        auth = _mod("auth")
        with pytest.raises(auth.KiroAuthError) as exc:
            auth.refresh_token(auth.StoredToken(access_token="a", region="us-east-1"))
        assert exc.value.relogin_required is True

    def test_token_roundtrip_preserves_unknown_fields(self):
        """A refresh must not destroy fields written by a newer Kiro."""
        auth = _mod("auth")
        raw = {"accessToken": "a", "refreshToken": "r", "someFutureField": {"x": 1}}
        token = auth.StoredToken.from_dict(raw)
        assert token.extra["someFutureField"] == {"x": 1}
        assert token.to_dict()["someFutureField"] == {"x": 1}

    def test_auth_status_never_raises_and_hides_the_token(self, monkeypatch, tmp_path):
        auth = _mod("auth")
        monkeypatch.setattr(auth, "token_path", lambda: tmp_path / "absent.json")
        status = auth.auth_status()
        assert status["signed_in"] is False
        assert "accessToken" not in json.dumps(status)
        assert isinstance(status["installs"], list)

    def test_detect_installs_returns_list(self):
        # Machine-dependent, so only the contract is asserted.
        auth = _mod("auth")
        for install in auth.detect_installs():
            assert install.kind in ("ide", "cli")
            assert install.path.exists()


# --------------------------------------------------------------------------
# proxy
# --------------------------------------------------------------------------


@pytest.fixture()
def proxy_server():
    proxy = _mod("proxy")
    server = proxy.KiroProxy(port=0).start(publish=False)
    try:
        yield server
    finally:
        server.stop()


class TestProxy:
    def _get(self, server, path, token=""):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(f"{server.base_url}{path}")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_health_needs_no_auth(self, proxy_server):
        status, _ = self._get(proxy_server, "/health")
        assert status == 200

    # base_url already ends in /v1, so paths here are relative to that.
    def test_rejects_missing_token(self, proxy_server):
        status, body = self._get(proxy_server, "/models")
        assert status == 401
        assert "missing bearer token" in body

    def test_rejects_wrong_token(self, proxy_server):
        status, _ = self._get(proxy_server, "/models", token="not-the-secret")
        assert status == 401

    def test_unknown_route(self, proxy_server):
        status, _ = self._get(proxy_server, "/nope", token=proxy_server.secret)
        assert status == 404

    def test_secret_is_not_guessable(self, proxy_server):
        assert len(proxy_server.secret) >= 32

    def test_binds_loopback_only(self, proxy_server):
        assert proxy_server.host == "127.0.0.1"
        assert proxy_server.base_url.startswith("http://127.0.0.1:")

    def test_stop_does_not_kill_an_adopted_server(self):
        """An adopted handle points at another process; stopping must not shut it."""
        proxy = _mod("proxy")
        handle = proxy.KiroProxy(port=9999)
        handle._external_port = 9999
        assert handle.is_live is True
        handle.stop()  # must be a no-op beyond releasing our reference
        assert handle.is_live is False
