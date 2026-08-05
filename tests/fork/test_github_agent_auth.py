"""Guard: the connected GitHub account is the agent's GitHub credential.

The GitHub integration first shipped renderer-only — the token lived in the
Electron main process for the project dialog, and nothing on the Python side
could see it. So when windro asked the agent to push, it fell through its own
resolution chain (tools/skills_hub.py: GITHUB_TOKEN -> `gh auth token` -> App
JWT -> unauthenticated) and went looking for the gh CLI, which is not installed.

These assert the wiring that closes that, and the properties that make it safe:
the token is passed through the ENVIRONMENT (never a git config file, never
.git/config), and it is scoped to github.com so other hosts are unaffected.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

MAIN = Path("apps/desktop/electron/main.ts")


@pytest.fixture(scope="module")
def main_src() -> str:
    return MAIN.read_text(encoding="utf-8")


class TestAgentGetsTheConnectedAccount:
    def test_gateway_spawn_injects_the_github_env(self, main_src):
        """Both spawn sites must get it, or auth works in one mode only."""
        assert main_src.count("...githubAgentEnv()") == 2, (
            "expected githubAgentEnv() at BOTH gateway spawn sites; the desktop "
            "spawns a backend in more than one place"
        )

    def test_it_sets_github_token_for_api_tools(self, main_src):
        """Rung 1 of the agent's existing chain, so no tool code has to change."""
        body = self._fn_body(main_src)

        assert "GITHUB_TOKEN: token" in body

    def test_it_sets_a_git_credential_helper(self, main_src):
        """Without this, plain `git push` uses the OS credential manager and
        ignores the connected account entirely."""
        body = self._fn_body(main_src)

        assert "GIT_CONFIG_KEY_0" in body
        assert "credential.https://github.com.helper" in body

    def test_the_helper_is_scoped_to_github(self, main_src):
        """A host-less `credential.helper` would answer for every remote."""
        body = self._fn_body(main_src)

        assert "credential.helper" not in body.replace(
            "credential.https://github.com.helper", ""
        ), "the credential helper must be scoped to https://github.com, not global"

    def test_it_returns_nothing_when_not_connected(self, main_src):
        """No account connected must leave behaviour exactly as before, not
        invent auth."""
        body = self._fn_body(main_src)

        assert "if (!token)" in body
        assert "return {}" in body

    def test_the_token_is_never_written_to_a_config_file(self, main_src):
        """The whole point of GIT_CONFIG_* over `git config` is that nothing
        persists: not global config, not .git/config, not `git remote -v`."""
        body = self._fn_body(main_src)

        for forbidden in ("git config", "writeFileSync", "credential.helper=store"):
            assert forbidden not in body, (
                f"githubAgentEnv must not persist anything ({forbidden!r} found)"
            )

    def test_env_is_spread_after_the_base_environment(self, main_src):
        """A stale inherited GITHUB_TOKEN must not beat the connected account."""
        for match in re.finditer(r"\.\.\.githubAgentEnv\(\)", main_src):
            before = main_src[: match.start()]
            base = before.rfind("...process.env")

            assert base != -1 and base < match.start(), (
                "githubAgentEnv() must be spread AFTER ...process.env so it wins"
            )

    @staticmethod
    def _fn_body(src: str) -> str:
        start = src.index("function githubAgentEnv(")

        return src[start : src.index("\n}", start)]


class TestCredentialHelperActuallyWorks:
    """The mechanism, exercised for real via git's credential protocol.

    Global and system config are ignored so ONLY the injected env can answer —
    otherwise windro's existing `credential.helper=store` (which holds a
    github.com token) would satisfy the probe and the test would pass whether or
    not the wiring worked. That mistake was made once already.
    """

    HELPER = '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'

    def _fill(self, host: str, env_extra: dict[str, str], tmp_path: Path) -> str:
        subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, capture_output=True, check=False)
        env = {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": __import__("os").environ.get("PATH", ""),
            **env_extra,
        }
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n",
            capture_output=True,
            cwd=tmp_path,
            env=env,
            text=True,
            check=False,
        )

        return proc.stdout + proc.stderr

    def test_no_credentials_without_the_env(self, tmp_path):
        out = self._fill("github.com", {}, tmp_path)

        assert "password=" not in out, "something else is answering; probe is not isolated"

    def test_credentials_supplied_with_the_env(self, tmp_path):
        out = self._fill(
            "github.com",
            {
                "GITHUB_TOKEN": "test-token-value",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
                "GIT_CONFIG_VALUE_0": self.HELPER,
            },
            tmp_path,
        )

        assert "username=x-access-token" in out
        assert "password=test-token-value" in out

    def test_other_hosts_get_nothing(self, tmp_path):
        """Scoping check: a GitHub token must not be offered to gitlab."""
        out = self._fill(
            "gitlab.com",
            {
                "GITHUB_TOKEN": "test-token-value",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
                "GIT_CONFIG_VALUE_0": self.HELPER,
            },
            tmp_path,
        )

        assert "test-token-value" not in out, "the GitHub token leaked to another host"
