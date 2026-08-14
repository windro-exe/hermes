"""FORK: the projects.* RPC surface for nested projects.

`projects.create` accepts a parent, `projects.move` re-parents, and
`projects.delete` takes the whole subtree — including the sessions of every
sub-project, which live in a DIFFERENT database and therefore have to be
collected before the cascade runs.
"""

from __future__ import annotations

import pytest

import tui_gateway.server as server


def _call(method, params=None):
    handler = server._methods[method]
    resp = handler(1, params or {})
    assert "error" not in resp, resp.get("error")
    return resp["result"]


def _error(method, params=None):
    resp = server._methods[method](1, params or {})
    assert "error" in resp, resp
    return resp["error"]


def _create(name, folder, parent=None):
    folder.mkdir(parents=True, exist_ok=True)
    params = {"name": name, "folders": [str(folder)]}
    if parent is not None:
        params["parent"] = parent
    return _call("projects.create", params)["project"]


class _FakeDb:
    """Stands in for the sessions DB, which is not the projects DB."""

    def __init__(self, owners: dict[str, list[str]]):
        self.owners = owners
        self.deleted: list[str] = []

    def session_ids_for_project(self, project_id: str) -> list[str]:
        return list(self.owners.get(project_id, ()))

    def delete_sessions(self, ids):
        self.deleted.extend(ids)
        return len(ids)


def test_move_is_registered():
    assert "projects.move" in server._methods


def test_create_nests_under_a_parent(tmp_path):
    official = _create("Official", tmp_path / "official")
    child = _create("OS Projects", tmp_path / "os-contributions", parent=official["id"])

    assert child["parent_id"] == official["id"]
    assert child["path"] == "official/os-projects"
    assert child["depth"] == 1


def test_create_accepts_a_parent_path(tmp_path):
    _create("Official", tmp_path / "official")
    _create("OS Projects", tmp_path / "osp", parent="official")
    # The agent and the CLI address a deep parent by path, not by id.
    grandchild = _create("Nettacker", tmp_path / "nettacker", parent="official/os-projects")

    assert grandchild["path"] == "official/os-projects/nettacker"


def test_create_with_an_unknown_parent_is_a_bad_argument(tmp_path):
    err = _error(
        "projects.create",
        {"name": "Orphan", "folders": [str(tmp_path)], "parent": "nope/missing"},
    )

    assert err["code"] == server._E_PROJECT_ARG
    assert "no such parent project" in err["message"]


def test_move_repaths_the_subtree(tmp_path):
    official = _create("Official", tmp_path / "official")
    personal = _create("Personal", tmp_path / "personal")
    mid = _create("OS Projects", tmp_path / "osp", parent=official["id"])
    leaf = _create("Nettacker", tmp_path / "nettacker", parent=mid["id"])

    payload = _call("projects.move", {"id": mid["id"], "parent": personal["id"]})
    paths = {p["id"]: p["path"] for p in payload["projects"]}

    assert paths[mid["id"]] == "personal/os-projects"
    # The move must be reported for the whole subtree, not just the moved row —
    # every descendant's path changed with it.
    assert paths[leaf["id"]] == "personal/os-projects/nettacker"


def test_move_without_a_parent_promotes_to_a_root(tmp_path):
    official = _create("Official", tmp_path / "official")
    child = _create("Child", tmp_path / "child", parent=official["id"])

    payload = _call("projects.move", {"id": child["id"]})
    moved = next(p for p in payload["projects"] if p["id"] == child["id"])

    assert (moved["parent_id"], moved["path"], moved["depth"]) == (None, "child", 0)


def test_move_into_own_subtree_is_rejected(tmp_path):
    parent = _create("Parent", tmp_path / "parent")
    child = _create("Child", tmp_path / "child", parent=parent["id"])

    err = _error("projects.move", {"id": parent["id"], "parent": child["id"]})

    assert err["code"] == server._E_PROJECT_ARG
    assert "own subtree" in err["message"]


