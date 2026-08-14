"""Project membership is recorded, not re-derived from a moving cwd.

FORK regression tests.

Sessions had no ``project_id``: membership was recomputed from ``cwd`` on every
sidebar refresh. ``cwd`` is MUTABLE — the agent updates it as it works — so a chat
started inside a project silently left that project the moment the agent ``cd``'d
or cloned outside the project's folders.

Observed: a session created in "Os-Projects"
(``C:\\wnx-projects\\official\\os-contributions``) moved to the sibling repo
``nettacker`` and disappeared from the project. Nothing could correct it, because
the binding was never stored — and the previous auto-project behaviour hid the
escape by re-grouping the session under a folder named after wherever it landed.

The column is written once at creation and read back as authoritative, with path
matching kept as the fallback for rows that predate it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tui_gateway import project_tree


PROJECT_PATH = "C:/wnx-projects/official/os-contributions"
SIBLING_PATH = "C:/wnx-projects/official/nettacker"


def _project(pid: str = "p_390ddfcc", path: str = PROJECT_PATH) -> dict:
    return {
        "id": pid,
        "name": "Os-Projects",
        "archived": False,
        "primary_path": path,
        "folders": [{"path": path, "label": "os", "is_primary": True, "added_at": 0}],
    }


def _session(sid: str, cwd: str, project_id: str | None = None) -> dict:
    row = {
        "id": sid,
        "cwd": cwd,
        "git_repo_root": cwd,
        "git_branch": "main" if cwd else "",
        "started_at": 1,
        "last_active": 1,
    }
    if project_id is not None:
        row["project_id"] = project_id
    return row


def _tree(sessions: list[dict], projects: list[dict] | None = None) -> dict:
    return project_tree.build_tree(
        projects if projects is not None else [_project()],
        sessions,
        [],
        None,
        exists=lambda _p: True,
        auto_projects=False,
    )


class TestStickyMembership:
    def test_the_bug_a_session_that_moved_out_used_to_be_lost(self):
        """Without a stored id, a cwd outside the project means unowned."""
        tree = _tree([_session("escaped", SIBLING_PATH)])

        assert tree["scoped_session_ids"] == []

    def test_a_stored_project_id_survives_the_cwd_moving(self):
        """The fix: membership is stated, so the agent moving cwd cannot revoke it."""
        tree = _tree([_session("sticky", SIBLING_PATH, "p_390ddfcc")])

        assert tree["scoped_session_ids"] == ["sticky"]
        assert tree["projects"][0]["sessionCount"] == 1

    def test_it_survives_an_empty_cwd_too(self):
        """A detached session created inside a project still belongs to it."""
        tree = _tree([_session("detached-but-owned", "", "p_390ddfcc")])

        assert tree["scoped_session_ids"] == ["detached-but-owned"]

    def test_path_matching_still_works_for_legacy_rows(self):
        """Rows predating the column keep whatever grouping they had."""
        tree = _tree([_session("legacy", PROJECT_PATH)])

        assert tree["scoped_session_ids"] == ["legacy"]

    def test_a_stale_project_id_does_not_invent_a_project(self):
        """If the project was deleted, do not resurrect a phantom owner."""
        tree = _tree([_session("orphan", "C:/somewhere/else", "p_deleted")])

        assert tree["scoped_session_ids"] == []
        assert [p["label"] for p in tree["projects"]] == ["Os-Projects"]

    def test_a_stale_id_falls_back_to_path_matching(self):
        """A dangling id must not suppress ownership the path can still prove."""
        tree = _tree([_session("fallback", PROJECT_PATH, "p_deleted")])

        assert tree["scoped_session_ids"] == ["fallback"]

    def test_the_stored_id_wins_over_a_matching_path(self):
        """A session sitting inside project A's folder but STAMPED for project B
        belongs to B. The stated binding beats the inferred one — otherwise the
        stored id would be advisory and the bug could recur."""
        other = _project("p_other", "C:/unrelated")
        other["name"] = "Other"
        tree = _tree([_session("theirs", PROJECT_PATH, "p_other")], projects=[_project(), other])

        counts = {p["id"]: p.get("sessionCount") for p in tree["projects"]}
        assert counts["p_other"] == 1
        assert counts["p_390ddfcc"] == 0
        assert tree["scoped_session_ids"] == ["theirs"]

    def test_a_project_less_session_stays_out(self):
        tree = _tree([_session("plain", "", None)])

        assert tree["scoped_session_ids"] == []


class TestPersistence:
    """The column has to exist, migrate, and be projected — or none of the above
    fires in practice."""

    @pytest.fixture()
    def db(self):
        import hermes_state

        with tempfile.TemporaryDirectory(prefix="hermes-project-id-") as tmp:
            handle = hermes_state.SessionDB(db_path=Path(tmp) / "state.db")
            try:
                yield handle
            finally:
                handle.close()

    def test_project_id_round_trips(self, db):
        db.create_session("s1", source="desktop", project_id="p_x")
        db.append_message("s1", "user", "hi")

        rows = db.list_sessions_rich(limit=10, offset=0, min_message_count=1, compact_rows=True)
        assert rows[0]["project_id"] == "p_x"

    def test_omitting_it_stores_null(self, db):
        db.create_session("s1", source="desktop")
        db.append_message("s1", "user", "hi")

        rows = db.list_sessions_rich(limit=10, offset=0, min_message_count=1, compact_rows=True)
        assert rows[0]["project_id"] is None

    def test_the_compact_projection_includes_it(self):
        """compact_rows is derived from SCHEMA_SQL; if it ever stops including this
        column the sidebar silently reverts to cwd-derived membership."""
        import hermes_state

        assert "s.project_id" in hermes_state.SessionDB._compact_session_cols()

    def test_a_child_inherits_the_parent_project(self, db):
        """Compression forks, branches and delegate spawns create rows with no
        project of their own. Without inheritance a long session would drop out of
        its project the moment it compressed."""
        db.create_session("parent", source="desktop", project_id="p_x")
        db.append_message("parent", "user", "hi")
        db.create_session("child", source="desktop", parent_session_id="parent")
        db.append_message("child", "user", "hi")

        rows = {
            r["id"]: r
            for r in db.list_sessions_rich(
                limit=10, offset=0, min_message_count=1, include_children=True, compact_rows=True
            )
        }
        assert rows["child"]["project_id"] == "p_x"

    def test_an_explicit_child_project_is_not_overwritten(self, db):
        db.create_session("parent", source="desktop", project_id="p_x")
        db.append_message("parent", "user", "hi")
        db.create_session("child", source="desktop", parent_session_id="parent", project_id="p_y")
        db.append_message("child", "user", "hi")

        rows = {
            r["id"]: r
            for r in db.list_sessions_rich(
                limit=10, offset=0, min_message_count=1, include_children=True, compact_rows=True
            )
        }
        assert rows["child"]["project_id"] == "p_y"


class TestCascadeDelete:
    """Deleting a project deletes the sessions that belong to it."""

    @pytest.fixture()
    def db(self):
        import hermes_state

        with tempfile.TemporaryDirectory(prefix="hermes-cascade-") as tmp:
            handle = hermes_state.SessionDB(db_path=Path(tmp) / "state.db")
            for sid, kwargs in (
                ("owned-1", {"project_id": "p_keep"}),
                ("owned-2", {"project_id": "p_keep"}),
                ("other", {"project_id": "p_other"}),
                ("path-matched", {"cwd": "C:/repos/keep"}),
                ("plain", {}),
            ):
                handle.create_session(sid, source="desktop", **kwargs)
                handle.append_message(sid, "user", "hi")
            try:
                yield handle
            finally:
                handle.close()

    def _ids(self, db):
        return sorted(
            r["id"]
            for r in db.list_sessions_rich(
                limit=50, offset=0, min_message_count=1, include_children=True, compact_rows=True
            )
        )

    def test_finds_only_explicitly_owned_sessions(self, db):
        assert sorted(db.session_ids_for_project("p_keep")) == ["owned-1", "owned-2"]

    def test_a_blank_id_matches_nothing(self, db):
        """Guard against matching NULL: that would sweep up every project-less
        session on any project delete."""
        assert db.session_ids_for_project("") == []
        assert db.session_ids_for_project("   ") == []
        assert db.session_ids_for_project(None) == []

    def test_an_unknown_id_matches_nothing(self, db):
        assert db.session_ids_for_project("p_nope") == []

    def test_deleting_removes_only_that_project_sessions(self, db):
        assert db.delete_sessions(db.session_ids_for_project("p_keep")) == 2
        assert self._ids(db) == ["other", "path-matched", "plain"]

    def test_path_matched_sessions_are_not_destroyed(self, db):
        """Inferred membership must never authorise deletion — the user never
        filed those sessions under the project."""
        db.delete_sessions(db.session_ids_for_project("p_keep"))

        assert "path-matched" in self._ids(db)

    def test_another_project_is_untouched(self, db):
        db.delete_sessions(db.session_ids_for_project("p_keep"))

        assert "other" in self._ids(db)
