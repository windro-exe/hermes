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
        # `auto` was asserted here originally. It is now deliberately hidden — see
        # TestCatalog.test_auto_is_hidden.
        assert "auto" not in ids

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


class TestProviderSplit:
    """Two providers, one credential source each.

    Cramming a pasted key and a detected install behind one provider meant the
    desktop could only render a text box, because ``provider_catalog`` routes
    tabs purely on ``auth_type``. Split so each lands in its correct tab with no
    bespoke GUI work.
    """

    def test_both_registered_with_distinct_auth_types(self):
        _clear_provider_caches()
        from providers import get_provider_profile

        key_provider = get_provider_profile("kiro")
        ide_provider = get_provider_profile("kiro-ide")
        assert key_provider is not None and ide_provider is not None
        assert key_provider.auth_type == "api_key"
        # external_process is what routes to the Accounts tab.
        assert ide_provider.auth_type == "external_process"

    def test_they_are_not_the_same_object(self):
        _clear_provider_caches()
        from providers import get_provider_profile

        assert get_provider_profile("kiro") is not get_provider_profile("kiro-ide")

    def test_kiro_ide_is_no_longer_an_alias_of_kiro(self):
        """It used to be. If it regresses, the Accounts entry silently vanishes."""
        _clear_provider_caches()
        from providers import get_provider_profile

        assert get_provider_profile("kiro-ide").name == "kiro-ide"
        assert "kiro-ide" not in get_provider_profile("kiro").aliases

    def test_aliases_route_to_the_right_provider(self):
        _clear_provider_caches()
        from providers import get_provider_profile

        assert get_provider_profile("kiro-api").name == "kiro"
        assert get_provider_profile("kiro-cli").name == "kiro-ide"
        assert get_provider_profile("kiro-desktop").name == "kiro-ide"

    def test_tab_routing(self):
        from hermes_cli.provider_catalog import provider_catalog

        rows = {d.slug: d.tab for d in provider_catalog()}
        assert rows.get("kiro") == "keys"
        assert rows.get("kiro-ide") == "accounts"

    def test_both_appear_in_the_picker(self):
        """kiro-ide needs an EXPLICIT CANONICAL_PROVIDERS entry.

        external_process is in the auto-inject skip list, so without the explicit
        row it would be a registered provider that is invisible everywhere -- and
        the parity test would not catch it, since that asserts the GUI covers the
        picker, not the reverse.
        """
        from hermes_cli.models import CANONICAL_PROVIDERS

        slugs = {p.slug for p in CANONICAL_PROVIDERS}
        assert {"kiro", "kiro-ide"} <= slugs

    def test_both_resolvable_with_their_own_credential_var(self):
        from hermes_cli.providers import resolve_provider_full

        key_def = resolve_provider_full("kiro", {}, [])
        ide_def = resolve_provider_full("kiro-ide", {}, [])
        assert key_def is not None and ide_def is not None
        assert "KIRO_API_KEY" in key_def.api_key_env_vars
        assert "KIRO_IDE_TOKEN" in ide_def.api_key_env_vars

    def test_both_share_the_one_translator(self):
        """One proxy serves both; it picks the credential from the bearer token."""
        _clear_provider_caches()
        from hermes_cli.providers import HERMES_OVERLAYS
        from providers import get_provider_profile

        assert get_provider_profile("kiro").base_url == get_provider_profile("kiro-ide").base_url
        assert (
            HERMES_OVERLAYS["kiro"].base_url_override
            == HERMES_OVERLAYS["kiro-ide"].base_url_override
        )

    def test_labels(self):
        from hermes_cli.providers import get_label

        assert get_label("kiro") == "Kiro"
        assert get_label("kiro-ide") == "Kiro IDE"

    def test_ide_provider_ignores_a_passed_api_key(self):
        """Critical: KIRO_IDE_TOKEN is the PROXY secret, not a Kiro credential.

        Forwarding it to AWS as a bearer would leak a local secret to a third
        party and fail the request. fetch_models must resolve from disk instead.
        """
        _clear_provider_caches()
        from providers import get_provider_profile

        ide = get_provider_profile("kiro-ide")
        auth = _mod("auth")
        client = _mod("client")

        # Both are stubbed so the assertion cannot pass merely because this
        # machine has no SSO token and the call never reaches the client.
        resolve_calls: list[tuple] = []
        sent_tokens: list[str] = []

        fake_credential = auth.ResolvedCredential(token="sso-token-from-disk", source="kiro-ide")

        original_resolve = auth.resolve_token
        original_list = client.list_models
        try:
            def spy_resolve(explicit_key="", **kwargs):
                resolve_calls.append((explicit_key, kwargs))
                return fake_credential

            auth.resolve_token = spy_resolve
            client.list_models = lambda cred, **kw: sent_tokens.append(cred.token) or ["m"]
            result = ide.fetch_models(api_key="proxy-session-secret-not-a-kiro-key")
        finally:
            auth.resolve_token = original_resolve
            client.list_models = original_list

        assert result == ["m"], "the stubbed path must actually have run"
        assert sent_tokens == ["sso-token-from-disk"]
        assert "proxy-session-secret-not-a-kiro-key" not in sent_tokens
        # And it must not have been forwarded as the explicit key either.
        assert resolve_calls and resolve_calls[0][0] in ("", None)

    def test_both_setup_flows_exist(self):
        from hermes_cli.model_setup_flows import _model_flow_kiro, _model_flow_kiro_ide

        assert callable(_model_flow_kiro)
        assert callable(_model_flow_kiro_ide)


