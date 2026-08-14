"""Per-profile first-class Project store.

A **Project** is a human-named, multi-folder workspace. Unlike the desktop's
old inferred "workspaces" (derived from each session's ``cwd`` + a git probe)
and unlike kanban's self-generated worktrees, a Project is an explicit,
persisted entity the user creates and names. It anchors:

- **Desktop session grouping** — a session belongs to a project when its
  ``cwd`` lives under one of the project's folders (longest-prefix match).
- **Kanban task worktrees** — a task linked to a project creates its worktree
  under the project's primary repo with a deterministic branch name, instead
  of the random ``wt/<task-id>`` fallback.

Scope: **per-profile**, stored at ``$HERMES_HOME/projects.db`` (resolved via
``get_hermes_home()``), mirroring sessions / config / cron. This deliberately
differs from kanban, whose board DB is root-anchored and shared across
profiles. A Project may *bind* a kanban board (``board_slug``) so the two
systems agree on the repo + branch convention without merging their stores.

The schema is intentionally small and additive: column additions go through
:func:`_add_column_if_missing` so opening an old DB is always safe.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from hermes_cli.sqlite_util import add_column_if_missing as _add_column_if_missing, write_txn
from hermes_constants import get_hermes_home

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def projects_db_path() -> Path:
    """The per-profile projects DB path (``$HERMES_HOME/projects.db``).

    Profile-aware: ``get_hermes_home()`` already points at the active profile's
    home. Tests pass an explicit ``db_path`` to :func:`connect`.
    """
    return get_hermes_home() / "projects.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL,
    parent_id     TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    icon          TEXT,
    color         TEXT,
    board_slug    TEXT,
    primary_path  TEXT,
    created_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_folders (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    label       TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, path)
);

CREATE INDEX IF NOT EXISTS idx_project_folders_path
    ON project_folders(path);

CREATE TABLE IF NOT EXISTS project_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Git repos found by scanning the filesystem (desktop "repo-first" discovery).
-- Cached here so the overview is instant after the first scan instead of
-- re-walking the disk every time the Projects view opens.
CREATE TABLE IF NOT EXISTS discovered_repos (
    root          TEXT PRIMARY KEY,
    label         TEXT,
    last_seen     INTEGER NOT NULL
);
"""

# Indexes over columns that arrive by migration. Applied AFTER the migrations,
# never inside SCHEMA_SQL: on a legacy DB the `projects` table exists without
# `parent_id`, so `CREATE INDEX ... (parent_id)` would fail before the column
# was added and abort the whole schema script.
#
# FORK: a slug is unique WITHIN ITS PARENT, not globally, so `official/docs` and
# `personal/docs` can coexist. `IFNULL(parent_id, '')` folds the roots into one
# namespace — plain `(parent_id, slug)` would not, because SQL treats every NULL
# as distinct and two root projects could then share a slug.
POST_MIGRATION_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_slug_in_parent
    ON projects(IFNULL(parent_id, ''), slug);

CREATE INDEX IF NOT EXISTS idx_projects_parent
    ON projects(parent_id);
