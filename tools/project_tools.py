#!/usr/bin/env python3
"""Project tools — the agent's INTENTIONAL handle on first-class Projects.

Projects (per-profile ``projects.db``) are the named workspaces the desktop
sidebar groups sessions into. Creating / switching a project is a deliberate act
expressed as explicit tools — never a side effect of a terminal ``cd``.

Exposed only on GUI sessions: the tools live in the `project` toolset (kept off
``_HERMES_CORE_TOOLS``) which the desktop/TUI gateway folds into its resolved
toolsets, so no CLI/messaging/cron schema carries them. The GUI also wires
``set_project_workspace_callback`` so a create/switch re-anchors the live
session's cwd and the sidebar follows the move; the DB write is the durable part.
"""

import json
import os
from typing import Callable, Optional

from tools.registry import registry

# Set by the GUI gateway (tui_gateway) at session wiring. Receives
# ``(task_id, primary_path, project_name)`` and re-anchors that session's
# workspace + refreshes the sidebar. ``None`` in CLI / messaging contexts — the
# DB write still happens; there's just no live GUI session to move.
_workspace_callback: Optional[Callable[[str, str, str], None]] = None


def set_project_workspace_callback(fn: Optional[Callable[[str, str, str], None]]) -> None:
    global _workspace_callback
    _workspace_callback = fn


def _primary_path(proj) -> Optional[str]:
    if getattr(proj, "primary_path", None):
        return proj.primary_path
    for folder in proj.folders:
        if folder.is_primary:
            return folder.path
    return proj.folders[0].path if proj.folders else None


def _apply_workspace(task_id: Optional[str], path: Optional[str], name: str) -> None:
    cb = _workspace_callback
    if cb and task_id and path:
        try:
            cb(task_id, path, name)
        except Exception:
            pass


def _resolve(conn, token: str):
    from hermes_cli import projects_db as pdb

    token = (token or "").strip()
    if not token:
        return None
    projects = pdb.list_projects(conn, include_archived=True)
    # Exact id / slug / path / name first, then case-insensitive.
    for proj in projects:
        if token in (proj.id, proj.slug, proj.path) or proj.name == token:
            return proj
    low = token.lower()
    # FORK: a nested project is addressed by its PATH (`official/os-projects`).
    # A bare slug or name can now legitimately be ambiguous across parents, so a
    # non-unique match resolves to nothing rather than silently picking the first.
    wanted_path = pdb.normalize_path_key(token)
    if wanted_path:
        hits = [p for p in projects if p.path == wanted_path]
        if len(hits) == 1:
            return hits[0]
    for field in ("slug", "name"):
        hits = [p for p in projects if str(getattr(p, field)).lower() == low]
        if len(hits) == 1:
            return hits[0]
    return None


def project_list(task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        active = pdb.get_active_id(conn)
        projects = pdb.list_projects(conn)

    return json.dumps({
        "active_id": active,
        "projects": [
            {
                "id": p.id,
                "slug": p.slug,
                "path": p.path,
                "parent_id": p.parent_id,
                "name": p.name,
                "primary_path": _primary_path(p),
                "active": p.id == active,
            }
            for p in projects
        ],
    })


def project_create(
    name: str,
    path: Optional[str] = None,
    parent: Optional[str] = None,
    task_id: Optional[str] = None,
) -> str:
    name = (name or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"})

    from hermes_cli import projects_db as pdb

    folder = (path or "").strip()
    if folder:
        folder = os.path.abspath(os.path.expanduser(folder))

    try:
        with pdb.connect_closing() as conn:
            pid = pdb.create_project(
                conn,
                name=name,
                folders=[folder] if folder else [],
                primary_path=folder or None,
                parent=(parent or "").strip() or None,
            )
            pdb.set_active(conn, pid)
            proj = pdb.get_project(conn, pid)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})

    if proj is None:
        return json.dumps({"success": False, "error": "project vanished after create"})

    primary = _primary_path(proj)
    _apply_workspace(task_id, primary, proj.name)

    return json.dumps({
        "success": True,
        "id": proj.id,
        "slug": proj.slug,
        "path": proj.path,
        "parent_id": proj.parent_id,
        "name": proj.name,
        "primary_path": primary,
    })


def project_switch(project: str, task_id: Optional[str] = None) -> str:
    from hermes_cli import projects_db as pdb

    with pdb.connect_closing() as conn:
        proj = _resolve(conn, project)
        if proj is None:
            return json.dumps({"success": False, "error": f"no project matching '{project}'"})
        pdb.set_active(conn, proj.id)

    primary = _primary_path(proj)
    _apply_workspace(task_id, primary, proj.name)

    return json.dumps({
        "success": True,
        "id": proj.id,
        "slug": proj.slug,
        "path": proj.path,
        "parent_id": proj.parent_id,
        "name": proj.name,
        "primary_path": primary,
    })


registry.register(
    name="project_list",
    toolset="project",
    schema={
        "name": "project_list",
        "description": (
            "List the desktop Projects (named workspaces) and which one is active. "
            "Projects nest, so each one carries a `path` of slugs like "
            "'official/os-projects/nettacker' — that path is how you address it."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: project_list(task_id=kw.get("task_id")),
)

registry.register(
    name="project_create",
    toolset="project",
    schema={
        "name": "project_create",
        "description": (
            "Create a desktop Project (a named workspace) and switch this chat into it. "
            "Pass `path` to anchor it to a repo/folder — this chat's workspace moves there "
            "and the sidebar follows. Pass `parent` to nest it under an existing project. "
            "Use when starting work in a new repo/folder; this is "
            "the intentional way to move the session, not `cd`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human name, e.g. 'Aurora Demo'"},
                "path": {"type": "string", "description": "Primary repo/folder to anchor the project to"},
                "parent": {
                    "type": "string",
                    "description": (
                        "Optional parent project to nest under — its id or its slug path, "
                        "e.g. 'official/os-projects'. Omit for a top-level project. The "
                        "project path is a namespace, not a filesystem path."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    handler=lambda args, **kw: project_create(
        name=args.get("name", ""),
        path=args.get("path"),
        parent=args.get("parent"),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="project_switch",
    toolset="project",
    schema={
        "name": "project_switch",
        "description": (
            "Switch this chat into an existing desktop Project (by slug path, name, or id). "
            "Moves the session's workspace to the project's primary folder and the sidebar "
            "follows. The intentional way to move between projects, not `cd`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Project id, slug path ('official/os-projects'), or name. Use the "
                        "full path when a bare name or slug exists under more than one parent."
                    ),
                },
            },
            "required": ["project"],
        },
    },
    handler=lambda args, **kw: project_switch(project=args.get("project", ""), task_id=kw.get("task_id")),
)
