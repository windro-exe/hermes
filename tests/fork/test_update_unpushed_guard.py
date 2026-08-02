"""Guard: ``hermes update`` must never reset away unpushed commits.

windro pressed Update while a finished fix sat committed-but-unpushed on main.
Local was 1 ahead / 1 behind, so ``git pull --ff-only`` failed, and the fallback
``git reset --hard origin/<branch>`` moved HEAD onto the remote commit. The work
was recoverable from the reflog, but that is luck rather than design: the
autostash only covers UNCOMMITTED edits, and an unpushed commit exists nowhere
else.

The reset itself is upstream's and stays — it is the right recovery for a
mangled checkout or an upstream force-push, where local history is worth less
than the remote's. The fork only refuses in the one case that is destructive:
local carries commits the remote has never seen.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from hermes_cli.main import _count_commits_not_on_remote

GIT = ["git"]


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=False)


@pytest.fixture
def repo(tmp_path):
    """A clone with a real ``origin`` so rev-list has something to compare."""
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"

    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)], capture_output=True, check=False
    )
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(local)], capture_output=True, check=False
    )
    _run("git", "config", "user.email", "t@example.com", cwd=local)
    _run("git", "config", "user.name", "Test", cwd=local)

    (local / "a.txt").write_text("one\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=local)
    _run("git", "commit", "-qm", "first", cwd=local)
    _run("git", "push", "-q", "origin", "HEAD:main", cwd=local)
    _run("git", "branch", "-M", "main", cwd=local)
    _run("git", "fetch", "-q", "origin", cwd=local)
    _run("git", "branch", "--set-upstream-to=origin/main", "main", cwd=local)

    return local


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=repo)
    _run("git", "commit", "-qm", message, cwd=repo)


class TestCountCommitsNotOnRemote:
    def test_zero_when_in_sync(self, repo):
        assert _count_commits_not_on_remote(GIT, repo, "main") == 0

    def test_counts_a_single_unpushed_commit(self, repo):
        _commit(repo, "b.txt", "my unpushed fix")

        assert _count_commits_not_on_remote(GIT, repo, "main") == 1

    def test_counts_several(self, repo):
        _commit(repo, "b.txt", "one")
        _commit(repo, "c.txt", "two")

        assert _count_commits_not_on_remote(GIT, repo, "main") == 2

    def test_back_to_zero_after_pushing(self, repo):
        _commit(repo, "b.txt", "pushed soon")
        _run("git", "push", "-q", "origin", "main", cwd=repo)
        _run("git", "fetch", "-q", "origin", cwd=repo)

        assert _count_commits_not_on_remote(GIT, repo, "main") == 0

    def test_unpushed_commits_still_counted_when_also_behind(self, repo):
        """The exact shape that bit windro: diverged, 1 ahead AND 1 behind.

        ff-only fails here, which is what reaches the reset — so this is the case
        the guard has to catch.

        The divergence is built from this one repo (a side branch pushed to
        origin/main, then discarded locally) rather than a second clone, so it
        does not depend on what the bare repo's default HEAD points at.
        """
        # Local gains a commit origin has never seen.
        _commit(repo, "mine.txt", "my unpushed fix")

        # origin/main gains a DIFFERENT commit, pushed from a throwaway branch
        # started at the old remote tip.
        _run("git", "checkout", "-q", "-b", "sidebranch", "origin/main", cwd=repo)
        _commit(repo, "theirs.txt", "someone else's commit")
        _run("git", "push", "-q", "origin", "sidebranch:main", cwd=repo)
        _run("git", "checkout", "-q", "main", cwd=repo)
        _run("git", "branch", "-qD", "sidebranch", cwd=repo)
        _run("git", "fetch", "-q", "origin", cwd=repo)

        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True, text=True, cwd=repo, check=False,
        )

        assert behind.stdout.strip() == "1", "test setup did not create divergence"
        assert _count_commits_not_on_remote(GIT, repo, "main") == 1

    @pytest.mark.parametrize("branch", ["no-such-branch", ""])
    def test_fails_safe_on_a_bad_branch(self, repo, branch):
        """A probe that cannot answer must protect, not permit."""
        assert _count_commits_not_on_remote(GIT, repo, branch) >= 1

    def test_fails_safe_outside_a_repo(self, tmp_path):
        assert _count_commits_not_on_remote(GIT, tmp_path, "main") >= 1


class TestUpdatePathUsesTheGuard:
    """The helper is only useful if the update path actually consults it, and
    does so BEFORE the reset."""

    def _update_source(self) -> str:
        from hermes_cli import main as cli_main

        return inspect.getsource(cli_main)

    def test_the_guard_is_called_before_the_reset(self):
        src = self._update_source()

        guard = src.find("_count_commits_not_on_remote(")
        reset = src.find('"reset", "--hard", f"origin/{branch}"')

        assert guard != -1, (
            "the update path no longer counts unpushed commits — pressing Update "
            "with unpushed work will reset it away again"
        )
        assert reset != -1, "the reset fallback moved; re-check this guard"
        assert guard < reset, (
            "the unpushed-commit check must run BEFORE the reset, or it cannot "
            "prevent anything"
        )

    def test_it_exits_rather_than_resetting(self):
        """Refusing has to stop the flow, not just print a warning."""
        src = self._update_source()
        start = src.find("Fork guard: never reset away unpushed commits")
        reset = src.find('"reset", "--hard", f"origin/{branch}"')

        assert start != -1
        block = src[start:reset]

        assert "sys.exit(1)" in block, (
            "the guard prints its warning but falls through to the reset"
        )

    def test_it_restores_the_autostash_before_exiting(self):
        """Exiting must not leave the user's uncommitted work in a stash."""
        src = self._update_source()
        start = src.find("Fork guard: never reset away unpushed commits")
        reset = src.find('"reset", "--hard", f"origin/{branch}"')
        block = src[start:reset]

        assert "_restore_stashed_changes(" in block, (
            "the guard exits without restoring the changes the updater stashed, "
            "so the user is left with a dirty stash and a clean tree"
        )

    def test_the_force_push_recovery_path_survives(self):
        """Only the ahead case is refused; upstream's reset must still exist."""
        src = self._update_source()

        assert '"reset", "--hard", f"origin/{branch}"' in src, (
            "the reset fallback was removed entirely — that breaks recovery from "
            "a force-push or a mangled checkout, which is not what this guard is for"
        )