"""

# The path separator between project slugs: `official/os-projects/nettacker`.
PATH_SEP = "/"

# A hard ceiling on nesting depth. Depth is unlimited by design, but a cycle
# introduced by a corrupted DB (or a bug in `move_project`) would otherwise spin
# the recursive path query forever.
MAX_DEPTH = 64


# ---------------------------------------------------------------------------
# Slug + id helpers
# ---------------------------------------------------------------------------

# Lowercase alphanumerics, hyphens, underscores; 1-64 chars; no leading
# separator. Strict enough to stop traversal and path separators, loose enough
# for kebab-case names like ``hermes-agent``. Display formatting (spaces,
# emoji, capitalisation) lives in ``name``; the slug is just a stable handle.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def _slugify(name: str) -> str:
    """Derive a slug candidate from a human name (best-effort)."""
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-_")
    s = s[:64].strip("-_")
    return s or "project"


def normalize_slug(slug: Optional[str]) -> Optional[str]:
    """Lowercase + strip a slug; validate; return ``None`` for empty."""
    if slug is None:
        return None
    s = str(slug).strip().lower()
    if not s:
        return None
    if not _SLUG_RE.match(s):
        raise ValueError(
            f"invalid project slug {slug!r}: must be 1-64 chars, lowercase "
            f"alphanumerics / hyphens / underscores, not starting with "
            f"'-' or '_'"
        )
    return s


def _new_project_id() -> str:
    return "p_" + secrets.token_hex(4)


def _now() -> int:
    return int(time.time())


def _normalize_path(path: str) -> str:
    """Absolute, user-expanded, separator-normalized path (no trailing sep)."""
    p = os.path.abspath(os.path.expanduser(str(path).strip()))
    return p.rstrip("/\\") or p


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_INITIALIZED_PATHS: set[str] = set()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialize if needed) the per-profile projects DB.

    WAL with DELETE fallback for network filesystems (shared helper from
    ``hermes_state``). Schema init is idempotent (``CREATE TABLE IF NOT
    EXISTS`` + additive migrations) and cached per-path per-process.
    """
    path = db_path if db_path is not None else projects_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="projects.db")
        conn.execute("PRAGMA foreign_keys=ON")
        if resolved not in _INITIALIZED_PATHS:
            conn.executescript(SCHEMA_SQL)
            _migrate_add_optional_columns(conn)
            _migrate_nested_projects(conn)
            conn.executescript(POST_MIGRATION_SQL)
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Optional[Path] = None):
    """Open a projects DB connection and guarantee it is closed on exit.

    sqlite3's connection context manager only commits/rollbacks; it does NOT
    close the file descriptor. Long-lived processes (gateway, dashboard) route
    many project operations through ``connect()``; without closing, FDs to
    ``projects.db`` accumulate. Mirrors ``kanban_db.connect_closing``.
    """
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# TEXT columns added to `projects` after v1; re-applied idempotently on every
# open so a legacy DB upgrades in place.
_OPTIONAL_PROJECT_COLUMNS = ("board_slug", "primary_path", "icon", "color")


def _migrate_add_optional_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after v1 to legacy DBs (safe on every open)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    for col in _OPTIONAL_PROJECT_COLUMNS:
        if col not in cols:
            _add_column_if_missing(conn, "projects", col, f"{col} TEXT")


# Matches the v1 declaration `slug TEXT NOT NULL UNIQUE`, whose implicit
# sqlite_autoindex enforces GLOBAL slug uniqueness. That constraint cannot be
# dropped by ALTER TABLE, so nesting needs a full table rebuild.
_LEGACY_GLOBAL_SLUG_RE = re.compile(r"\bslug\b[^,]*\bUNIQUE\b", re.IGNORECASE)

_PROJECTS_TABLE_DDL = """
CREATE TABLE {table} (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL,
    parent_id     TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    icon          TEXT,
    color         TEXT,
    board_slug    TEXT,
    primary_path  TEXT,
    created_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
)
"""

_PROJECTS_COLUMNS = (
    "id",
    "slug",
    "name",
    "description",
    "icon",
    "color",
    "board_slug",
    "primary_path",
    "created_at",
    "archived",
)


