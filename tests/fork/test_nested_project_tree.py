"""FORK: nesting invariants for the authoritative project-tree builder.

The payload stays FLAT — every project remains a top-level row — and carries the
metadata the sidebar needs to draw the hierarchy (`parentId`, `projectPath`,
`depth`) plus subtree rollups (`totalSessionCount`, `lastActive`,
`previewSessions`). Flatness is the load-bearing part: a nested payload would
hide sub-projects from every consumer that scans the tree list by id or path.
"""

from __future__ import annotations

from tui_gateway import project_tree as pt

_SID = 0


def _session(cwd, *, project_id="", last_active=1000, **over):
    global _SID
    _SID += 1
    row = {
        "id": f"s{_SID}",
        "cwd": cwd,
        "git_branch": "",
        "git_repo_root": "",
        "started_at": last_active,
        "last_active": last_active,
        "project_id": project_id,
    }
    row.update(over)
    return row


def _project(pid, name, folders, *, parent_id=None, path="", depth=0, **over):
    row = {
        "id": pid,
        "name": name,
        "slug": name.lower(),
        "primary_path": folders[0] if folders else None,
        "archived": False,
        "folders": [{"path": p, "is_primary": i == 0} for i, p in enumerate(folders)],
        "parent_id": parent_id,
        "path": path or name.lower(),
        "depth": depth,
    }
    row.update(over)
    return row


def _build(projects, sessions, **over):
    kwargs = {"discovered_repos": [], "auto_projects": False}
    kwargs.update(over)
    return pt.build_tree(projects, sessions, **kwargs)


def _by_id(tree):
    return {node["id"]: node for node in tree["projects"]}


# The shape windro asked for: a namespace project holding two nested ones whose
# folders are SIBLINGS on disk. Nesting is a namespace, not the filesystem.
def _official_tree():
    return [
        _project("p_official", "Official", [], path="official"),
        _project(
            "p_osp",
            "OS Projects",
            ["/wnx/official/os-contributions"],
            parent_id="p_official",
            path="official/os-projects",
            depth=1,
        ),
        _project(
            "p_nett",
            "Nettacker",
            ["/wnx/official/nettacker"],
            parent_id="p_osp",
            path="official/os-projects/nettacker",
            depth=2,
        ),
    ]


def test_nesting_metadata_rides_on_a_flat_list():
    tree = _build(_official_tree(), [])

    # Flat: three top-level rows, no `children` nesting to walk.
    assert [node["id"] for node in tree["projects"]] == ["p_official", "p_osp", "p_nett"]

    nodes = _by_id(tree)
    assert nodes["p_nett"]["parentId"] == "p_osp"
    assert nodes["p_nett"]["projectPath"] == "official/os-projects/nettacker"
    assert nodes["p_nett"]["depth"] == 2
    assert nodes["p_official"]["parentId"] is None
    assert nodes["p_official"]["depth"] == 0


def test_project_path_is_independent_of_the_folder_path():
    tree = _build(_official_tree(), [])
    nodes = _by_id(tree)

    # On disk these two are siblings; in the namespace one is inside the other.
    assert nodes["p_osp"]["path"] == "/wnx/official/os-contributions"
    assert nodes["p_nett"]["path"] == "/wnx/official/nettacker"
    assert nodes["p_nett"]["projectPath"].startswith(nodes["p_osp"]["projectPath"] + "/")


def test_session_counts_roll_up_to_ancestors():
    sessions = [
        _session("/wnx/official/os-contributions", project_id="p_osp"),
        _session("/wnx/official/nettacker", project_id="p_nett"),
        _session("/wnx/official/nettacker/sub", project_id="p_nett"),
    ]
    nodes = _by_id(_build(_official_tree(), sessions))

    # Own counts stay honest; the rollup is a separate field.
    assert (nodes["p_official"]["sessionCount"], nodes["p_official"]["totalSessionCount"]) == (0, 3)
    assert (nodes["p_osp"]["sessionCount"], nodes["p_osp"]["totalSessionCount"]) == (1, 3)
    assert (nodes["p_nett"]["sessionCount"], nodes["p_nett"]["totalSessionCount"]) == (2, 2)


def test_last_active_rolls_up_so_a_namespace_sorts_by_its_children():
    sessions = [_session("/wnx/official/nettacker", project_id="p_nett", last_active=9000)]
    nodes = _by_id(_build(_official_tree(), sessions))

    # A parent with no sessions of its own would otherwise sort as dead.
    assert nodes["p_official"]["lastActive"] == 9000
    assert nodes["p_osp"]["lastActive"] == 9000


