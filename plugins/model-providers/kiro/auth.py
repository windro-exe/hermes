"""Kiro credential resolution and install detection.

Fork-owned. Ported from the working provider in windro's Syncode fork
(``packages/opencode/src/provider/kiro/index.ts``), which is the only reference
for these endpoints and payload shapes -- none of it is publicly documented.

Two credential sources, resolved in precedence order by :func:`resolve_token`:

1. An explicit programmatic API key (``ksk_...`` from app.kiro.dev), pasted by
   the user. Stored by Hermes like any other provider key.
2. The AWS SSO token that an installed Kiro IDE or CLI writes to
   ``~/.aws/sso/cache/kiro-auth-token.json``, refreshed in place when it is
   close to expiry.

Both end up as a plain bearer token against the same endpoint, so there is no
"proxy mode" versus "key mode" split below the surface -- the only wire-level
difference is that ``ksk_`` keys also need a ``tokentype: API_KEY`` header.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- constants ported from the reference implementation ---------------------

#: Where kiro-cli / the Kiro IDE park the SSO token.
TOKEN_FILENAME = "kiro-auth-token.json"

#: Treat a token as expired this many seconds early, so an in-flight request
#: cannot straddle the expiry boundary.
EXPIRY_BUFFER_SECONDS = 300

#: Regions probed for the Q endpoint, in order. First one that answers wins.
CANDIDATE_REGIONS = ("us-east-1", "eu-central-1")
DEFAULT_REGION = "us-east-1"

#: Region strings are interpolated into URLs, so they are validated first.
_VALID_REGION = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")

#: ``clientIdHash`` is interpolated into a filename -- path-traversal guard.
_VALID_HASH = re.compile(r"^[a-zA-Z0-9_-]+$")

#: Programmatic API keys carry this prefix and need an extra header.
API_KEY_PREFIX = "ksk_"

_HTTP_TIMEOUT = 20.0


class KiroAuthError(Exception):
    """No usable Kiro credential, or a refresh was rejected.

    ``relogin_required`` distinguishes "your token is dead, sign in again" from
    transient failures, so callers can avoid telling the user to re-auth over a
    network blip.
    """

    def __init__(self, message: str, *, code: str = "kiro_auth_error", relogin_required: bool = False):
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


# --- paths -----------------------------------------------------------------


def sso_cache_dir() -> Path:
    """Directory Kiro shares with the AWS SSO cache."""
    return Path.home() / ".aws" / "sso" / "cache"


def token_path() -> Path:
    """Full path to the Kiro SSO token file."""
    return sso_cache_dir() / TOKEN_FILENAME


def _validate_region(region: str) -> str:
    region = (region or "").strip()
    if not _VALID_REGION.match(region):
        raise KiroAuthError(
            f"Refusing to build a URL from a malformed region: {region!r}",
            code="kiro_bad_region",
        )
    return region


# --- install detection -----------------------------------------------------


@dataclass
class KiroInstall:
    """A Kiro IDE or CLI found on this machine."""

    path: Path
    kind: str  # "ide" | "cli"
    version: str = ""

    def describe(self) -> str:
        label = "Kiro IDE" if self.kind == "ide" else "Kiro CLI"
        return f"{label} {self.version}".strip() + f" ({self.path})"


def _ide_candidates() -> list[Path]:
    """Platform-specific install locations, most likely first."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return [
            Path(local) / "Programs" / "Kiro" / "Kiro.exe",
            Path(local) / "Programs" / "kiro" / "Kiro.exe",
            Path(program_files) / "Kiro" / "Kiro.exe",
        ]
    if sys.platform == "darwin":
        return [
            Path("/Applications/Kiro.app/Contents/MacOS/Electron"),
            home / "Applications" / "Kiro.app" / "Contents" / "MacOS" / "Electron",
        ]
    return [
        Path("/usr/share/kiro/kiro"),
        Path("/opt/kiro/kiro"),
        home / ".local" / "share" / "kiro" / "kiro",
    ]


def _read_ide_version(exe: Path) -> str:
    """Pull the version out of the Electron app manifest beside the binary."""
    # Windows/Linux: <root>/resources/app/package.json. macOS nests under Contents.
    roots = [exe.parent, exe.parent.parent, exe.parent.parent.parent]
    for root in roots:
        manifest = root / "resources" / "app" / "package.json"
        if manifest.is_file():
            try:
                return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "") or "")
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("kiro: unreadable app manifest at %s: %s", manifest, exc)
                return ""
    return ""


