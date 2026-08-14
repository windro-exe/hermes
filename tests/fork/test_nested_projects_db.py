"""FORK: nested Projects — a project's identity is a PATH of slugs.

Covers what plain CRUD tests do not: uniqueness scoped to a parent, paths derived
(never stored) so renames/moves re-path a whole subtree, cascade semantics, and
the in-place rebuild that drops v1's global ``slug UNIQUE`` constraint.
"""

from __future__ import annotations

import sqlite3

import pytest

from hermes_cli import projects_db as pdb


@pytest.fixture
def conn(tmp_path):
    c = pdb.connect(db_path=tmp_path / "projects.db")
    try:
        yield c
    finally:
        c.close()


def _tree(conn) -> dict[str, str]:
    """``name -> full path`` for every live project."""
    return {p.name: p.path for p in pdb.list_projects(conn)}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_path_is_the_chain_of_slugs(conn):
    official = pdb.create_project(conn, name="Official")
    os_projects = pdb.create_project(conn, name="OS Projects", parent=official)
    pdb.create_project(conn, name="Nettacker", parent=os_projects)

    assert _tree(conn) == {
        "Official": "official",
        "OS Projects": "official/os-projects",
        "Nettacker": "official/os-projects/nettacker",
    }


def test_depth_is_unlimited(conn):
    parent = None
    for i in range(12):
        parent = pdb.create_project(conn, name=f"Level {i}", parent=parent)

    deepest = pdb.get_project(conn, parent)
    assert deepest.depth == 11
    assert deepest.path == "/".join(f"level-{i}" for i in range(12))


def test_parent_may_be_named_by_path(conn):
    pdb.create_project(conn, name="Official")
    pdb.create_project(conn, name="OS Projects", parent="official")
    # The whole point: a deep parent is addressable without knowing its id.
    child = pdb.create_project(conn, name="Nettacker", parent="official/os-projects")

    assert pdb.get_project(conn, child).path == "official/os-projects/nettacker"


def test_unknown_parent_is_rejected(conn):
    with pytest.raises(ValueError, match="no such parent project"):
        pdb.create_project(conn, name="Orphan", parent="nope/not-here")


# ---------------------------------------------------------------------------
# Slug uniqueness is per-parent
# ---------------------------------------------------------------------------


def test_same_slug_under_different_parents(conn):
    official = pdb.create_project(conn, name="Official")
    personal = pdb.create_project(conn, name="Personal")
    a = pdb.create_project(conn, name="Docs", parent=official)
    b = pdb.create_project(conn, name="Docs", parent=personal)

    # v1 made slugs globally unique, so the second would have become `docs-2`.
    assert pdb.get_project(conn, a).path == "official/docs"
    assert pdb.get_project(conn, b).path == "personal/docs"


def test_true_siblings_still_deduplicate(conn):
    official = pdb.create_project(conn, name="Official")
    pdb.create_project(conn, name="Docs", parent=official)
    dup = pdb.create_project(conn, name="Docs", parent=official)

    assert pdb.get_project(conn, dup).path == "official/docs-2"


def test_ambiguous_bare_slug_does_not_resolve(conn):
    official = pdb.create_project(conn, name="Official")
    personal = pdb.create_project(conn, name="Personal")
    pdb.create_project(conn, name="Docs", parent=official)
    pdb.create_project(conn, name="Docs", parent=personal)

    # Two projects answer to `docs`. Guessing one would silently file work in the
    # wrong project, so the caller must qualify it.
    assert pdb.get_project(conn, "docs") is None
    assert pdb.get_project(conn, "official/docs") is not None


def test_lookup_tolerates_separator_and_case(conn):
    pdb.create_project(conn, name="Official")
    child = pdb.create_project(conn, name="OS Projects", parent="official")

    for spelling in ("official/os-projects", "Official\\OS-Projects", " official/os-projects/ "):
        assert pdb.get_project(conn, spelling).id == child, spelling