def _migrate_nested_projects(conn: sqlite3.Connection) -> bool:
    """FORK: give a legacy `projects` table a parent and a per-parent slug.

    Two changes, one of which ALTER TABLE cannot express:

    1. ``parent_id`` — additive, so a plain ADD COLUMN. It self-references
       ``projects(id)`` with ON DELETE CASCADE, which SQLite accepts on an added
       column precisely because the implicit default is NULL.
    2. dropping the v1 ``slug ... UNIQUE`` constraint. Its enforcing index is an
       implicit ``sqlite_autoindex`` that no DDL statement can drop, so the only
       route is the documented rebuild: copy into a new table, swap, re-index.

    Returns whether the table was rebuilt. Idempotent: on an already-migrated DB
    (and on every fresh one) both branches no-op, so this is safe on every open.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if row is None:
        return False

    ddl = row["sql"] or ""
    if not _LEGACY_GLOBAL_SLUG_RE.search(ddl):
        # Modern shape. `parent_id` may still be missing if a previous open
        # crashed between the rebuild and the column add.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        if "parent_id" not in cols:
            _add_column_if_missing(
                conn,
                "projects",
                "parent_id",
                "parent_id TEXT REFERENCES projects(id) ON DELETE CASCADE",
            )
        return False

    # Foreign keys must be off across the swap: `project_folders` references
    # `projects(id)`, and dropping the old table with enforcement on would either
    # fail or cascade the folders away. PRAGMA foreign_keys is a no-op inside a
    # transaction, hence the toggle outside `write_txn`.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with write_txn(conn):
            columns = ", ".join(_PROJECTS_COLUMNS)
            conn.execute(_PROJECTS_TABLE_DDL.format(table="projects_migrating"))
            conn.execute(
                f"INSERT INTO projects_migrating ({columns}) "
                f"SELECT {columns} FROM projects"
            )
            conn.execute("DROP TABLE projects")
            conn.execute("ALTER TABLE projects_migrating RENAME TO projects")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    # A rename cannot rewrite `project_folders`' FK target (it still names
    # `projects`, which exists again), but a mid-rebuild crash could have left
    # folders pointing at ids that no longer exist. Report, never delete.
    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM project_folders pf "
        "WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = pf.project_id)"
    ).fetchone()
    if orphans and orphans["n"]:
        import logging

        logging.getLogger(__name__).warning(
            "projects.db: %d orphaned project_folders row(s) after nesting migration",
            orphans["n"],
        )
    return True


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProjectFolder:
    path: str
    label: Optional[str] = None
    is_primary: bool = False
    added_at: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "is_primary": bool(self.is_primary),
            "added_at": self.added_at,
        }


@dataclass
class Project:
    id: str
    slug: str
    name: str
    created_at: int
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    board_slug: Optional[str] = None
    primary_path: Optional[str] = None
    archived: bool = False
    # FORK: nesting. ``parent_id`` is the stored edge; ``path`` and ``depth`` are
    # DERIVED from the chain of slugs on every read, never stored, so a rename or
    # a move can never leave a stale copy behind.
    parent_id: Optional[str] = None
    path: str = ""
    depth: int = 0
    folders: List[ProjectFolder] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "board_slug": self.board_slug,
            "primary_path": self.primary_path,
            "archived": bool(self.archived),
            "created_at": self.created_at,
            "parent_id": self.parent_id,
            "path": self.path or self.slug,
            "depth": self.depth,
            "folders": [f.to_dict() for f in self.folders],
        }


def _project_from_row(row: sqlite3.Row) -> Project:
    keys = row.keys()
    return Project(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        created_at=row["created_at"],
        description=row["description"] if "description" in keys else None,
        icon=row["icon"] if "icon" in keys else None,
        color=row["color"] if "color" in keys else None,
        board_slug=row["board_slug"] if "board_slug" in keys else None,
        primary_path=row["primary_path"] if "primary_path" in keys else None,
        archived=bool(row["archived"]) if "archived" in keys else False,
        parent_id=(row["parent_id"] if "parent_id" in keys else None) or None,
    )


# ---------------------------------------------------------------------------
# Nesting: derived paths
# ---------------------------------------------------------------------------

# Walks parent -> child accumulating the slug chain. Roots seed the walk, so a
# row whose parent_id dangles (only reachable if the FK was bypassed) is simply
# absent from the result and falls back to its bare slug — a missing path is
# better than an infinite walk.
_PATHS_CTE = """
WITH RECURSIVE tree(id, path, depth) AS (
    SELECT id, slug, 0 FROM projects WHERE parent_id IS NULL
    UNION ALL
    SELECT p.id, t.path || ? || p.slug, t.depth + 1
      FROM projects p JOIN tree t ON p.parent_id = t.id
     WHERE t.depth < ?
)
SELECT id, path, depth FROM tree
"""


def _path_index(conn: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    """``id -> (full slug path, depth)`` for every project, in one query."""
    rows = conn.execute(_PATHS_CTE, (PATH_SEP, MAX_DEPTH)).fetchall()
    return {r["id"]: (r["path"], r["depth"]) for r in rows}


def _attach_path(conn: sqlite3.Connection, project: Project) -> Project:
    path, depth = _path_index(conn).get(project.id, (project.slug, 0))
    project.path = path
    project.depth = depth
    return project


def normalize_path_key(value: Optional[str]) -> str:
    """Canonicalize a project path (``Official/OS-Projects/`` -> ``official/os-projects``)."""
    parts = [
        seg.strip().lower()
        for seg in re.split(r"[/\\]+", str(value or "").strip())
        if seg.strip()
    ]
    return PATH_SEP.join(parts)


def project_path(conn: sqlite3.Connection, project_id: str) -> Optional[str]:
    """The full slug path of ``project_id`` (``official/os-projects``), or None."""
    hit = _path_index(conn).get(project_id)
    if hit is not None:
        return hit[0]
    row = conn.execute(
        "SELECT slug FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    return row["slug"] if row else None


def _load_folders(conn: sqlite3.Connection, project_id: str) -> List[ProjectFolder]:
    rows = conn.execute(
        "SELECT path, label, is_primary, added_at FROM project_folders "
        "WHERE project_id = ? ORDER BY is_primary DESC, added_at ASC",
        (project_id,),
    ).fetchall()
    return [
        ProjectFolder(
            path=r["path"],
            label=r["label"],
            is_primary=bool(r["is_primary"]),
            added_at=r["added_at"],
        )
        for r in rows
    ]


def _attach_folders(conn: sqlite3.Connection, project: Project) -> Project:
    project.folders = _load_folders(conn, project.id)
    return project


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def _unique_slug(
    conn: sqlite3.Connection,
    candidate: str,
    *,
    parent_id: Optional[str] = None,
    exclude_id: Optional[str] = None,
) -> str:
    """Return ``candidate`` or ``candidate-2``, ``-3`` ... if taken.

    FORK: scoped to ``parent_id``. Uniqueness is per-parent now, so two projects
    in different parents keep the slug the user asked for and only true siblings
    get a numeric suffix.
    """
    base = candidate
    n = 1
    slug = base
    sql = (
        "SELECT 1 FROM projects WHERE slug = ? "
        "AND IFNULL(parent_id, '') = IFNULL(?, '')"
    )
    params: tuple = (parent_id,)
    if exclude_id:
        sql += " AND id <> ?"
        params = (parent_id, exclude_id)
    while conn.execute(sql, (slug, *params)).fetchone() is not None:
        n += 1
        suffix = f"-{n}"
        slug = (base[: 64 - len(suffix)]).rstrip("-_") + suffix
    return slug


def _require_parent_id(conn: sqlite3.Connection, parent: Optional[str]) -> Optional[str]:
    """Resolve a parent id/slug/path to an id, or ``None`` for a root project."""
    ref = str(parent or "").strip()
    if not ref:
        return None
    row = conn.execute("SELECT id FROM projects WHERE id = ?", (ref,)).fetchone()
    if row is not None:
        return row["id"]
    resolved = get_project(conn, ref)
    if resolved is None:
        raise ValueError(f"no such parent project: {parent}")
    return resolved.id


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    slug: Optional[str] = None,
    folders: Optional[Iterable[str]] = None,
    primary_path: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    board_slug: Optional[str] = None,
    parent: Optional[str] = None,
) -> str:
    """Create a project and return its id.

    ``folders`` are normalized to absolute paths. If ``primary_path`` is given
    it is added to the folder set (if not already present) and marked primary;
    otherwise the first folder becomes primary.

    FORK: ``parent`` (an id, slug, or full path) nests the new project under an
    existing one, so its identity becomes a path of slugs —
    ``official/os-projects/nettacker``. Depth is unlimited.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("project name must not be empty")

    slug_candidate = normalize_slug(slug) if slug else _slugify(name)
    parent_id = _require_parent_id(conn, parent)
    if parent_id is not None:
        parent_depth = _path_index(conn).get(parent_id, ("", 0))[1]
        if parent_depth + 1 >= MAX_DEPTH:
            raise ValueError(f"project nesting deeper than {MAX_DEPTH} is not supported")
    pid = _new_project_id()
    now = _now()

    folder_paths: List[str] = []
    for f in folders or []:
        norm = _normalize_path(f)
        if norm and norm not in folder_paths:
            folder_paths.append(norm)

    primary = _normalize_path(primary_path) if primary_path else None
    if primary and primary not in folder_paths:
        folder_paths.insert(0, primary)
    if primary is None and folder_paths:
        primary = folder_paths[0]

    with write_txn(conn):
        unique = _unique_slug(conn, slug_candidate, parent_id=parent_id)
        conn.execute(
            "INSERT INTO projects "
            "(id, slug, parent_id, name, description, icon, color, board_slug, "
            " primary_path, created_at, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                pid,
                unique,
                parent_id,
                name,
                description,
                icon,
                color,
                normalize_slug(board_slug) if board_slug else None,
                primary,
                now,
            ),
        )
        for path in folder_paths:
            conn.execute(
                "INSERT INTO project_folders "
                "(project_id, path, label, is_primary, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, path, None, 1 if path == primary else 0, now),
            )
    return pid


