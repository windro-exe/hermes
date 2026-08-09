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


kiro = KiroProfile(
    name="kiro",
    aliases=("kiro-ide", "amazon-kiro", "kiro-q"),
    api_mode="chat_completions",
    display_name="Kiro",
    description="Kiro (Amazon Q) via a local translator - API key or an installed Kiro IDE",
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

register_provider(kiro)
