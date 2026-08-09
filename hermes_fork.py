"""Single source of truth for which repository this fork lives in.

Fork-owned. Created because the same wrong answer kept being hardcoded in
different files, with real consequences.

Two incidents motivated centralising this:

1. The install scripts and the desktop's first-launch bootstrap each carried
   their own copy of the repo slug. When the fork moved accounts they were
   updated one by one, and the ones that were missed did not fail loudly -- they
   quietly pointed installs at a stale repo. See
   ``fork/changelog/entries/2026-08-08-02-installer-fork-slug.md``.
2. Worse, ``hermes update`` had a Windows ZIP fallback that downloaded
   *upstream's* source archive whenever git file I/O was blocked, then extracted
   it over the fork. That silently replaced the entire working tree with
   NousResearch's code while ``.git`` still claimed to be on a fork commit. See
   ``fork/changelog/entries/2026-08-09-02-disconnect-upstream.md``.

Both bugs share one cause: the repo identity was duplicated instead of imported.
Anything that needs to know where this fork lives should import from here rather
than write the slug out again.

Upstream constants are kept below, but deliberately only for *recognising*
upstream (e.g. detecting that an install is pointed at the wrong place). Nothing
in this fork should fetch code, archives, or catalogs from them.
"""

from __future__ import annotations

# --- this fork -------------------------------------------------------------

#: GitHub account that owns the fork.
FORK_OWNER = "windro-exe"

#: Repository name within that account.
FORK_NAME = "hermes"

#: ``owner/name``, the form GitHub URLs and the API want.
FORK_SLUG = f"{FORK_OWNER}/{FORK_NAME}"

#: Default branch. Update here if the fork ever renames it.
FORK_DEFAULT_BRANCH = "main"

FORK_HTTPS_URL = f"https://github.com/{FORK_SLUG}.git"
FORK_SSH_URL = f"git@github.com:{FORK_SLUG}.git"
FORK_WEB_URL = f"https://github.com/{FORK_SLUG}"

#: Lowercased ``github.com/owner/name`` used for remote-identity comparisons.
FORK_CANONICAL = f"github.com/{FORK_SLUG}".lower()

#: Releases page, for "what changed" links.
FORK_RELEASES_URL = f"{FORK_WEB_URL}/releases"
FORK_RELEASE_TAG_URL_BASE = f"{FORK_WEB_URL}/releases/tag"

#: Whether this fork publishes the aggregated Skills Hub index.
#:
#: Upstream rebuilds ``website/static/api/skills-index.json`` in CI daily and
#: serves it from its docs site; it is deliberately not committed. Generating it
#: here produces ~32 MB (79k skills aggregated from clawhub, skills.sh, lobehub
#: and others), which is too much to commit: ``install.ps1`` shallow-clones the
#: repo, so every install would download it, and it would be stale on arrival.
#:
#: While this is False, ``HermesIndexSource`` is inert -- it is one of eleven
#: skill sources and the other ten query their own upstreams directly, so the
#: Skills Hub still works. To enable: run ``scripts/build_skills_index.py``,
#: publish the output at :func:`fork_raw_url`'s path (committed, or a release
#: asset the URL points at), and flip this to True.
FORK_PUBLISHES_SKILLS_INDEX = False


def fork_archive_url(ref: str = FORK_DEFAULT_BRANCH, *, kind: str = "heads") -> str:
    """URL of a source archive for this fork.

    ``kind`` is ``heads`` for a branch, ``tags`` for a tag, or ``commit`` for a
    raw sha. This exists so no caller writes an archive URL by hand -- the
    hand-written one in ``hermes update``'s ZIP fallback pointed at upstream and
    overwrote the fork with it.
    """
    if kind == "commit":
        return f"{FORK_WEB_URL}/archive/{ref}.zip"
    if kind not in ("heads", "tags"):
        raise ValueError(f"kind must be heads, tags or commit; got {kind!r}")
    return f"{FORK_WEB_URL}/archive/refs/{kind}/{ref}.zip"


def fork_raw_url(path: str, ref: str = FORK_DEFAULT_BRANCH) -> str:
    """``raw.githubusercontent.com`` URL for a file in this fork.

    Note raw does NOT follow repository renames or transfers, so a stale slug
    here produces a 404 rather than a redirect. That is how the desktop's
    first-launch bootstrap broke; keeping the slug in one place is the fix.
    """
    return f"https://raw.githubusercontent.com/{FORK_SLUG}/{ref}/{path.lstrip('/')}"


# --- upstream, for recognition only ---------------------------------------

#: Upstream project. Present so code can *identify* an install that is pointed
#: at upstream and warn about it. Do not fetch from these.
UPSTREAM_SLUG = "NousResearch/hermes-agent"
UPSTREAM_CANONICAL = f"github.com/{UPSTREAM_SLUG}".lower()
UPSTREAM_URLS = frozenset(
    {
        f"https://github.com/{UPSTREAM_SLUG}.git",
        f"git@github.com:{UPSTREAM_SLUG}.git",
        f"https://github.com/{UPSTREAM_SLUG}",
        f"git@github.com:{UPSTREAM_SLUG}",
    }
)


def is_upstream_url(url: str | None) -> bool:
    """True when ``url`` names the upstream repository."""
    if not url:
        return False
    normalised = url.strip().rstrip("/")
    if normalised.endswith(".git"):
        normalised = normalised[:-4]
    return any(
        normalised.lower() == candidate.rstrip("/").removesuffix(".git").lower()
        for candidate in UPSTREAM_URLS
    )


def is_fork_url(url: str | None) -> bool:
    """True when ``url`` names this fork."""
    if not url:
        return False
    normalised = url.strip().rstrip("/")
    if normalised.endswith(".git"):
        normalised = normalised[:-4]
    return normalised.lower().endswith(FORK_SLUG.lower())
