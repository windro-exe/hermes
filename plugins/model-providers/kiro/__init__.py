"""Kiro provider for Hermes.

Kiro is not an officially supported Hermes provider, and Kiro itself exposes no
OpenAI-compatible API -- it speaks AWS Q ``GenerateAssistantResponse`` with
binary event-stream responses. Rather than add a sixth transport to Hermes core
for one provider, this plugin runs a loopback translator (``proxy.py``) and
presents an ordinary ``chat_completions`` provider to Hermes. Core is untouched.

Two ways to authenticate, both ending as a bearer token on the same endpoint:

1. **API key.** A ``ksk_`` programmatic key from app.kiro.dev, pasted at setup
   and stored like any other provider key.
2. **Installed Kiro.** Reuse the AWS SSO token an installed Kiro IDE or CLI
   already wrote to ``~/.aws/sso/cache/kiro-auth-token.json``, refreshing it in
   place when it nears expiry.

Layout of this package:

* ``auth.py``    credential resolution + install detection
* ``wire.py``    OpenAI <-> AWS Q translation, event-stream framing
* ``client.py``  the HTTPS client for the Q endpoint
* ``proxy.py``   loopback OpenAI-compatible server
* ``catalog.py`` model ids and context limits
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from providers import register_provider
from providers.base import ProviderProfile

from . import catalog

logger = logging.getLogger(__name__)

#: Must agree with ``proxy.DEFAULT_PORT``. Persisted into config.yaml as the
#: provider's base_url, so it is a fixed port rather than an ephemeral one.
DEFAULT_BASE_URL = "http://127.0.0.1:8779/v1"


class KiroProfile(ProviderProfile):
    """Kiro via the loopback translator.

    Declared ``auth_type="api_key"`` deliberately. It is accurate for the pasted
    key, and it buys automatic wiring into the provider registry, the model
    picker, ``OPTIONAL_ENV_VARS`` and the desktop keys tab. The installed-Kiro
    path is offered through an explicit dispatch branch in ``select_provider_and_model``,
    which is checked ahead of the api_key catch-all -- so both credential styles
    are reachable without declaring a bespoke auth type.
    """

    def build_extra_body(self, *, session_id: Optional[str] = None, **ctx: Any) -> dict[str, Any]:
        """Ensure the translator is up before Hermes tries to talk to it.

        This hook is called while building each request, which is the only point
        guaranteed to run before an inference call regardless of entry point (CLI,
        gateway, TUI, desktop). ``ensure_running`` is idempotent and cheap once
        started, and a failure here must never block the request -- the ensuing
        connection error is a clearer signal than an exception thrown from a
        body-building hook.
        """
        try:
            from . import proxy

            proxy.ensure_running()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kiro: could not start the local translator: %s", exc)
        return {}

    def fetch_models(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 8.0,
    ) -> Optional[list[str]]:
        """Ask Kiro which models this account may use.

        Goes straight to the Q endpoint rather than through the proxy: listing
        models needs no translation, and not starting a server just to populate a
        picker keeps ``hermes model`` fast. Returns ``None`` on any failure so the
        caller falls back to the static catalog instead of showing nothing.

        Measured 2026-08-09: the live call returns 19 ids, more than the static
        table, so live-first is the right default here.
        """
        try:
            from . import client
            from .auth import resolve_token

            credential = resolve_token(api_key or "", allow_refresh=True)
            return client.list_models(credential, timeout=timeout)
        except Exception as exc:
            logger.debug("kiro: fetch_models failed, falling back to the static catalog: %s", exc)
            return None

    def get_max_tokens(self, model: str) -> Optional[int]:
        return catalog.info_for(model).output

    def supports_vision_for_model(self, model: str) -> Optional[bool]:
        """Per-model vision capability.

        The profile-wide ``supports_vision`` flag is too coarse here: Q accepts
        images on the Claude ids and rejects them outright on every ``gpt-*`` id
        with REQUEST_BODY_INVALID, so a single flag would either lose images on
        Claude or 400 on GPT.

        ``agent.image_routing._lookup_supports_vision`` calls this when models.dev
        has no entry for the model, which is always true for Kiro's ids. Without
        it the lookup returns None and image_mode falls back to "text" -- the
        image never reaches the model and it reports it cannot see one.
        """
        return catalog.supports_vision(model)


class KiroIdeProfile(KiroProfile):
    """Kiro using the credentials an installed Kiro IDE or CLI already holds.

    Split out from ``kiro`` deliberately. ``auth_type`` is designed to describe
    exactly one credential source, and cramming both a pasted key and a detected
    install behind a single provider meant the desktop GUI -- which routes tabs
    purely on ``auth_type`` -- could only ever render a text box. As two
    providers each lands in its correct tab with no bespoke GUI work: ``kiro`` on
    API keys, ``kiro-ide`` on Accounts.

    Both point at the same loopback translator. No proxy change was needed for
    this: it already picks the credential from the bearer token it is handed --
    a ``ksk_`` prefix is used as-is, the session secret authorises reading the
    SSO token from disk.
    """

    def fetch_models(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 8.0,
    ) -> Optional[list[str]]:
        """Resolve from the installed Kiro's token, never from a pasted key.

        ``api_key`` here is the proxy session secret, not a Kiro credential, so
        it is deliberately ignored -- passing it upstream would send the wrong
        bearer to AWS.
        """
        try:
            from . import client
            from .auth import resolve_token

            return client.list_models(resolve_token(allow_refresh=True), timeout=timeout)
        except Exception as exc:
            logger.debug("kiro-ide: fetch_models failed (%s); using the static catalog", exc)
            return None


kiro = KiroProfile(
    name="kiro",
    aliases=("amazon-kiro", "kiro-q", "kiro-api"),
    api_mode="chat_completions",
    display_name="Kiro",
    description="Kiro (Amazon Q) with an API key from app.kiro.dev",
    signup_url="https://app.kiro.dev",
    env_vars=("KIRO_API_KEY", "KIRO_BASE_URL"),
    base_url=DEFAULT_BASE_URL,
    auth_type="api_key",
    # The translator is loopback-only and has no /models route worth probing from
    # `hermes doctor`; a failed probe there would report a healthy setup as broken.
    supports_health_check=False,
    supports_vision=True,
    fallback_models=tuple(catalog.static_model_ids()),
    default_aux_model="claude-haiku-4.5",
)

kiro_ide = KiroIdeProfile(
    name="kiro-ide",
    aliases=("kiro-desktop", "kiro-cli"),
    api_mode="chat_completions",
    display_name="Kiro IDE",
    description="Kiro (Amazon Q) reusing an installed Kiro IDE or CLI sign-in",
    signup_url="https://kiro.dev",
    # KIRO_IDE_TOKEN holds the translator's session secret, written by the setup
    # flow -- it is never typed by the user and is not a Kiro credential. The real
    # credential stays in ~/.aws/sso/cache/ where the IDE put it.
    env_vars=("KIRO_IDE_TOKEN", "KIRO_BASE_URL"),
    base_url=DEFAULT_BASE_URL,
    # Routes this provider to the desktop Accounts tab rather than API keys, and
    # keeps it out of the auto-injected OPTIONAL_ENV_VARS key fields.
    auth_type="external_process",
    supports_health_check=False,
    supports_vision=True,
    fallback_models=tuple(catalog.static_model_ids()),
    default_aux_model="claude-haiku-4.5",
)

register_provider(kiro)
register_provider(kiro_ide)