def list_projects(
    conn: sqlite3.Connection, *, include_archived: bool = False
) -> List[Project]:
    """Every project, each carrying its derived ``path`` and ``depth``.

    Ordered by path so parents always precede their children — callers that
    build a tree (or print an indented list) can consume the rows in one pass.
    """
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql).fetchall()
    paths = _path_index(conn)
    projects = []
    for row in rows:
        project = _project_from_row(row)
        project.path, project.depth = paths.get(project.id, (project.slug, 0))
        projects.append(_attach_folders(conn, project))
    projects.sort(key=lambda p: p.path)
    return projects


def get_project(
    conn: sqlite3.Connection, id_or_slug: str
) -> Optional[Project]:
    """Look up a project by id, then by full path, then by bare slug.

    FORK: the path lookup. With per-parent slugs a bare slug can be ambiguous
    (``official/docs`` and ``personal/docs`` are both ``docs``), so the full path
    is the unambiguous handle. The bare-slug fallback is kept for existing
    callers and resolves only when exactly one project carries that slug.
    """
    ref = str(id_or_slug or "").strip()
    if not ref:
        return None

    row = conn.execute("SELECT * FROM projects WHERE id = ?", (ref,)).fetchone()
    if row is not None:
        return _attach_path(conn, _attach_folders(conn, _project_from_row(row)))

    wanted = normalize_path_key(ref)
    if not wanted:
        return None
    if PATH_SEP in wanted:
        for pid, (path, _depth) in _path_index(conn).items():
            if path == wanted:
                return get_project(conn, pid)
        return None

    matches = conn.execute(
        "SELECT * FROM projects WHERE slug = ?", (wanted,)
    ).fetchall()
    if len(matches) != 1:
        # Zero matches, or an ambiguous bare slug that the caller must qualify.
        return None
    return _attach_path(conn, _attach_folders(conn, _project_from_row(matches[0])))