def detect_installs() -> list[KiroInstall]:
    """Find every Kiro IDE/CLI on this machine.

    Deliberately does not run the binary -- launching an IDE to ask its version
    is both slow and rude. Version comes from the on-disk manifest.
    """
    found: list[KiroInstall] = []
    seen: set[str] = set()

    for exe in _ide_candidates():
        if exe.is_file():
            key = str(exe).lower()
            if key not in seen:
                seen.add(key)
                found.append(KiroInstall(path=exe, kind="ide", version=_read_ide_version(exe)))

    # The IDE ships a CLI shim in bin/; also honour a PATH install.
    for install in list(found):
        root = install.path.parent
        for shim in ("kiro.cmd", "kiro"):
            candidate = root / "bin" / shim
            if candidate.is_file():
                key = str(candidate).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(KiroInstall(path=candidate, kind="cli", version=install.version))
                break

    on_path = shutil.which("kiro")
    if on_path:
        key = str(Path(on_path)).lower()
        if key not in seen:
            seen.add(key)
            found.append(KiroInstall(path=Path(on_path), kind="cli"))

    return found


# --- token file ------------------------------------------------------------


@dataclass
class StoredToken:
    """The on-disk SSO token, plus whatever else the writer put there.

    ``extra`` preserves unknown keys so a refresh round-trip never destroys
    fields written by a newer Kiro than the one this code was written against.
    """

    access_token: str
    refresh_token: str = ""
    expires_at: str = ""
    region: str = ""
    client_id: str = ""
    client_secret: str = ""
    client_id_hash: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "accessToken",
        "refreshToken",
        "expiresAt",
        "region",
        "clientId",
        "clientSecret",
        "clientIdHash",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredToken":
        return cls(
            access_token=str(data.get("accessToken", "") or ""),
            refresh_token=str(data.get("refreshToken", "") or ""),
            expires_at=str(data.get("expiresAt", "") or ""),
            region=str(data.get("region", "") or ""),
            client_id=str(data.get("clientId", "") or ""),
            client_secret=str(data.get("clientSecret", "") or ""),
            client_id_hash=str(data.get("clientIdHash", "") or ""),
            extra={k: v for k, v in data.items() if k not in cls._KNOWN},
        )

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.extra)
        out["accessToken"] = self.access_token
        if self.refresh_token:
            out["refreshToken"] = self.refresh_token
        if self.expires_at:
            out["expiresAt"] = self.expires_at
        if self.region:
            out["region"] = self.region
        if self.client_id:
            out["clientId"] = self.client_id
        if self.client_secret:
            out["clientSecret"] = self.client_secret
        if self.client_id_hash:
            out["clientIdHash"] = self.client_id_hash
        return out

    def expires_at_epoch(self) -> Optional[float]:
        """``expiresAt`` as a unix timestamp, or None if absent/unparseable."""
        raw = (self.expires_at or "").strip()
        if not raw:
            return None
        # ISO 8601, usually Z-suffixed. datetime.fromisoformat rejects "Z"
        # before 3.11, so normalise it.
        from datetime import datetime, timezone

        try:
            normalised = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
            parsed = datetime.fromisoformat(normalised)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            logger.debug("kiro: unparseable expiresAt %r", raw)
            return None

    def is_expiring(self, buffer_seconds: int = EXPIRY_BUFFER_SECONDS) -> bool:
        """True when the token is gone or within ``buffer_seconds`` of expiry.

        A token with no ``expiresAt`` is treated as expiring: better to attempt
        a refresh than to send a credential we cannot reason about.
        """
        expiry = self.expires_at_epoch()
        if expiry is None:
            return True
        return time.time() + max(0, buffer_seconds) >= expiry