def test_delete_takes_the_sub_projects_with_it(tmp_path):
    official = _create("Official", tmp_path / "official")
    mid = _create("OS Projects", tmp_path / "osp", parent=official["id"])
    leaf = _create("Nettacker", tmp_path / "nettacker", parent=mid["id"])
    keeper = _create("Personal", tmp_path / "personal")

    payload = _call("projects.delete", {"id": official["id"]})

    assert [p["id"] for p in payload["projects"]] == [keeper["id"]]
    assert all(p["id"] not in {mid["id"], leaf["id"]} for p in payload["projects"])


def test_delete_removes_descendant_sessions_too(tmp_path, monkeypatch):
    official = _create("Official", tmp_path / "official")
    mid = _create("OS Projects", tmp_path / "osp", parent=official["id"])
    leaf = _create("Nettacker", tmp_path / "nettacker", parent=mid["id"])
    other = _create("Personal", tmp_path / "personal")

    db = _FakeDb(
        {
            official["id"]: ["s_root"],
            mid["id"]: ["s_mid"],
            leaf["id"]: ["s_leaf"],
            other["id"]: ["s_untouched"],
        }
    )
    monkeypatch.setattr(server, "_get_db", lambda: db)

    payload = _call("projects.delete", {"id": official["id"]})

    # Sessions live in another database, so the FK cascade cannot reach them.
    assert sorted(db.deleted) == ["s_leaf", "s_mid", "s_root"]
    assert payload["deleted_sessions"] == 3


def test_keep_sessions_still_spares_the_subtree(tmp_path, monkeypatch):
    official = _create("Official", tmp_path / "official")
    mid = _create("OS Projects", tmp_path / "osp", parent=official["id"])

    db = _FakeDb({official["id"]: ["s_root"], mid["id"]: ["s_mid"]})
    monkeypatch.setattr(server, "_get_db", lambda: db)

    payload = _call("projects.delete", {"id": official["id"], "keep_sessions": True})

    assert db.deleted == []
    assert payload["deleted_sessions"] == 0


def test_update_renames_the_path_segment(tmp_path):
    parent = _create("Official", tmp_path / "official")
    child = _create("Nettacker", tmp_path / "nettacker", parent=parent["id"])

    _call("projects.update", {"id": parent["id"], "slug": "official-org"})
    listing = _call("projects.list")
    paths = {p["id"]: p["path"] for p in listing["projects"]}

    assert paths[parent["id"]] == "official-org"
    # Paths are derived, so the rename re-paths the subtree with no second write.
    assert paths[child["id"]] == "official-org/nettacker"


def test_list_and_get_expose_the_nesting_fields(tmp_path):
    parent = _create("Official", tmp_path / "official")
    child = _create("Child", tmp_path / "child", parent=parent["id"])

    fetched = _call("projects.get", {"id": child["path"]})["project"]

    assert fetched["id"] == child["id"]
    for row in _call("projects.list")["projects"]:
        assert set(row) >= {"parent_id", "path", "depth"}


def test_tree_carries_nesting_metadata_on_a_flat_list(tmp_path, monkeypatch):
    parent = _create("Official", tmp_path / "official")
    child = _create("Child", tmp_path / "child", parent=parent["id"])

    if server._get_db() is None:
        pytest.skip("sessions DB unavailable in this environment")

    tree = _call("projects.tree", {"preview_limit": 0})
    nodes = {node["id"]: node for node in tree["projects"]}

    assert parent["id"] in nodes and child["id"] in nodes
    assert nodes[child["id"]]["parentId"] == parent["id"]
    assert nodes[child["id"]]["projectPath"] == "official/child"
    assert nodes[child["id"]]["depth"] == 1
    # Flat: a nested project is still a top-level row, so every consumer that
    # scans this list by id keeps working.
    assert "children" not in nodes[child["id"]]