# ---------------------------------------------------------------------------
# Nesting: navigation + moves
# ---------------------------------------------------------------------------


def list_children(
    conn: sqlite3.Connection, parent_id: Optional[str], *, include_archived: bool = False
) -> List[Project]:
    """Direct children of ``parent_id`` (or the root projects when ``None``)."""
    sql = "SELECT * FROM projects WHERE IFNULL(parent_id, '') = IFNULL(?, '')"
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql, (parent_id,)).fetchall()
    paths = _path_index(conn)
    out = []
    for row in rows:
        project = _project_from_row(row)
        project.path, project.depth = paths.get(project.id, (project.slug, 0))
        out.append(_attach_folders(conn, project))
    return out


def descendant_ids(conn: sqlite3.Connection, project_id: str) -> List[str]:
    """Every id beneath ``project_id``, deepest last. Excludes ``project_id``.

    Needed because a delete cascades in the DB but the *sessions* of the whole
    subtree live in another database, so the caller has to know the full set.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id, depth) AS (
            SELECT id, 0 FROM projects WHERE parent_id = ?
            UNION ALL
            SELECT p.id, s.depth + 1 FROM projects p JOIN sub s ON p.parent_id = s.id
             WHERE s.depth < ?
        )
        SELECT id FROM sub ORDER BY depth ASC
        """,
        (project_id, MAX_DEPTH),
    ).fetchall()
    return [r["id"] for r in rows]


def subtree_ids(conn: sqlite3.Connection, project_id: str) -> List[str]:
    """``project_id`` plus every descendant."""
    return [project_id, *descendant_ids(conn, project_id)]