def read_token_file(path: Optional[Path] = None) -> StoredToken:
    """Load the SSO token, raising :class:`KiroAuthError` if unusable."""
    target = path or token_path()
    if not target.is_file():
        raise KiroAuthError(
            "No Kiro sign-in found. Open the Kiro IDE and sign in, then try again.\n"
            f"Expected credentials at: {target}",
            code="kiro_token_missing",
            relogin_required=True,
        )
    try:
        # utf-8-sig, not utf-8: it decodes both BOM-prefixed and plain UTF-8.
        # We do not control who writes this file -- the Kiro IDE owns it -- and a
        # strict utf-8 read fails outright on a BOM with "Unexpected UTF-8 BOM",
        # which reads like "not signed in" and sends the user to re-authenticate
        # for no reason. Being permissive on read costs nothing.
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise KiroAuthError(
            f"Kiro credential file is unreadable ({exc}). Sign in again in the Kiro IDE.\n"
            f"File: {target}",
            code="kiro_token_unreadable",
            relogin_required=True,
        ) from exc
    if not isinstance(data, dict):
        raise KiroAuthError(
            f"Kiro credential file is not a JSON object: {target}",
            code="kiro_token_unreadable",
            relogin_required=True,
        )
    token = StoredToken.from_dict(data)
    if not token.access_token:
        raise KiroAuthError(
            f"Kiro credential file has no accessToken: {target}",
            code="kiro_token_incomplete",
            relogin_required=True,
        )
    return token


def write_token_file(token: StoredToken, path: Optional[Path] = None) -> None:
    """Persist a refreshed token back to Kiro's own file, 0600.

    Writing to the shared file on purpose: a refresh done here also keeps the
    IDE working, which is the same contract the Qwen CLI integration uses. A
    failure here is logged, not raised -- we still hold a valid access token in
    memory and the request should proceed.
    """
    target = path or token_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(token.to_dict(), indent=2)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
        except Exception:
            fd = -1
            raise
        os.replace(str(tmp), str(target))
    except Exception as exc:
        logger.warning("kiro: could not persist refreshed token to %s: %s", target, exc)


