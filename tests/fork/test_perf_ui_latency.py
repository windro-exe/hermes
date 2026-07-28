"""Guards for windro's fork perf patches — see fork/changelog/.

These exist because both patches are single lines inside large upstream files.
An upstream merge that rewrites the surrounding block can drop either one with
no error and no other test failing: the code still works, just slower. That is
exactly the failure mode the fork changelog warns about, so it gets a test.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import pytest


class TestSessionHistoryIsPooled:
    """session.history must run on the RPC thread pool, not the reader thread.

    Measured on a real 878-message state.db: 22ms in the DB plus 93ms in
    _history_to_messages = 116ms. Inline, that is 116ms of head-of-line
    blocking on the socket that also carries prompt.submit and
    session.interrupt.
    """

    def test_session_history_is_in_long_handlers(self):
        from tui_gateway import server

        assert "session.history" in server._LONG_HANDLERS, (
            "session.history was removed from _LONG_HANDLERS — it will run "
            "inline on the reader thread and block prompt.submit / "
            "session.interrupt for the duration of a full transcript rebuild. "
            "See fork/changelog/entries/ for the measurement."
        )

    def test_its_siblings_are_still_pooled(self):
        """If upstream ever un-pools these, our reasoning needs revisiting."""
        from tui_gateway import server

        for name in ("session.resume", "session.list", "session.compress"):
            assert name in server._LONG_HANDLERS, (
                f"{name} is no longer pooled upstream — the justification for "
                "pooling session.history was consistency with these siblings, "
                "so re-check that reasoning."
            )


class TestProjectTreeUsesCompactRows:
    """The project tree must not drag system_prompt blobs out of the DB.

    _project_tree_row never reads system_prompt, and compact_rows is the flag
    that omits it. Measured: 242KB carried for 5 rows on a real 13MB state.db,
    1.09ms -> 0.44ms.
    """

    def test_project_tree_inputs_requests_compact_rows(self, monkeypatch):
        from tui_gateway import server

        seen: dict[str, object] = {}

        class FakeDB:
            def list_sessions_rich(self, **kwargs):
                seen.update(kwargs)
                return []

        # Stop the real implementation after the query we care about: the git
        # warm-up and projects.db reads are irrelevant here and touch the disk.
        monkeypatch.setattr(
            server.git_probe, "warm_roots", lambda *a, **k: None, raising=False
        )

        class _Stop(RuntimeError):
            pass

        def _boom(*a, **k):
            raise _Stop

        import hermes_cli.projects_db as pdb

        monkeypatch.setattr(pdb, "connect_closing", _boom, raising=False)

        with pytest.raises(_Stop):
            server._project_tree_inputs(
                FakeDB(), session_limit=50, include_discovered=False
            )

        assert seen, "list_sessions_rich was never called — test needs updating"
        assert seen.get("compact_rows") is True, (
            "_project_tree_inputs stopped passing compact_rows=True, so every "
            "sidebar project-tree refresh now reads and discards every "
            "system_prompt blob. See fork/changelog/entries/."
        )

    def test_compact_rows_still_excludes_system_prompt(self):
        """Our patch is only useful while compact_rows means what we think."""
        from hermes_state import SessionDB

        assert "system_prompt" in SessionDB._SESSION_COMPACT_EXCLUDED, (
            "compact_rows no longer excludes system_prompt — the project-tree "
            "patch may now be pointless, or may need a different flag."
        )

    def test_project_tree_row_does_not_read_system_prompt(self):
        """If the row builder ever needs it, compact_rows would break it.

        Checks the function body only. The docstring legitimately names the
        column — it says the projection drops the heavy columns on purpose,
        which is why fetching them was waste in the first place.
        """
        import ast
        import inspect

        from tui_gateway import server

        fn = ast.parse(inspect.getsource(server._project_tree_row)).body[0]
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        code = "\n".join(ast.unparse(node) for node in body)

        assert "system_prompt" not in code, (
            "_project_tree_row now reads system_prompt, which compact_rows "
            "omits — the project-tree patch must be reverted or reworked."
        )