def move_project(
    conn: sqlite3.Connection, project_id: str, new_parent: Optional[str]
) -> bool:
    """Re-parent a project (``None`` promotes it to a root).

    Rejects the two moves that would corrupt the tree: onto itself, or into its
    own subtree (which would detach that subtree from every root and make its
    path unresolvable).
    """
    project = get_project(conn, project_id)
    if project is None:
        raise ValueError(f"no such project: {project_id}")

    parent_id = _require_parent_id(conn, new_parent)
    if parent_id == project.id:
        raise ValueError("a project cannot be its own parent")
    if parent_id is not None and parent_id in set(descendant_ids(conn, project.id)):
        raise ValueError("cannot move a project into its own subtree")
    if (project.parent_id or None) == parent_id:
        return False

    if parent_id is not None:
        parent_depth = _path_index(conn).get(parent_id, ("", 0))[1]
        deepest = max(
            (_path_index(conn).get(cid, ("", 0))[1] for cid in subtree_ids(conn, project.id)),
            default=project.depth,
        )
        if parent_depth + 1 + (deepest - project.depth) >= MAX_DEPTH:
            raise ValueError(f"project nesting deeper than {MAX_DEPTH} is not supported")

    with write_txn(conn):
        slug = _unique_slug(
            conn, project.slug, parent_id=parent_id, exclude_id=project.id
        )
        conn.execute(
            "UPDATE projects SET parent_id = ?, slug = ? WHERE id = ?",
            (parent_id, slug, project.id),
        )
    return True


def update_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    board_slug: Optional[str] = None,
    slug: Optional[str] = None,
) -> bool:
    """Patch top-level project fields. Only provided fields change.

    ``icon``, ``color``, and ``board_slug`` accept an empty string to clear
    (store NULL) — passing ``None`` leaves the field untouched, so callers that
    want to clear must send ``""``.

    FORK: ``slug`` renames the project's path segment. It is de-duplicated
    against its siblings only, and every descendant path follows automatically
    because paths are derived rather than stored. Re-parenting is a separate
    operation (:func:`move_project`).
    """
    sets: List[str] = []
    params: List[object] = []
    if slug is not None:
        candidate = normalize_slug(slug)
        if candidate is None:
            raise ValueError("project slug must not be empty")
        row = conn.execute(
            "SELECT parent_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return False
        sets.append("slug = ?")
        params.append(
            _unique_slug(
                conn,
                candidate,
                parent_id=row["parent_id"] or None,
                exclude_id=project_id,
            )
        )
    if name is not None:
        n = str(name).strip()
        if not n:
            raise ValueError("project name must not be empty")
        sets.append("name = ?")
        params.append(n)
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if icon is not None:
        sets.append("icon = ?")
        params.append(icon or None)
    if color is not None:
        sets.append("color = ?")
        params.append(color or None)
    if board_slug is not None:
        sets.append("board_slug = ?")
        params.append(normalize_slug(board_slug) if board_slug.strip() else None)
    if not sets:
        return False
    params.append(project_id)
    with write_txn(conn):
        cur = conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
        )
    return cur.rowcount > 0


def add_folder(
    conn: sqlite3.Connection,
    project_id: str,
    path: str,
    *,
    label: Optional[str] = None,
    is_primary: bool = False,
) -> str:
    """Add a folder to a project. Returns the normalized path.

    When ``is_primary`` is set, the folder becomes the project's primary repo
    (the previous primary is demoted, and ``projects.primary_path`` updates).
    """
    norm = _normalize_path(path)
    if not norm:
        raise ValueError("folder path must not be empty")
    if get_project(conn, project_id) is None:
        raise ValueError(f"no such project: {project_id}")
    now = _now()
    with write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO project_folders "
            "(project_id, path, label, is_primary, added_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (project_id, norm, label, now),
        )
        if label is not None:
            conn.execute(
                "UPDATE project_folders SET label = ? "
                "WHERE project_id = ? AND path = ?",
                (label, project_id, norm),
            )
        if is_primary:
            _set_primary_locked(conn, project_id, norm)
        else:
            # First folder of an empty project becomes primary implicitly.
            existing_primary = conn.execute(
                "SELECT 1 FROM project_folders "
                "WHERE project_id = ? AND is_primary = 1",
                (project_id,),
            ).fetchone()
            if existing_primary is None:
                _set_primary_locked(conn, project_id, norm)
    return norm


def remove_folder(conn: sqlite3.Connection, project_id: str, path: str) -> bool:
    """Remove a folder from a project. Repoints primary if it was primary."""
    norm = _normalize_path(path)
    with write_txn(conn):
        was_primary = conn.execute(
            "SELECT is_primary FROM project_folders "
            "WHERE project_id = ? AND path = ?",
            (project_id, norm),
        ).fetchone()
        cur = conn.execute(
            "DELETE FROM project_folders WHERE project_id = ? AND path = ?",
            (project_id, norm),
        )
        if was_primary is not None and was_primary["is_primary"]:
            nxt = conn.execute(
                "SELECT path FROM project_folders WHERE project_id = ? "
                "ORDER BY added_at ASC LIMIT 1",
                (project_id,),
            ).fetchone()
            new_primary = nxt["path"] if nxt else None
            if new_primary:
                _set_primary_locked(conn, project_id, new_primary)
            else:
                conn.execute(
                    "UPDATE projects SET primary_path = NULL WHERE id = ?",
                    (project_id,),
                )
    return cur.rowcount > 0