def _resolve_client(token: StoredToken) -> tuple[str, str]:
    """Get the OIDC client credentials needed to refresh.

    Newer writers store only a ``clientIdHash`` pointing at the sibling SSO
    client-registration file rather than inlining the client id.
    """
    if token.client_id:
        return token.client_id, token.client_secret
    if not token.client_id_hash:
        raise KiroAuthError(
            "Kiro credential file has neither clientId nor clientIdHash, so it cannot be "
            "refreshed. Sign in again in the Kiro IDE.",
            code="kiro_token_incomplete",
            relogin_required=True,
        )
    if not _VALID_HASH.match(token.client_id_hash):
        raise KiroAuthError(
            "Kiro credential file has a malformed clientIdHash; refusing to read it.",
            code="kiro_token_unreadable",
        )
    ref = sso_cache_dir() / f"{token.client_id_hash}.json"
    try:
        # utf-8-sig for the same reason as the token file: another tool wrote it.
        data = json.loads(ref.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise KiroAuthError(
            f"Could not read the SSO client registration at {ref} ({exc}).",
            code="kiro_client_unreadable",
            relogin_required=True,
        ) from exc
    client_id = str(data.get("clientId", "") or "")
    if not client_id:
        raise KiroAuthError(
            f"SSO client registration at {ref} has no clientId.",
            code="kiro_client_unreadable",
            relogin_required=True,
        )
    return client_id, str(data.get("clientSecret", "") or "")


def refresh_token(token: StoredToken, *, persist: bool = True, path: Optional[Path] = None) -> StoredToken:
    """Exchange the refresh token for a new access token.

    Note the body is AWS SSO-OIDC ``CreateToken`` REST-JSON -- **camelCase**
    keys, JSON encoded. It is not the form-encoded OAuth 2.0 shape, and sending
    ``grant_type``/``refresh_token`` instead will be rejected.
    """
    if not token.refresh_token:
        raise KiroAuthError(
            "Kiro token has expired and carries no refreshToken. Sign in again in the Kiro IDE.",
            code="kiro_refresh_missing",
            relogin_required=True,
        )
    region = _validate_region(token.region or DEFAULT_REGION)
    client_id, client_secret = _resolve_client(token)

    body = json.dumps(
        {
            "grantType": "refresh_token",
            "clientId": client_id,
            "clientSecret": client_secret,
            "refreshToken": token.refresh_token,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"https://oidc.{region}.amazonaws.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        opener = _credentialed_opener()
        with opener(request, timeout=_HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # pragma: no cover - defensive
            pass
        # A rejected refresh token is terminal; anything else may be transient,
        # so don't send the user to a re-login screen over a 5xx.
        relogin = exc.code in (400, 401, 403)
        raise KiroAuthError(
            f"Kiro token refresh failed (HTTP {exc.code}). {detail}".strip(),
            code="kiro_refresh_rejected" if relogin else "kiro_refresh_failed",
            relogin_required=relogin,
        ) from exc
    except Exception as exc:
        raise KiroAuthError(
            f"Kiro token refresh could not reach the SSO endpoint: {exc}",
            code="kiro_refresh_failed",
        ) from exc

    access = str(payload.get("accessToken", "") or "")
    if not access:
        raise KiroAuthError(
            "Kiro token refresh returned no accessToken.",
            code="kiro_refresh_rejected",
            relogin_required=True,
        )

    expires_in = payload.get("expiresIn")
    from datetime import datetime, timezone

    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = datetime.fromtimestamp(time.time() + float(expires_in), tz=timezone.utc)
        expires_iso = expires_at.isoformat().replace("+00:00", "Z")
    else:
        expires_iso = token.expires_at

    refreshed = StoredToken(
        access_token=access,
        # The response omits refreshToken when the old one is still valid.
        refresh_token=str(payload.get("refreshToken", "") or "") or token.refresh_token,
        expires_at=expires_iso,
        region=token.region,
        client_id=token.client_id,
        client_secret=token.client_secret,
        client_id_hash=token.client_id_hash,
        extra=dict(token.extra),
    )
    if persist:
        write_token_file(refreshed, path)
    return refreshed


# --- resolution ------------------------------------------------------------


@dataclass
class ResolvedCredential:
    """A bearer token ready to put on the wire."""

    token: str
    source: str  # "explicit" | "env" | "kiro-ide"
    region: str = ""
    expires_at_epoch: Optional[float] = None

    @property
    def is_api_key(self) -> bool:
        """``ksk_`` keys need the extra ``tokentype: API_KEY`` header."""
        return self.token.startswith(API_KEY_PREFIX)


def resolve_token(
    explicit_key: str = "",
    *,
    allow_refresh: bool = True,
    token_file: Optional[Path] = None,
) -> ResolvedCredential:
    """Resolve a Kiro bearer token.

    Precedence: explicit key, then ``KIRO_API_KEY``, then the SSO token file.
    Set ``allow_refresh=False`` for cheap, offline status checks -- credential
    discovery should never block on the network.
    """
    explicit_key = (explicit_key or "").strip()
    if explicit_key:
        return ResolvedCredential(token=explicit_key, source="explicit")

    env_key = (os.environ.get("KIRO_API_KEY") or "").strip()
    if env_key:
        return ResolvedCredential(token=env_key, source="env")

    token = read_token_file(token_file)
    if allow_refresh and token.is_expiring():
        token = refresh_token(token, path=token_file)
    return ResolvedCredential(
        token=token.access_token,
        source="kiro-ide",
        region=token.region,
        expires_at_epoch=token.expires_at_epoch(),
    )


def auth_status() -> dict[str, Any]:
    """Everything the setup menu needs, without touching the network.

    Never raises and never returns the token itself -- only whether one exists.
    """
    installs = detect_installs()
    status: dict[str, Any] = {
        "installs": [
            {"path": str(i.path), "kind": i.kind, "version": i.version, "label": i.describe()}
            for i in installs
        ],
        "installed": bool(installs),
        "token_file": str(token_path()),
        "signed_in": False,
        "expires_at_epoch": None,
        "region": "",
        "env_key_present": bool((os.environ.get("KIRO_API_KEY") or "").strip()),
        "error": "",
    }
    try:
        token = read_token_file()
    except KiroAuthError as exc:
        status["error"] = str(exc)
        return status
    status["signed_in"] = True
    status["expires_at_epoch"] = token.expires_at_epoch()
    status["region"] = token.region
    status["expiring"] = token.is_expiring()
    return status


def _credentialed_opener():
    """Hermes' hardened URL opener, falling back to urlopen when unavailable.

    Import is deferred and guarded so this module stays importable from tests
    that do not have the full CLI package on the path.
    """
    try:
        from hermes_cli.urllib_security import open_credentialed_url

        return open_credentialed_url
    except Exception:  # pragma: no cover - exercised only outside the app
        return urllib.request.urlopen