class TestInferenceWiring:
    """The registries that only an end-to-end run catches.

    A provider can register, resolve, sit in the picker and on the right tab with
    every unit test green, and still be refused at inference time. Each of these
    was found by running a real prompt, not by assertion.
    """

    def test_in_auth_provider_registry(self):
        """PROVIDER_REGISTRY is the gate that raises "Unknown provider"."""
        from hermes_cli.auth import PROVIDER_REGISTRY

        entry = PROVIDER_REGISTRY.get("kiro-ide")
        assert entry is not None, 'missing here => "Unknown provider" at inference time'
        assert entry.auth_type == "external_process"
        assert "KIRO_IDE_TOKEN" in entry.api_key_env_vars
        # `kiro` is auto-extended (api_key) and must not be hand-added.
        assert PROVIDER_REGISTRY.get("kiro") is not None

    def test_runtime_provider_resolves_it(self, monkeypatch):
        """resolve_runtime_provider populates api_key; without a branch there it
        stays empty and surfaces as "No LLM provider configured"."""
        import hermes_cli.runtime_provider as rp

        # Patch on runtime_provider, not on auth: it does
        # `from hermes_cli.auth import resolve_external_process_provider_credentials`,
        # so the name is bound in this module and patching the source has no effect.
        monkeypatch.setattr(
            rp,
            "resolve_external_process_provider_credentials",
            lambda pid: {
                "provider": pid,
                "api_key": "session-secret",
                "base_url": "http://127.0.0.1:8779/v1",
                "source": "kiro-ide",
            },
        )
        runtime = rp.resolve_runtime_provider(requested="kiro-ide")
        assert runtime["provider"] == "kiro-ide"
        assert runtime["api_mode"] == "chat_completions"
        assert runtime["api_key"] == "session-secret"
        assert runtime["base_url"] == "http://127.0.0.1:8779/v1"
        # Nothing is spawned, unlike copilot-acp.
        assert not runtime.get("command")

    def test_credentials_raise_actionably_when_not_signed_in(self, monkeypatch):
        import hermes_cli.auth as auth_mod

        monkeypatch.setattr(
            auth_mod,
            "get_kiro_ide_status",
            lambda: {"installed": True, "logged_in": False, "token_file": "/tmp/t.json"},
        )
        with pytest.raises(auth_mod.AuthError) as exc:
            auth_mod.resolve_external_process_provider_credentials("kiro-ide")
        assert exc.value.code == "kiro_not_signed_in"
        # Must name the alternative, or the user is stuck.
        assert "api key" in str(exc.value).lower()

    def test_credentials_raise_actionably_when_not_installed(self, monkeypatch):
        import hermes_cli.auth as auth_mod

        monkeypatch.setattr(
            auth_mod, "get_kiro_ide_status", lambda: {"installed": False, "logged_in": False}
        )
        with pytest.raises(auth_mod.AuthError) as exc:
            auth_mod.resolve_external_process_provider_credentials("kiro-ide")
        assert exc.value.code == "kiro_not_installed"


