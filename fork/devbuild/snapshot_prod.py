#!/usr/bin/env python3
"""Refresh the ``dev`` profile from production, without touching production.

Fork-owned. Creates or refreshes an isolated HERMES_HOME that holds a COPY of
the real profile's data, so the dev branch's code can be exercised against real
sessions, skills, config and keys while production stays untouched.

Why this exists
---------------
Code isolation and data isolation are different axes:

* the ``dev`` git branch isolates CODE,
* ``HERMES_HOME`` isolates DATA.

``hermes profile create --clone-all`` is the closest built-in, but it
deliberately excludes ``state.db`` and ``sessions`` (see
``_CLONE_ALL_HISTORY_EXCLUDE_ROOT`` in ``hermes_cli/profiles.py``) because a new
profile is normally meant to be a fresh workspace. Here the session history is
exactly what we want, so this script clones the profile and then copies the
history in on top.

The safety property that matters: if dev code runs a schema migration, it
migrates the COPY. Production history cannot be migrated, corrupted or deleted
by anything running against this profile.

SQLite is copied with the online backup API, never with a file copy
--------------------------------------------------------------------
The production gateway and desktop app are usually RUNNING while this script is
used (that is the point — you snapshot mid-work). ``state.db`` is in WAL mode, so
a plain ``shutil.copy2`` of the ``.db`` file races the writer: recent commits
live in ``-wal`` and a byte copy can capture a torn page, producing a snapshot
that opens fine and is subtly missing or corrupting recent rows. ``sqlite3``'s
``Connection.backup()`` takes a consistent snapshot of a live database, which is
what this uses. The source is opened read-only via a URI so this script cannot
write to production even by accident.

Usage
-----
    .venv/Scripts/python.exe fork/devbuild/snapshot_prod.py            # refresh
    .venv/Scripts/python.exe fork/devbuild/snapshot_prod.py --dry-run  # show plan
    .venv/Scripts/python.exe fork/devbuild/snapshot_prod.py --data-only

Then run the dev build against it with ``fork/devbuild/dev.sh`` (or by exporting
``HERMES_HOME`` to the path this prints).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Root-level entries copied wholesale (config, credentials, agent assets).
# Deliberately NOT copied: hermes-agent (the git checkout + ~3 GB venv),
# profiles (sibling profiles), bin, node_modules, backups, state-snapshots,
# logs, caches, and every runtime lock/pid file — see _SKIP_ROOT.
_SKIP_ROOT: frozenset[str] = frozenset(
    {
        # Code + heavy installs. The dev build runs from the repo checkout, so a
        # copy of the managed install would be dead weight and confusing.
        "hermes-agent",
        ".worktrees",
        "node_modules",
        "bin",
        # Sibling profiles: copying these recursively is never intended and
        # would nest a copy of the dev profile inside itself on refresh.
        "profiles",
        # Snapshot/restore artifacts. Carrying these in means "restore backup"
        # inside the dev profile would resurrect PRODUCTION state, which defeats
        # the isolation this script exists to provide.
        "backups",
        "state-snapshots",
        # Regenerable or host-specific.
        "logs",
        "cache",
        "bootstrap-cache",
        "audio_cache",
        # Runtime state that must never be inherited: a copied pid/lock file
        # makes the dev instance believe a gateway is already running (or, worse,
        # lets it think it owns production's lock).
        "gateway.pid",
        "gateway.lock",
        "gateway_state.json",
        "auth.lock",
        "gateway-starts.log",
        "desktop-build-stamp.json",
        # The updater binary; the dev build must never self-update.
        "hermes-setup.exe",
        "hermes-setup.exe.old-selflocking",
    }
)

# Copied with sqlite3's backup API rather than as files. EVERY live SQLite db in
# the profile root belongs here, not just state.db — each has the same WAL
# tearing risk while the gateway/desktop is running. Found by listing `*.db` in
# a real HERMES_HOME rather than by assuming state.db was the only one.
_SQLITE_DBS: tuple[str, ...] = (
    "state.db",
    "kanban.db",
    "projects.db",
    "verification_evidence.db",
)

# WAL sidecars: never copied. They belong to the source database's write
# transaction; the backup API folds their contents into the destination .db, and
# a stale sidecar next to a fresh copy is a corruption risk. Derived from
# _SQLITE_DBS so adding a db above cannot leave its sidecars behind.
_WAL_SIDECARS: frozenset[str] = frozenset(
    f"{name}{suffix}" for name in _SQLITE_DBS for suffix in ("-wal", "-shm", "-journal")
)


def _is_skippable_extra(name: str) -> bool:
    """True for files that are pointless or harmful to carry into the snapshot.

    * ``*.bak`` / ``.pre-update-emergency-*`` — the updater's own safety copies of
      ``state.db``. On this machine there are five, together several times the
      size of the live db, and they are snapshots of PRODUCTION state. Copying
      them means "restore" inside the dev profile would resurrect prod data,
      which is the one thing this isolation exists to prevent.
    * ``*.lock`` — held by whichever process owns the resource. A copied lock
      makes the dev instance reason about production's locks (``kanban.db``'s
      dispatch/init locks are the live example).
    """
    lowered = name.lower()
    if lowered.endswith((".lock", ".pid", ".sock", ".tmp")):
        return True
    return ".bak" in lowered or ".pre-update-emergency-" in lowered


def _prod_home() -> Path:
    """Production HERMES_HOME, ignoring any dev override in the environment."""
    return Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes")) if os.name == "nt" else Path.home() / ".hermes"


def _dev_home(prod: Path) -> Path:
    return prod / "profiles" / "dev"


def _copy_sqlite(src: Path, dst: Path, *, dry_run: bool) -> str:
    """Copy a live SQLite db consistently, or report why it was skipped."""
    if not src.exists():
        return f"skip {src.name} (not present)"
    size_mb = src.stat().st_size / (1024 * 1024)
    if dry_run:
        return f"backup {src.name} ({size_mb:.1f} MB) -> {dst}"

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Remove any previous copy AND its sidecars, so a refresh cannot leave a new
    # .db beside an old -wal.
    for leftover in (dst, dst.with_name(dst.name + "-wal"), dst.with_name(dst.name + "-shm")):
        leftover.unlink(missing_ok=True)

    # Read-only URI: this script cannot write to production even on a bug.
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return f"backup {src.name} ({size_mb:.1f} MB) -> ok"


def _copy_tree_entry(src: Path, dst: Path, *, dry_run: bool) -> str:
    if dry_run:
        kind = "dir " if src.is_dir() else "file"
        return f"copy {kind} {src.name}"
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(
            src,
            dst,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.sock", "*.tmp"),
            dirs_exist_ok=True,
        )
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"copy {src.name} -> ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the plan without copying anything")
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="refresh only state.db and sessions/ (leave dev config/skills edits alone)",
    )
    parser.add_argument("--name", default="dev", help="profile name to write into (default: dev)")
    args = parser.parse_args()

    prod = _prod_home()
    if not prod.is_dir():
        print(f"✗ production HERMES_HOME not found at {prod}", file=sys.stderr)
        return 1

    dev = prod / "profiles" / args.name
    if dev.resolve() == prod.resolve():
        print("✗ refusing to snapshot a profile onto itself", file=sys.stderr)
        return 1

    print(f"prod : {prod}")
    print(f"dev  : {dev}")
    print(f"mode : {'dry-run' if args.dry_run else 'refresh'}{' (data only)' if args.data_only else ''}")
    print()

    started = time.time()
    if not args.dry_run:
        dev.mkdir(parents=True, exist_ok=True)

    actions: list[str] = []

    if not args.data_only:
        for entry in sorted(prod.iterdir(), key=lambda p: p.name.lower()):
            if (
                entry.name in _SKIP_ROOT
                or entry.name in _WAL_SIDECARS
                or entry.name in _SQLITE_DBS
                or _is_skippable_extra(entry.name)
            ):
                continue
            actions.append(_copy_tree_entry(entry, dev / entry.name, dry_run=args.dry_run))
    else:
        sessions = prod / "sessions"
        if sessions.is_dir():
            actions.append(_copy_tree_entry(sessions, dev / "sessions", dry_run=args.dry_run))

    for db_name in _SQLITE_DBS:
        actions.append(_copy_sqlite(prod / db_name, dev / db_name, dry_run=args.dry_run))

    # A copied gateway pid/lock would make the dev instance misreport a running
    # gateway. Belt and braces: _SKIP_ROOT already excludes them, but a refresh
    # over an older snapshot may still have them on disk.
    if not args.dry_run:
        for stale in ("gateway.pid", "gateway.lock", "auth.lock", "gateway_state.json"):
            (dev / stale).unlink(missing_ok=True)

    for line in actions:
        print(f"  {line}")

    print()
    if args.dry_run:
        print("dry-run: nothing was written")
        return 0

    print(f"✓ snapshot refreshed in {time.time() - started:.1f}s")
    print()
    print("Run the dev build against it:")
    print("  bash fork/devbuild/dev.sh            # CLI")
    print("  bash fork/devbuild/dev.sh desktop    # desktop app")
    print()
    print(f"Or set it yourself:  export HERMES_HOME='{dev}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