# ---------------------------------------------------------------------------
# Moves and renames re-path the subtree
# ---------------------------------------------------------------------------


def test_move_repaths_the_whole_subtree(conn):
    official = pdb.create_project(conn, name="Official")
    personal = pdb.create_project(conn, name="Personal")
    mid = pdb.create_project(conn, name="OS Projects", parent=official)
    leaf = pdb.create_project(conn, name="Nettacker", parent=mid)

    assert pdb.move_project(conn, mid, personal) is True
    assert pdb.get_project(conn, leaf).path == "personal/os-projects/nettacker"

    # Promoting to a root drops the whole prefix.
    assert pdb.move_project(conn, mid, None) is True
    assert pdb.get_project(conn, leaf).path == "os-projects/nettacker"


def test_move_is_a_no_op_when_the_parent_is_unchanged(conn):
    official = pdb.create_project(conn, name="Official")
    child = pdb.create_project(conn, name="Child", parent=official)

    assert pdb.move_project(conn, child, official) is False


def test_move_rejects_cycles(conn):
    root = pdb.create_project(conn, name="Root")
    mid = pdb.create_project(conn, name="Mid", parent=root)
    leaf = pdb.create_project(conn, name="Leaf", parent=mid)

    with pytest.raises(ValueError, match="own parent"):
        pdb.move_project(conn, mid, mid)
    # Into its own descendant: would detach the subtree from every root and make
    # its path unresolvable.
    with pytest.raises(ValueError, match="own subtree"):
        pdb.move_project(conn, mid, leaf)

    assert pdb.get_project(conn, leaf).path == "root/mid/leaf"


def test_move_renames_on_sibling_collision(conn):
    official = pdb.create_project(conn, name="Official")
    personal = pdb.create_project(conn, name="Personal")
    pdb.create_project(conn, name="Docs", parent=personal)
    moving = pdb.create_project(conn, name="Docs", parent=official)

    pdb.move_project(conn, moving, personal)

    # Its new parent already had a `docs`, so the mover takes the free slug.
    assert pdb.get_project(conn, moving).path == "personal/docs-2"


def test_slug_rename_repaths_descendants(conn):
    root = pdb.create_project(conn, name="Nettacker")
    leaf = pdb.create_project(conn, name="Deep", parent=root)

    assert pdb.update_project(conn, root, slug="nettacker-ng") is True
    assert pdb.get_project(conn, leaf).path == "nettacker-ng/deep"


def test_slug_rename_cannot_collide_with_a_sibling(conn):
    parent = pdb.create_project(conn, name="Parent")
    pdb.create_project(conn, name="Taken", parent=parent)
    other = pdb.create_project(conn, name="Other", parent=parent)

    pdb.update_project(conn, other, slug="taken")

    assert pdb.get_project(conn, other).path == "parent/taken-2"


# ---------------------------------------------------------------------------
# Subtree helpers, archive, delete
# ---------------------------------------------------------------------------


def test_subtree_helpers(conn):
    root = pdb.create_project(conn, name="Root")
    mid = pdb.create_project(conn, name="Mid", parent=root)
    leaf = pdb.create_project(conn, name="Leaf", parent=mid)
    sibling = pdb.create_project(conn, name="Sibling")

    assert set(pdb.descendant_ids(conn, root)) == {mid, leaf}
    assert pdb.subtree_ids(conn, root) == [root, mid, leaf]
    assert pdb.descendant_ids(conn, sibling) == []
    assert [p.id for p in pdb.list_children(conn, root)] == [mid]
    assert [p.id for p in pdb.list_children(conn, None)] == [root, sibling]


def test_archive_and_restore_cascade(conn):
    root = pdb.create_project(conn, name="Root")
    leaf = pdb.create_project(conn, name="Leaf", parent=root)

    pdb.archive_project(conn, root)
    # A hidden parent with a visible child would strand that child: its path
    # resolves through a project the tree no longer lists.
    assert pdb.list_projects(conn) == []
    assert len(pdb.list_projects(conn, include_archived=True)) == 2

    pdb.restore_project(conn, root)
    assert {p.id for p in pdb.list_projects(conn)} == {root, leaf}


