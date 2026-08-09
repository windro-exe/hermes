"""Guard tests: nothing in this fork may fetch code or data from upstream.

Fork-owned. This bug class has recurred twice and both times it failed silently:

* ``2026-08-08-02`` -- three hardcoded slugs in the install scripts and the
  desktop bootstrap pointed installs at a stale repo.
* ``2026-08-09-02`` -- ``hermes update``'s ZIP fallback downloaded *upstream's*
  source archive and extracted it over the fork, replacing the entire working
  tree while ``.git`` still claimed a fork commit.

Neither raised an error. These tests exist so a third recurrence fails loudly in
CI instead of quietly on a user's machine.

Note the deliberate exclusions, so nobody "fixes" them into these assertions:
the Nous Portal inference API, the ``com.nousresearch.hermes`` app identity,
upstream docs URLs, and issue references in comments. See the Follow-ups section
of ``fork/changelog/entries/2026-08-09-02-disconnect-upstream.md``.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

import hermes_fork


class TestForkConstants:
    def test_slug_is_coherent(self):
        assert hermes_fork.FORK_SLUG == f"{hermes_fork.FORK_OWNER}/{hermes_fork.FORK_NAME}"
        assert hermes_fork.FORK_CANONICAL == f"github.com/{hermes_fork.FORK_SLUG}".lower()

    def test_urls_all_name_the_fork(self):
        for url in (
            hermes_fork.FORK_HTTPS_URL,
            hermes_fork.FORK_SSH_URL,
            hermes_fork.FORK_WEB_URL,
            hermes_fork.FORK_RELEASES_URL,
            hermes_fork.FORK_RELEASE_TAG_URL_BASE,
        ):
            assert hermes_fork.FORK_SLUG in url, url
            assert "nousresearch" not in url.lower(), url

    def test_archive_url_shapes(self):
        assert hermes_fork.fork_archive_url("main").endswith("/archive/refs/heads/main.zip")
        assert hermes_fork.fork_archive_url("v1", kind="tags").endswith("/archive/refs/tags/v1.zip")
        assert hermes_fork.fork_archive_url("abc123", kind="commit").endswith("/archive/abc123.zip")
        assert hermes_fork.FORK_SLUG in hermes_fork.fork_archive_url("main")

    def test_archive_url_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            hermes_fork.fork_archive_url("main", kind="branches")

    def test_raw_url_points_at_the_fork(self):
        url = hermes_fork.fork_raw_url("website/static/api/model-catalog.json")
        assert url.startswith(f"https://raw.githubusercontent.com/{hermes_fork.FORK_SLUG}/")
        assert "nousresearch" not in url.lower()

    def test_upstream_recognition_still_works(self):
        """Upstream constants are retained to RECOGNISE, never to fetch."""
        assert hermes_fork.is_upstream_url("https://github.com/NousResearch/hermes-agent.git")
        assert hermes_fork.is_upstream_url("git@github.com:NousResearch/hermes-agent")
        assert not hermes_fork.is_upstream_url(hermes_fork.FORK_HTTPS_URL)
        assert not hermes_fork.is_upstream_url(None)
        assert hermes_fork.is_fork_url(hermes_fork.FORK_SSH_URL)
        assert not hermes_fork.is_fork_url("https://github.com/NousResearch/hermes-agent")


class TestNoUpstreamFetchUrls:
    """Every URL that is *fetched* must name the fork."""

    def test_model_catalog_url(self):
        from hermes_cli import model_catalog

        assert hermes_fork.FORK_SLUG in model_catalog.DEFAULT_CATALOG_URL
        assert "nousresearch" not in model_catalog.DEFAULT_CATALOG_URL.lower()

    def test_model_catalog_fallback_chain_has_no_upstream(self):
        from hermes_cli import model_catalog

        for url in model_catalog.DEFAULT_CATALOG_FALLBACK_URLS:
            assert "nousresearch" not in url.lower(), url

    def test_config_default_catalog_url(self):
        from hermes_cli.config import DEFAULT_CONFIG

        url = DEFAULT_CONFIG["model_catalog"]["url"]
        assert hermes_fork.FORK_SLUG in url
        assert "nousresearch" not in url.lower()

    def test_banner_ls_remote_target(self):
        """`_check_via_rev` ls-remotes this URL and compares to the LOCAL rev.

        Pointed upstream, a fork's revision can never match: it contacted
        NousResearch every check and reported "update available" forever.
        """
        from hermes_cli import banner

        assert banner._UPSTREAM_REPO_URL == hermes_fork.FORK_HTTPS_URL
        assert banner._OFFICIAL_REPO_CANONICAL == hermes_fork.FORK_CANONICAL
        assert hermes_fork.FORK_SLUG in banner._RELEASE_URL_BASE

    def test_skills_hub_index_url(self):
        import tools.skills_hub as hub

        assert hermes_fork.FORK_SLUG in hub.HERMES_INDEX_URL
        assert "nousresearch" not in hub.HERMES_INDEX_URL.lower()

    def test_skills_hub_official_repo_is_this_fork(self):
        import tools.skills_hub as hub

        assert hub.OptionalSkillSource.OFFICIAL_REPO == hermes_fork.FORK_SLUG


class TestZipFallbackCannotPullUpstream:
    """The 2026-08-09 corruption. See the changelog entry."""

    def test_source_uses_the_fork_helper(self):
        import hermes_cli.main as m

        src = inspect.getsource(m._update_via_zip)
        assert "fork_archive_url" in src
        # The exact string that overwrote a live install.
        assert "NousResearch/hermes-agent/archive" not in src

    def test_extract_dir_derives_from_repo_name(self):
        """GitHub archives root at ``<repo>-<ref>``.

        This was hardcoded ``hermes-agent-<branch>`` while the fork is ``hermes``,
        so the path never existed and it only worked via a guess loop.
        """
        import hermes_cli.main as m

        src = inspect.getsource(m._update_via_zip)
        assert 'f"{FORK_NAME}-{branch}"' in src
        # Comments legitimately mention the old name to explain the bug, so only
        # executable lines are checked.
        code_lines = [
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        ]
        assert not any("hermes-agent-" in line for line in code_lines)


class TestUpstreamSyncIsDisabled:
    """AGENTS.md: never sync upstream. The prompt defaulted to yes."""

    def test_is_a_no_op_making_no_git_calls_and_no_prompt(self, monkeypatch, tmp_path):
        import builtins

        import hermes_cli.main as m

        calls: list[list[str]] = []

        def spy_run(cmd, **kwargs):
            calls.append(cmd)
            raise AssertionError(f"upstream sync ran a git command: {cmd!r}")

        def spy_input(*args, **kwargs):
            raise AssertionError("upstream sync prompted the user")

        monkeypatch.setattr(m.subprocess, "run", spy_run)
        monkeypatch.setattr(builtins, "input", spy_input)

        assert m._sync_with_upstream_if_needed(["git"], Path(tmp_path)) is None
        assert calls == []

    def test_fork_detection_is_retained_for_the_banner(self):
        """`_is_fork` still drives the informational "Updating from fork" line."""
        import hermes_cli.main as m

        assert m._is_fork(hermes_fork.FORK_HTTPS_URL) is True
        assert m._is_fork("https://github.com/NousResearch/hermes-agent.git") is False


class TestPackaging:
    """`hermes_fork` is a root-level single-file module, so it must be declared in
    ``[tool.setuptools] py-modules`` or it is silently dropped from any
    non-editable install.

    This was shipped broken: `hermes_cli/main.py` imports `hermes_fork` at module
    level, so omitting it breaks the entire CLI in a wheel, the Docker image, or a
    uv2nix sealed venv -- while looking perfectly fine in the editable install
    used for development. pyproject's own comment on that list says as much.
    """

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _declared_py_modules(self) -> set[str]:
        import re

        text = (self._repo_root() / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r"^py-modules\s*=\s*\[(.*?)\]", text, re.M | re.S)
        assert match, "py-modules list not found in pyproject.toml"
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_hermes_fork_is_declared(self):
        assert "hermes_fork" in self._declared_py_modules()

    def test_every_imported_root_module_is_declared(self):
        """General form of the same bug, for whatever gets added next."""
        import re
        import subprocess

        root = self._repo_root()
        tracked = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True
        ).stdout.split()
        if not tracked:
            pytest.skip("git not available or not a checkout")

        root_modules = {Path(p).stem for p in tracked if "/" not in p}
        pattern = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.M)
        imported: set[str] = set()
        for rel in tracked:
            if "/" not in rel or rel.startswith("tests/"):
                continue
            try:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            imported.update(name for name in pattern.findall(text) if name in root_modules)

        undeclared = sorted(imported - self._declared_py_modules())
        assert not undeclared, (
            "root-level modules imported by source but missing from "
            f"pyproject py-modules (they vanish in non-editable installs): {undeclared}"
        )

    def test_declared_modules_all_exist(self):
        """Catches the reverse: a declared module that was deleted or renamed."""
        root = self._repo_root()
        missing = sorted(
            name for name in self._declared_py_modules() if not (root / f"{name}.py").is_file()
        )
        assert not missing, f"py-modules names files that do not exist: {missing}"


class TestDuplicatedSlugsStayInStep:
    """TypeScript and Rust cannot import hermes_fork.py, so three files carry
    the slug by hand. These assertions are the only thing keeping them honest."""

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]

    @pytest.mark.parametrize(
        "relpath",
        [
            "apps/desktop/electron/update-remote.ts",
            "apps/desktop/src/app/settings/about-settings.tsx",
            "apps/bootstrap-installer/src-tauri/src/install_script.rs",
        ],
    )
    def test_file_names_the_fork(self, relpath):
        path = self._repo_root() / relpath
        if not path.is_file():
            pytest.skip(f"{relpath} not present in this checkout")
        text = path.read_text(encoding="utf-8", errors="replace")
        assert hermes_fork.FORK_SLUG in text, f"{relpath} lost the fork slug"

    def test_tauri_installer_does_not_fetch_upstream_raw(self):
        """raw.githubusercontent does NOT follow renames: a mutable ref pointed
        upstream returns UPSTREAM's installer, which then clones upstream."""
        path = self._repo_root() / "apps/bootstrap-installer/src-tauri/src/install_script.rs"
        if not path.is_file():
            pytest.skip("bootstrap installer not present")
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "raw.githubusercontent.com/NousResearch" not in text
