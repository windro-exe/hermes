"""Guard: creating a project makes its folder a git repo, in both modes.

Everything branch-shaped downstream needs a repo — sidebar lanes group sessions
by the branch recorded in their cwd, and worktrees are how a branch gets its own
directory. A project folder that is not a repo records no branch at all, which is
why lanes were meaningless in windro's `Documents\\Asthra HR admin` project.

The desktop has two transports, and the trap is that only one is obvious: local
uses `hermes:git:init` over IPC, remote-gateway mode uses `POST /api/git/init`.
Implementing only the local path leaves remote projects silently without a repo,
so the Python half is guarded here.

Fork-owned file; upstream has no tests/fork/.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli.web_git import repo_init


def _commits(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        capture_output=True, cwd=repo, text=True, check=False,
    )

    return int((out.stdout or "0").strip() or 0)


def _is_repo(repo: Path) -> bool:
    out = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, cwd=repo, text=True, check=False,
    )

    return (out.stdout or "").strip() == "true"


class TestRepoInit:
    def test_initialises_a_plain_folder(self, tmp_path):
        assert not _is_repo(tmp_path)

        repo_init(str(tmp_path))

        assert _is_repo(tmp_path)

    def test_seeds_a_head_so_branches_and_worktrees_work(self, tmp_path):
        """Without a HEAD there is nothing to branch from, so a worktree — the
        mechanism behind per-branch sessions — cannot be created."""
        repo_init(str(tmp_path))

        assert _commits(tmp_path) == 1

    def test_is_idempotent(self, tmp_path):
        """Opening an existing project must never add commits to its history."""
        repo_init(str(tmp_path))
        repo_init(str(tmp_path))
        repo_init(str(tmp_path))

        assert _commits(tmp_path) == 1

    def test_leaves_an_existing_repo_and_its_history_alone(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=False)
        (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-qm", "real work"], cwd=tmp_path, capture_output=True, check=False)

        before = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, cwd=tmp_path, text=True, check=False
        ).stdout.strip()

        repo_init(str(tmp_path))

        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, cwd=tmp_path, text=True, check=False
        ).stdout.strip()

        assert after == before, "repo_init rewrote an existing project's HEAD"
        assert _commits(tmp_path) == 1

    def test_works_without_global_git_identity(self, tmp_path, monkeypatch):
        """A fresh machine may have no user.name/user.email configured at all;
        the seed commit passes an inline identity so it still lands."""
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nonexistent-gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "nonexistent-gitconfig"))

        target = tmp_path / "project"
        target.mkdir()
        repo_init(str(target))

        assert _commits(target) == 1

    def test_returns_the_path_it_acted_on(self, tmp_path):
        assert repo_init(str(tmp_path)) == {"ok": True, "path": str(tmp_path)}


class TestBothTransportsAreWired:
    """Local IPC and remote REST must both exist, or one mode silently skips git."""

    def _read(self, rel: str) -> str:
        root = Path(__file__).resolve().parents[2]

        return (root / rel).read_text(encoding="utf-8")

    def test_remote_rest_route_exists(self):
        src = self._read("hermes_cli/web_server.py")

        assert '"/api/git/init"' in src, (
            "the remote git-init route is gone — projects created against a remote "
            "gateway would silently get no repo"
        )
        assert "_web_git.repo_init" in src

    def test_local_ipc_handler_exists(self):
        src = self._read("apps/desktop/electron/main.ts")

        assert "'hermes:git:init'" in src, "the local git-init IPC handler is gone"
        assert "ensureGitRepo(" in src

    def test_preload_exposes_it(self):
        src = self._read("apps/desktop/electron/preload.ts")

        assert "hermes:git:init" in src, (
            "the renderer can no longer reach git init — the handler exists but "
            "nothing can call it"
        )

    def test_remote_bridge_implements_it(self):
        src = self._read("apps/desktop/src/lib/desktop-git.ts")

        assert "gitPost('init'" in src, (
            "the remote bridge lost its init, so remote projects get no repo"
        )

    def test_project_creation_calls_it(self):
        src = self._read("apps/desktop/src/store/projects.ts")

        assert "initProjectGitRepo(" in src, (
            "project creation no longer initialises git — new projects would have "
            "no branch lanes and no worktrees"
        )

    @pytest.mark.parametrize("needle", ["git?.init", "hermesDesktop"])
    def test_it_goes_through_the_desktop_bridge(self, needle):
        """Not raw ipcRenderer: the bridge is what falls back to REST in remote mode."""
        src = self._read("apps/desktop/src/store/projects.ts")

        assert needle in src
