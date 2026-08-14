"""A failed post-pull step must not trigger a full re-download.

FORK regression test.

``cmd_update``'s Windows handler caught ANY ``CalledProcessError`` and answered
with a full ZIP re-download of the project. The most common Windows failure is not
a broken repo at all: ``uv pip install -e .`` cannot delete
``venv\\Scripts\\hermes.exe`` because the update is RUNNING from it, so it exits 2
with "The process cannot access the file because it is being used by another
process (os error 32)".

Observed: the pull succeeded, the editable reinstall failed on a 45KB shim, and the
updater began downloading the whole repository over a stalled connection with no
timeout — which the user experienced as an update hanging for many minutes. Had it
finished it would have extracted over a working tree that was already correct,
which is how this fork previously lost its own files.

``_worktree_matches_remote`` is the guard: if HEAD already equals the remote branch
tip, the code on disk IS the update and nothing needs re-fetching.
"""

from __future__ import annotations

import subprocess

import pytest

from hermes_cli import main as cli_main


class TestWorktreeMatchesRemote:
    def _fake_git(self, monkeypatch, mapping: dict, returncode: int = 0):
        """Stub subprocess.run for `git rev-parse --verify <rev>`."""

        def fake_run(cmd, **kwargs):
            rev = cmd[-1]
            out = mapping.get(rev)

            class R:
                pass

            r = R()
            r.returncode = returncode if out is not None else 1
            r.stdout = f"{out}\n" if out is not None else ""
            r.stderr = ""
            return r

        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

    def test_true_when_head_equals_the_remote_tip(self, monkeypatch):
        self._fake_git(
            monkeypatch, {"HEAD": "abc123", "refs/remotes/origin/main": "abc123"}
        )
        assert cli_main._worktree_matches_remote("main") is True

    def test_false_when_head_is_behind(self, monkeypatch):
        """A genuinely stale checkout keeps the existing fallback behaviour."""
        self._fake_git(
            monkeypatch, {"HEAD": "old111", "refs/remotes/origin/main": "new222"}
        )
        assert cli_main._worktree_matches_remote("main") is False

    def test_false_when_the_remote_ref_is_missing(self, monkeypatch):
        self._fake_git(monkeypatch, {"HEAD": "abc123"})
        assert cli_main._worktree_matches_remote("main") is False

    def test_false_when_head_cannot_be_read(self, monkeypatch):
        """Detached/broken checkout: do not claim the code is current."""
        self._fake_git(monkeypatch, {"refs/remotes/origin/main": "abc123"})
        assert cli_main._worktree_matches_remote("main") is False

    def test_a_blank_branch_defaults_to_main(self, monkeypatch):
        self._fake_git(
            monkeypatch, {"HEAD": "abc123", "refs/remotes/origin/main": "abc123"}
        )
        assert cli_main._worktree_matches_remote("") is True
        assert cli_main._worktree_matches_remote(None) is True

    def test_a_named_branch_is_honoured(self, monkeypatch):
        self._fake_git(
            monkeypatch,
            {"HEAD": "abc123", "refs/remotes/origin/dev": "abc123", "refs/remotes/origin/main": "zzz"},
        )
        assert cli_main._worktree_matches_remote("dev") is True

    def test_git_failing_entirely_is_not_a_match(self, monkeypatch):
        """Conservative by design: a false positive would skip a repair the user
        needs, while a false negative only costs the re-download they already had."""

        def boom(*_a, **_k):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(cli_main.subprocess, "run", boom)
        assert cli_main._worktree_matches_remote("main") is False

    def test_a_git_timeout_is_not_a_match(self, monkeypatch):
        def slow(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="git", timeout=20)

        monkeypatch.setattr(cli_main.subprocess, "run", slow)
        assert cli_main._worktree_matches_remote("main") is False

    @pytest.mark.parametrize("noise", ["abc123\n", "  abc123  \n", "abc123"])
    def test_output_is_trimmed(self, monkeypatch, noise):
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = noise
                stderr = ""

            return R()

        monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
        assert cli_main._worktree_matches_remote("main") is True