def _set_primary_locked(
    conn: sqlite3.Connection, project_id: str, path: str
) -> None:
    """Set the primary folder (caller already holds a write txn)."""
    conn.execute(
        "UPDATE project_folders SET is_primary = 0 WHERE project_id = ?",
        (project_id,),
    )
    conn.execute(
        "UPDATE project_folders SET is_primary = 1 "
        "WHERE project_id = ? AND path = ?",
        (project_id, path),
    )
    conn.execute(
        "UPDATE projects SET primary_path = ? WHERE id = ?",
        (path, project_id),
    )


def set_primary(conn: sqlite3.Connection, project_id: str, path: str) -> bool:
    norm = _normalize_path(path)
    with write_txn(conn):
        exists = conn.execute(
            "SELECT 1 FROM project_folders WHERE project_id = ? AND path = ?",
            (project_id, norm),
        ).fetchone()
        if exists is None:
            return False
        _set_primary_locked(conn, project_id, norm)
    return True


def _set_archived_subtree(conn: sqlite3.Connection, project_id: str, value: int) -> bool:
    """Flip ``archived`` on a project AND its whole subtree.

    FORK: archiving has to cascade. A hidden parent with visible children would
    strand those children — their path still resolves through a project the tree
    no longer lists, so they would surface as bogus roots.
    """
    ids = subtree_ids(conn, project_id)
    placeholders = ", ".join("?" for _ in ids)
    with write_txn(conn):
        cur = conn.execute(
            f"UPDATE projects SET archived = ? WHERE id IN ({placeholders})",
            (value, *ids),
        )
    return cur.rowcount > 0


def archive_project(conn: sqlite3.Connection, project_id: str) -> bool:
    return _set_archived_subtree(conn, project_id, 1)


def restore_project(conn: sqlite3.Connection, project_id: str) -> bool:
    return _set_archived_subtree(conn, project_id, 0)


def delete_project(conn: sqlite3.Connection, project_id: str) -> bool:
    """Hard-delete a project, its folders, and its whole subtree.

    FORK: sub-projects go with their parent. The cascade is the DB's own —
    ``projects.parent_id`` is a self-referencing FK with ON DELETE CASCADE, and
    :func:`connect` enables ``PRAGMA foreign_keys``. Sessions live in a different
    database, so a caller that also wants those gone must ask
    :func:`subtree_ids` for the full set BEFORE deleting.
    """
    with write_txn(conn):
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Active-project pointer (project_meta KV)
# ---------------------------------------------------------------------------


_ACTIVE_META_KEY = "active_id"
_DISCOVERY_POLICY_META_KEY = "repo_discovery_policy"


def set_active(conn: sqlite3.Connection, project_id: Optional[str]) -> None:
    """Set (or clear, when ``None``) the active project pointer."""
    with write_txn(conn):
        if project_id is None:
            conn.execute("DELETE FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,))
        else:
            conn.execute(
                "INSERT INTO project_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_ACTIVE_META_KEY, project_id),
            )


def get_active_id(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,)
    ).fetchone()
    return row["value"] if row else None


def get_discovery_policy_key(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM project_meta WHERE key = ?", (_DISCOVERY_POLICY_META_KEY,)
    ).fetchone()
    return row["value"] if row else None


def reconcile_discovered_repos_policy(
    conn: sqlite3.Connection,
    policy_key: str,
    *,
    preserve_unversioned: bool = False,
) -> bool:
    """Clear cached scan rows when their discovery policy changes.

    Existing pre-policy rows are retained only for the backward-compatible
    default policy. Returns whether rows were cleared.
    """
    current = get_discovery_policy_key(conn)
    if current == policy_key:
        return False

    cleared = current is not None or not preserve_unversioned
    with write_txn(conn):
        if cleared:
            conn.execute("DELETE FROM discovered_repos")
        conn.execute(
            "INSERT INTO project_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_DISCOVERY_POLICY_META_KEY, policy_key),
        )
    return cleared