def test_delete_cascades_children_and_their_folders(conn):
    root = pdb.create_project(conn, name="Root", folders=["/www/root"])
    leaf = pdb.create_project(conn, name="Leaf", parent=root, folders=["/www/leaf"])
    survivor = pdb.create_project(conn, name="Survivor", folders=["/www/survivor"])

    assert pdb.delete_project(conn, root) is True

    assert [p.id for p in pdb.list_projects(conn)] == [survivor]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM project_folders WHERE project_id = ?", (leaf,)
    ).fetchone()[0]
    assert orphans == 0


def test_branch_name_uses_the_full_path(conn):
    root = pdb.create_project(conn, name="Official")
    leaf = pdb.create_project(conn, name="Nettacker", parent=root)

    assert pdb.branch_name_for(pdb.get_project(conn, leaf), "t_ab12") == (
        "official/nettacker/t_ab12"
    )
    # Root projects are unchanged, so existing branches keep their names.
    assert pdb.branch_name_for(pdb.get_project(conn, root), "t_ab12") == "official/t_ab12"


# ---------------------------------------------------------------------------
# Migration off the v1 schema
# ---------------------------------------------------------------------------


V1_SCHEMA = """
CREATE TABLE projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    description   TEXT,
    icon          TEXT,
    color         TEXT,
    board_slug    TEXT,
    primary_path  TEXT,
    created_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE project_folders (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    label       TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, path)
);
CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO projects (id, slug, name, primary_path, created_at)
    VALUES ('p_old', 'legacy', 'Legacy', '/www/legacy', 1);
INSERT INTO project_folders VALUES ('p_old', '/www/legacy', NULL, 1, 1);
INSERT INTO project_meta VALUES ('active_id', 'p_old');
"""


@pytest.fixture
def legacy_db(tmp_path):
    """A v1 projects.db, written before `parent_id` and per-parent slugs existed."""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(V1_SCHEMA)
    raw.commit()
    raw.close()
    # `connect` caches its per-path init, so a fresh temp file must not be
    # considered already-migrated from an earlier test.
    pdb._INITIALIZED_PATHS.discard(str(path.resolve()))
    return path


def test_v1_db_upgrades_in_place(legacy_db):
    conn = pdb.connect(db_path=legacy_db)
    try:
        legacy = pdb.get_project(conn, "p_old")

        assert legacy is not None
        assert (legacy.path, legacy.depth, legacy.parent_id) == ("legacy", 0, None)
        # The rebuild copies rows into a new table; the folders and the active
        # pointer must come through it intact.
        assert [f.path for f in legacy.folders] == ["/www/legacy"]
        assert legacy.primary_path == "/www/legacy"
        assert pdb.get_active_id(conn) == "p_old"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # The v1 `slug UNIQUE` autoindex is gone, so nesting is possible.
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()[0]
        assert "UNIQUE" not in ddl.upper()

        child = pdb.create_project(conn, name="Child", parent="p_old")
        assert pdb.get_project(conn, child).path == "legacy/child"
    finally:
        conn.close()


def test_migration_is_idempotent_across_opens(legacy_db):
    first = pdb.connect(db_path=legacy_db)
    try:
        child = pdb.create_project(first, name="Child", parent="p_old")
    finally:
        first.close()

    # Force the per-process init cache to miss, as a fresh process would.
    pdb._INITIALIZED_PATHS.discard(str(legacy_db.resolve()))
    second = pdb.connect(db_path=legacy_db)
    try:
        assert pdb.get_project(second, child).path == "legacy/child"
        assert pdb.get_project(second, "p_old") is not None
        # Foreign keys must be back ON after the rebuild toggled them off, or
        # every later cascade silently stops working.
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        second.close()
