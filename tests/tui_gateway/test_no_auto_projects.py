"""A session with no explicit project must not become a folder.

FORK regression test.

``project_tree.build_tree`` synthesises projects in three tiers:

  1. explicit projects from projects.db  (user-created)
  2. AUTO projects from any unowned session's git repo root
  3. AUTO projects from a disk/history scan, for repos with NO sessions at all

Tiers 2 and 3 mean that merely running one session inside a checkout produces a
permanent sidebar folder nobody asked for, and removing it requires editing the
session's stored cwd by hand. That is how a stray session whose cwd had leaked to a
developer checkout (see the `terminal.cwd: .` -> os.getcwd() fix) put a "hermes"
project in the sidebar of an install that had nothing to do with it.

``auto_projects=False`` (the fork default) suppresses both auto tiers. Explicit
projects and their session scoping are untouched.
"""

from __future__ import annotations

import pytest

from tui_gateway import project_tree


def _explicit_project(path: str = "C:/repos/explicit") -> dict:
    """A projects.db-shaped row. `folders` is what owns sessions, not primary_path."""
    return {
        "id": "p_real",
        "name": "Explicit One",
        "primary_path": path,
        "archived": False,
        "folders": [{"path": path, "label": "explicit", "is_primary": True, "added_at": 0}],
    }


def _session(sid: str, cwd: str, *, root: str = "") -> dict:
    return {
        "id": sid,
        "cwd": cwd,
        "git_repo_root": root or cwd,
        "git_branch": "main" if cwd else "",
        "started_at": 1,
        "last_active": 1,
    }


def _build(*, auto_projects: bool, sessions=None, discovered=None, projects=None) -> dict:
    return project_tree.build_tree(
        projects if projects is not None else [_explicit_project()],
        sessions if sessions is not None else [],
        discovered or [],
        None,
        exists=lambda _p: True,
        auto_projects=auto_projects,
    )


def _labels(tree: dict) -> list[str]:
    return [p.get("label") for p in tree["projects"]]


def _auto_labels(tree: dict) -> list[str]:
    return sorted(p.get("label") for p in tree["projects"] if p.get("isAuto"))


class TestAutoProjectsDisabled:
    def test_a_session_in_a_repo_does_not_become_a_project(self):
        """The reported symptom: a "hermes" folder appearing on its own."""
        tree = _build(
            auto_projects=False,
            sessions=[_session("s2", "C:/wnx-projects/personal/hermes")],
        )

        assert _auto_labels(tree) == []
        assert "hermes" not in _labels(tree)

    def test_such_a_session_falls_back_to_recents(self):
        """Not scoped to any project == shown in flat Recents."""
        tree = _build(
            auto_projects=False,
            sessions=[_session("s2", "C:/wnx-projects/personal/hermes")],
        )

        assert "s2" not in tree["scoped_session_ids"]

    def test_a_detached_session_is_unaffected(self):
        tree = _build(auto_projects=False, sessions=[_session("s3", "")])

        assert _auto_labels(tree) == []
        assert "s3" not in tree["scoped_session_ids"]

    def test_disk_scanned_repos_do_not_become_projects(self):
        """Tier 3 is the more aggressive one — a repo with NO sessions at all."""
        tree = _build(
            auto_projects=False,
            discovered=[
                {"root": "C:/repos/never-opened", "label": "never-opened", "sessions": 0, "last_active": 0}
            ],
        )

        assert _auto_labels(tree) == []

    def test_explicit_projects_still_appear(self):
        tree = _build(auto_projects=False, sessions=[_session("s1", "C:/repos/explicit")])

        assert _labels(tree) == ["Explicit One"]

    def test_explicit_projects_keep_their_sessions(self):
        """The invariant that matters most: suppressing auto tiers must not
        un-scope a session from the project that genuinely owns it."""
        tree = _build(auto_projects=False, sessions=[_session("s1", "C:/repos/explicit")])

        assert "s1" in tree["scoped_session_ids"]

    def test_a_mixed_tree_keeps_only_the_explicit_project(self):
        tree = _build(
            auto_projects=False,
            sessions=[
                _session("s1", "C:/repos/explicit"),
                _session("s2", "C:/wnx-projects/personal/hermes"),
                _session("s3", ""),
            ],
            discovered=[
                {"root": "C:/repos/never-opened", "label": "never-opened", "sessions": 0, "last_active": 0}
            ],
        )

        assert _labels(tree) == ["Explicit One"]
        assert sorted(tree["scoped_session_ids"]) == ["s1"]


class TestAutoProjectsEnabled:
    """The old behaviour must remain reachable via projects.auto_projects: true."""

    def test_repo_sessions_are_grouped(self):
        tree = _build(
            auto_projects=True,
            sessions=[_session("s2", "C:/wnx-projects/personal/hermes")],
        )

        assert "hermes" in _auto_labels(tree)
        assert "s2" in tree["scoped_session_ids"]

    def test_disk_scanned_repos_are_grouped(self):
        tree = _build(
            auto_projects=True,
            discovered=[
                {"root": "C:/repos/never-opened", "label": "never-opened", "sessions": 0, "last_active": 0}
            ],
        )

        assert "never-opened" in _auto_labels(tree)

    def test_default_argument_preserves_the_upstream_contract(self):
        """build_tree's own default stays True; the fork default lives in the
        caller's config read, so the pure builder is unchanged for other callers."""
        import inspect

        assert inspect.signature(project_tree.build_tree).parameters["auto_projects"].default is True


class TestConfigGate:
    """`projects.auto_projects` drives the caller; default is OFF."""

    @pytest.mark.parametrize(
        "cfg,expected",
        [
            ({}, False),
            ({"projects": {}}, False),
            ({"projects": None}, False),
            ({"projects": {"auto_projects": False}}, False),
            ({"projects": {"auto_projects": True}}, True),
            ({"projects": {"auto_projects": "true"}}, True),
            ({"projects": {"auto_projects": "yes"}}, True),
            ({"projects": {"auto_projects": "0"}}, False),
        ],
    )
    def test_gate(self, monkeypatch, cfg, expected):
        import tui_gateway.server as server

        monkeypatch.setattr(server, "_load_cfg", lambda: cfg)
        assert server._auto_projects_enabled() is expected

    def test_a_broken_config_does_not_raise(self, monkeypatch):
        import tui_gateway.server as server

        def boom():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(server, "_load_cfg", boom)
        assert server._auto_projects_enabled() is False