def clear_discovered_repos(
    conn: sqlite3.Connection, *, policy_key: Optional[str] = None
) -> None:
    with write_txn(conn):
        conn.execute("DELETE FROM discovered_repos")
        if policy_key is not None:
            conn.execute(
                "INSERT INTO project_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_DISCOVERY_POLICY_META_KEY, policy_key),
            )


# ---------------------------------------------------------------------------
# Discovered repos (filesystem scan cache)
# ---------------------------------------------------------------------------


def record_discovered_repos(
    conn: sqlite3.Connection,
    repos: Iterable[tuple[str, Optional[str]]],
    *,
    replace: bool = False,
    policy_key: Optional[str] = None,
) -> int:
    """Persist scanned git repo roots into the cache.

    ``repos`` is an iterable of ``(root, label)``. Roots are normalized; the
    label falls back to the basename. Returns the number of rows written.

    When ``replace`` is true, this is the authoritative result of a fresh disk
    scan: delete stale rows first so old eval/worktree noise disappears instead
    of living forever in the cache.
    """
    now = _now()
    rows = []
    for root, label in repos:
        norm = _normalize_path(root)
        if not norm:
            continue
        rows.append((norm, (label or os.path.basename(norm) or norm), now))

    with write_txn(conn):
        if replace:
            conn.execute("DELETE FROM discovered_repos")
        if rows:
            conn.executemany(
                "INSERT INTO discovered_repos (root, label, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(root) DO UPDATE SET label = excluded.label, "
                "last_seen = excluded.last_seen",
                rows,
            )
        if policy_key is not None:
            conn.execute(
                "INSERT INTO project_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_DISCOVERY_POLICY_META_KEY, policy_key),
            )
    return len(rows)


def list_discovered_repos(conn: sqlite3.Connection) -> List[dict]:
    """All cached discovered repo roots, most-recently-seen first."""
    rows = conn.execute(
        "SELECT root, label, last_seen FROM discovered_repos ORDER BY last_seen DESC"
    ).fetchall()
    return [
        {"root": r["root"], "label": r["label"], "last_seen": r["last_seen"]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resolution + naming
# ---------------------------------------------------------------------------


def project_for_path(
    conn: sqlite3.Connection, path: str, *, include_archived: bool = False
) -> Optional[Project]:
    """Return the project owning ``path`` (longest-prefix folder match).

    A folder owns ``path`` when ``path`` equals the folder or is nested under
    it. The most specific (longest) folder wins, so nested projects resolve to
    the innermost one.
    """
    if not str(path or "").strip():
        return None
    target = _normalize_path(path)
    sql = (
        "SELECT pf.project_id AS pid, pf.path AS folder "
        "FROM project_folders pf JOIN projects p ON p.id = pf.project_id"
    )
    if not include_archived:
        sql += " WHERE p.archived = 0"
    best_pid: Optional[str] = None
    best_len = -1
    for row in conn.execute(sql).fetchall():
        folder = row["folder"]
        if target == folder or target.startswith(folder.rstrip("/\\") + os.sep) or \
                target.startswith(folder.rstrip("/\\") + "/"):
            if len(folder) > best_len:
                best_len = len(folder)
                best_pid = row["pid"]
    if best_pid is None:
        return None
    return get_project(conn, best_pid)


# Deterministic branch slug: lowercase, separators collapsed, capped.
_BRANCH_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def branch_name_for(project: Project, task_id: str, *, title: str = "") -> str:
    """Deterministic branch name for a project-linked kanban task.

    Shape: ``<project-path>/<task-id>`` (optionally ``-<title-slug>``). Stable
    and human-meaningful, replacing the random ``wt/<task-id>`` fallback.

    FORK: a nested project contributes its whole path, so the branch reads
    ``official/os-projects/nettacker/t_ab12``. Root projects are unchanged
    (``path`` equals ``slug``), so existing branches keep their names.
    """
    slug = project.path or project.slug or _slugify(project.name)
    base = f"{slug}/{task_id}"
    if title:
        tslug = _BRANCH_SAFE_RE.sub("-", str(title).strip().lower()).strip("-")
        tslug = tslug[:40].strip("-")
        if tslug:
            base = f"{base}-{tslug}"
    return base