class TestStreamTimeout:
    """A streaming chat call must tolerate a long silence before first bytes.

    urllib applies `timeout` to every socket operation, not the whole request, so
    the value is effectively an inactivity limit. It was 30s, and measured turns at
    ~123K input took 29.2s and 36.3s before responding — so real calls were killed
    mid-flight and surfaced as "HTTP 500: The read operation timed out" and
    "HTTP 502: ... could not reach the Kiro endpoint: The read operation timed out".
    """

    def test_stream_default_is_the_long_timeout(self):
        import inspect

        client = _mod("client")

        default = inspect.signature(client.stream_chat).parameters["timeout"].default
        assert default == client._STREAM_READ_TIMEOUT
        assert default != client._CONNECT_TIMEOUT

    def test_stream_timeout_survives_a_slow_model(self):
        """Must exceed the slowest observed first-byte latency by a wide margin."""
        client = _mod("client")

        # 36.3s was measured on a real 123K-token turn; a limit anywhere near that
        # kills legitimate calls.
        assert client._STREAM_READ_TIMEOUT >= 120.0

    def test_short_calls_keep_the_short_timeout(self):
        """Region probe and model listing must NOT wait minutes on a dead endpoint."""
        import inspect

        client = _mod("client")

        assert inspect.signature(client.list_models).parameters["timeout"].default <= 60.0
        assert inspect.signature(client.probe_region).parameters["timeout"].default <= 60.0

    def test_stream_passes_its_timeout_to_urlopen(self, monkeypatch):
        """The value has to reach the socket, not just sit in a constant."""
        client = _mod("client")
        auth = _mod("auth")

        seen: dict = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            raise RuntimeError("stop here — only the timeout matters")

        monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
        credential = auth.ResolvedCredential(token="ksk_x", source="explicit")

        with pytest.raises(Exception):
            list(client.stream_chat(credential, {"conversationState": {}}, region="us-east-1"))

        assert seen["timeout"] == client._STREAM_READ_TIMEOUT


class TestProxyPortCollision:
    """A silent port fallback guarantees a confusing 401.

    The provider's base_url is pinned to 127.0.0.1:8779 in HERMES_OVERLAYS, so it
    cannot follow the server to another port. Binding elsewhere means Hermes keeps
    dialling 8779, reaches a foreign proxy, and gets "invalid bearer token".
    Measured with two Hermes instances on separate HERMES_HOMEs.
    """

    def test_taken_fixed_port_raises_instead_of_rebinding(self, monkeypatch):
        """The guard must fire on a PRE-BIND probe, not on bind failure.

        On Windows HTTPServer's allow_reuse_address lets a second bind to an
        already-listening port succeed, so a bind-failure guard would be dead code
        exactly where it matters. This drives ensure_running, which probes first.
        """
        proxy = _mod("proxy")

        holder = proxy.KiroProxy(port=0).start(publish=False)
        try:
            taken = holder.port
            assert proxy._port_is_serving(taken) is True

            # Force ensure_running down the fixed-port path with no adoptable state.
            monkeypatch.setattr(proxy, "_singleton", None)
            monkeypatch.setattr(proxy, "read_state", lambda: None)
            monkeypatch.setenv("KIRO_PROXY_PORT", str(taken))

            with pytest.raises(RuntimeError) as exc:
                proxy.ensure_running()
            message = str(exc.value)
            assert str(taken) in message
            # Must tell the user how to run two instances deliberately.
            assert "KIRO_PROXY_PORT" in message
            assert "KIRO_BASE_URL" in message
        finally:
            holder.stop()
            proxy._singleton = None

    def test_free_port_is_not_reported_as_serving(self):
        proxy = _mod("proxy")
        server = proxy.KiroProxy(port=0).start(publish=False)
        port = server.port
        server.stop()
        # Now free again.
        assert proxy._port_is_serving(port) is False

    def test_ephemeral_port_zero_still_allowed_to_fail_naturally(self):
        """port=0 keeps the OS-assigns-a-port behaviour; only fixed ports are strict."""
        proxy = _mod("proxy")
        server = proxy.KiroProxy(port=0).start(publish=False)
        try:
            assert server.port > 0
        finally:
            server.stop()