def test_parent_previews_stand_in_for_the_subtree():
    deep = _session("/wnx/official/nettacker", project_id="p_nett", last_active=9000)
    mid = _session("/wnx/official/os-contributions", project_id="p_osp", last_active=8000)
    nodes = _by_id(_build(_official_tree(), [deep, mid], preview_limit=3))

    # Collapsed, the namespace row is the only thing on screen — it has to be
    # able to show what is underneath it.
    assert [s["id"] for s in nodes["p_official"]["previewSessions"]] == [deep["id"], mid["id"]]
    assert [s["id"] for s in nodes["p_osp"]["previewSessions"]] == [deep["id"], mid["id"]]
    assert [s["id"] for s in nodes["p_nett"]["previewSessions"]] == [deep["id"]]


def test_rolled_up_previews_respect_the_limit_and_do_not_repeat():
    sessions = [
        _session("/wnx/official/nettacker", project_id="p_nett", last_active=stamp)
        for stamp in (9000, 8000, 7000, 6000)
    ]
    nodes = _by_id(_build(_official_tree(), sessions, preview_limit=2))

    previews = nodes["p_official"]["previewSessions"]
    assert len(previews) == 2
    assert len({s["id"] for s in previews}) == 2
    # The newest anywhere in the subtree, not just the first ones encountered.
    assert [s["last_active"] for s in previews] == [9000, 8000]


def test_a_sticky_session_stays_in_its_own_project_not_its_parent():
    # The bug that started this: the agent cd'd into a SIBLING checkout, which
    # `nettacker` also claims. The stored project_id must still win.
    session = _session("/wnx/official/nettacker", project_id="p_osp")
    nodes = _by_id(_build(_official_tree(), [session]))

    assert nodes["p_osp"]["sessionCount"] == 1
    assert nodes["p_nett"]["sessionCount"] == 0


def test_deep_membership_matches_the_innermost_folder():
    # No stored project_id (a row predating sticky membership): the deepest
    # folder owns it, so a child project beats its ancestor.
    projects = [
        _project("p_outer", "Outer", ["/www"], path="outer"),
        _project("p_inner", "Inner", ["/www/app"], parent_id="p_outer", path="outer/inner", depth=1),
    ]
    nodes = _by_id(_build(projects, [_session("/www/app/src")]))

    assert nodes["p_inner"]["sessionCount"] == 1
    assert nodes["p_outer"]["sessionCount"] == 0
    assert nodes["p_outer"]["totalSessionCount"] == 1


def test_an_orphaned_child_still_appears():
    # Its parent is archived (filtered out before the build). The child must not
    # disappear with it — a project the user created is never silently dropped.
    projects = [
        _project("p_gone", "Gone", [], path="gone", archived=True),
        _project("p_kid", "Kid", ["/www/kid"], parent_id="p_gone", path="gone/kid", depth=1),
    ]
    tree = _build(projects, [_session("/www/kid")])

    assert [node["id"] for node in tree["projects"]] == ["p_kid"]
    assert tree["projects"][0]["totalSessionCount"] == 1


def test_a_parent_cycle_does_not_hang_the_build():
    # Only reachable through a corrupted DB, but a naive walk would recurse
    # until the sidebar refresh died.
    projects = [
        _project("p_a", "A", ["/www/a"], parent_id="p_b", path="a"),
        _project("p_b", "B", ["/www/b"], parent_id="p_a", path="b"),
    ]
    tree = _build(projects, [_session("/www/a"), _session("/www/b")])

    assert {node["id"] for node in tree["projects"]} == {"p_a", "p_b"}
    for node in tree["projects"]:
        assert node["totalSessionCount"] >= 1


def test_auto_projects_are_not_nested():
    # Tiers 2/3 invent projects from repo roots; they have no parent and no path.
    projects = [_project("p_official", "Official", ["/wnx/official"], path="official")]
    tree = _build(
        projects,
        [_session("/elsewhere/repo", git_repo_root="/elsewhere/repo")],
        auto_projects=True,
        resolve=lambda cwd: {"repo_root": "/elsewhere/repo", "worktree_root": "/elsewhere/repo"},
    )

    auto = next(node for node in tree["projects"] if node["isAuto"])
    assert auto["parentId"] is None
    assert auto["depth"] == 0
    assert auto["totalSessionCount"] == auto["sessionCount"] == 1