class TestTokenFileEncoding:
    def test_bom_prefixed_token_file_is_readable(self, tmp_path):
        """Kiro owns this file; we do not control its encoding.

        A strict utf-8 read fails on a BOM with "Unexpected UTF-8 BOM", which
        presents to the user as "not signed in" and sends them to re-authenticate
        for no reason. utf-8-sig accepts both.
        """
        auth = _mod("auth")
        path = tmp_path / "kiro-auth-token.json"
        payload = json.dumps({"accessToken": "abc", "expiresAt": "2099-01-01T00:00:00Z"})
        path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

        token = auth.read_token_file(path)
        assert token.access_token == "abc"

    def test_plain_utf8_token_file_still_readable(self, tmp_path):
        auth = _mod("auth")
        path = tmp_path / "kiro-auth-token.json"
        path.write_text(json.dumps({"accessToken": "xyz"}), encoding="utf-8")
        assert auth.read_token_file(path).access_token == "xyz"


class TestAccountsCard:
    """The Accounts-tab status card -- this is the 'scan' affordance."""

    def test_card_is_in_the_oauth_catalog(self):
        from hermes_cli.web_server import _build_oauth_catalog

        ids = [row["id"] for row in _build_oauth_catalog()]
        assert "kiro-ide" in ids
        # The key provider belongs on the keys tab, not here.
        assert "kiro" not in ids

    def test_status_never_raises_and_hides_the_token(self):
        from hermes_cli.web_server import _kiro_ide_status

        status = _kiro_ide_status()
        assert set(status) >= {
            "logged_in",
            "source",
            "source_label",
            "token_preview",
            "expires_at",
            "has_refresh_token",
        }
        # A live cloud credential must never reach a settings page.
        assert status["token_preview"] is None

    def test_status_distinguishes_not_installed_from_not_signed_in(self, monkeypatch):
        """Three outcomes need three different user actions, so they must differ."""
        import hermes_cli.web_server as ws

        auth = _mod("auth")

        monkeypatch.setattr(auth, "auth_status", lambda: {"installs": [], "signed_in": False})
        assert "No Kiro" in ws._kiro_ide_status()["source_label"]

        monkeypatch.setattr(
            auth,
            "auth_status",
            lambda: {"installs": [{"label": "Kiro IDE 9.9"}], "signed_in": False},
        )
        label = ws._kiro_ide_status()["source_label"]
        assert "not signed in" in label and "Kiro IDE 9.9" in label

        monkeypatch.setattr(
            auth,
            "auth_status",
            lambda: {"installs": [{"label": "Kiro IDE 9.9"}], "signed_in": True},
        )
        signed_in = ws._kiro_ide_status()
        assert signed_in["logged_in"] is True
        assert "signed in" in signed_in["source_label"]

    def test_status_survives_a_broken_plugin(self, monkeypatch):
        import hermes_cli.web_server as ws

        auth = _mod("auth")
        monkeypatch.setattr(
            auth, "auth_status", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        status = ws._kiro_ide_status()
        assert status["logged_in"] is False
        assert "unavailable" in status["source_label"]


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


class TestVisionRouting:
    """Images must actually reach the model.

    Hermes resolves vision capability from config, then models.dev. Kiro's model
    ids are not in models.dev, so the answer was "unknown", image_mode fell back
    to "text", and the image never reached the model -- Opus 5 replied that it
    could not see one. The profile now answers per model.
    """

    def test_profile_answers_per_model(self, profile):
        # Claude ids accept images; every gpt id is rejected by Q with
        # REQUEST_BODY_INVALID, so a single profile-wide flag cannot serve both.
        assert profile.supports_vision_for_model("claude-opus-5") is True
        assert profile.supports_vision_for_model("claude-sonnet-4.5") is True
        assert profile.supports_vision_for_model("gpt-5.6-sol") is False

    def test_unknown_model_gets_the_conservative_default(self, profile):
        # catalog.info_for defaults unknown ids to vision-capable; assert whatever
        # the catalog says rather than duplicating the policy here.
        catalog = _mod("catalog")
        assert profile.supports_vision_for_model("brand-new-model") == catalog.supports_vision(
            "brand-new-model"
        )

    def test_end_to_end_routing_decision(self):
        """The whole point: the routing layer must now say "native" for Claude."""
        _clear_provider_caches()
        from agent.image_routing import decide_image_input_mode

        assert decide_image_input_mode("kiro", "claude-opus-5", {}) == "native"
        assert decide_image_input_mode("kiro", "gpt-5.6-sol", {}) == "text"

    def test_kiro_ide_inherits_the_hook(self):
        _clear_provider_caches()
        from agent.image_routing import decide_image_input_mode

        assert decide_image_input_mode("kiro-ide", "claude-opus-5", {}) == "native"

    def test_request_body_matches_the_routing_decision(self):
        """Guard against the two halves disagreeing.

        wire.build_request_body drops images for gpt ids independently of the
        routing layer. If those two ever disagree, either images are silently lost
        on Claude or a gpt request 400s.
        """
        wire = _mod("wire")
        catalog = _mod("catalog")
        content = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}},
        ]
        for model in ("claude-opus-5", "gpt-5.6-sol"):
            body = wire.build_request_body([{"role": "user", "content": content}], model)
            attached = "images" in body["conversationState"]["currentMessage"]["userInputMessage"]
            assert attached == catalog.supports_vision(model), model


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

    def test_auto_is_hidden(self):
        """`auto` is Kiro's router and will not say what it routed to.

        `modelId` in the response echoes back "auto" (verified live), so cost and
        quality cannot be attributed. This fork does its own routing instead.
        """
        catalog = _mod("catalog")
        assert "auto" in catalog.HIDDEN_MODEL_IDS
        assert "auto" not in catalog.static_model_ids()

    def test_hidden_ids_are_filtered_from_a_live_list(self):
        """Omitting it from MODELS is NOT enough — ListAvailableModels returns it,
        so a live catalog fetch would put it straight back in the picker."""
        catalog = _mod("catalog")
        assert catalog.visible_model_ids(["claude-opus-5", "auto", "glm-5"]) == [
            "claude-opus-5",
            "glm-5",
        ]

    def test_visible_model_ids_trims_and_dedupes(self):
        catalog = _mod("catalog")
        assert catalog.visible_model_ids([" claude-opus-5 ", "claude-opus-5", "", "auto"]) == [
            "claude-opus-5"
        ]

    def test_live_list_filters_hidden_ids(self, monkeypatch):
        """The filter lives in client.list_models, the single funnel feeding both
        providers' fetch_models and the proxy's /v1/models route."""
        import json as _json
        import io

        client = _mod("client")
        auth = _mod("auth")

        payload = _json.dumps({"models": [{"modelId": "auto"}, {"modelId": "claude-opus-5"}]})

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            client.urllib.request, "urlopen", lambda *a, **k: _Resp(payload.encode())
        )
        credential = auth.ResolvedCredential(token="ksk_x", source="explicit")

        assert client.list_models(credential, region="us-east-1") == ["claude-opus-5"]

    def test_an_all_hidden_response_reads_as_failure_not_empty(self, monkeypatch):
        """None means "use the static catalog"; an empty list would show an empty
        picker."""
        import json as _json
        import io

        client = _mod("client")
        auth = _mod("auth")

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            client.urllib.request,
            "urlopen",
            lambda *a, **k: _Resp(_json.dumps({"models": ["auto"]}).encode()),
        )
        credential = auth.ResolvedCredential(token="ksk_x", source="explicit")

        assert client.list_models(credential, region="us-east-1") is None


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
