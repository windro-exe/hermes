"""The default session/browser cwd must be the home dir, not the process cwd.

FORK regression test. `terminal.cwd: .` is a placeholder, not a directory, and
three implementations of that rule existed: the canonical
`gateway/cwd_placeholder.py` (resolves to `messaging or home_fallback`) plus
inlined copies in `tui_gateway/server.py` and `hermes_cli/web_server.py` that had
drifted to returning the PROCESS working directory.

Symptom: a session with no project inherited whatever directory the backend was
started in. The desktop composer's git widget then showed an unrelated
repository's branch and uncommitted diff stats, and the file browser opened there.
Observed live — a session displayed the branch of a checkout it had no
relationship to.
"""

from __future__ import annotations

import os
from pathlib import Path


class TestDefaultSessionCwd:
    def test_falls_back_to_home_not_process_cwd(self, monkeypatch, tmp_path):
        """The bug: an arbitrary process cwd became the session's workspace."""
        import tui_gateway.server as server

        # No configured cwd and no bridged env var -> the fallback branch.
        monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
        monkeypatch.delenv("TERMINAL_CWD", raising=False)

        # Simulate a backend launched somewhere unrelated to the user's work.
        elsewhere = tmp_path / "somewhere-unrelated"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        resolved = server._default_session_cwd()

        assert resolved == str(Path.home())
        assert resolved != os.getcwd()

    def test_explicit_configured_cwd_still_wins(self, monkeypatch, tmp_path):
        import tui_gateway.server as server

        configured = tmp_path / "real-workspace"
        configured.mkdir()
        monkeypatch.setattr(server, "_launch_configured_cwd", lambda: str(configured))

        assert server._default_session_cwd() == str(configured)

    def test_bridged_env_var_still_wins_over_home(self, monkeypatch, tmp_path):
        import tui_gateway.server as server

        bridged = tmp_path / "bridged"
        bridged.mkdir()
        monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
        monkeypatch.setenv("TERMINAL_CWD", str(bridged))

        assert server._default_session_cwd() == str(bridged)

    def test_placeholder_set_is_shared_not_reimplemented(self):
        """Three copies drifted once; keep them as one object."""
        from gateway.cwd_placeholder import CWD_PLACEHOLDERS

        import tui_gateway.server as server

        assert server._CWD_PLACEHOLDERS is CWD_PLACEHOLDERS

    def test_a_placeholder_is_not_treated_as_a_workspace(self):
        """`terminal.cwd: .` must resolve to None, not to '.'."""
        import tui_gateway.server as server

        for placeholder in (".", "auto", "cwd", "  .  "):
            assert server._configured_cwd_from_cfg({"terminal": {"cwd": placeholder}}) is None


class TestFsDefaultCwd:
    def test_file_browser_falls_back_to_home(self, monkeypatch, tmp_path):
        import hermes_cli.web_server as web_server

        monkeypatch.setattr(web_server, "load_config", lambda: {"terminal": {"cwd": "."}})
        monkeypatch.delenv("TERMINAL_CWD", raising=False)

        elsewhere = tmp_path / "unrelated"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        resolved = web_server._fs_default_cwd()

        assert resolved == str(Path.home())
        assert resolved != os.getcwd()

    def test_explicit_dir_still_honoured(self, monkeypatch, tmp_path):
        import hermes_cli.web_server as web_server

        real = tmp_path / "workspace"
        real.mkdir()
        monkeypatch.setattr(web_server, "load_config", lambda: {"terminal": {"cwd": str(real)}})

        assert web_server._fs_default_cwd() == str(real)

    def test_nonexistent_configured_dir_degrades_to_home(self, monkeypatch, tmp_path):
        """A stale config path must not be returned just because it is absolute."""
        import hermes_cli.web_server as web_server

        missing = tmp_path / "deleted-long-ago"
        monkeypatch.setattr(web_server, "load_config", lambda: {"terminal": {"cwd": str(missing)}})
        monkeypatch.delenv("TERMINAL_CWD", raising=False)

        assert web_server._fs_default_cwd() == str(Path.home())

    def test_both_implementations_agree(self, monkeypatch, tmp_path):
        """The drift itself is the bug; assert the two cannot diverge again."""
        import hermes_cli.web_server as web_server
        import tui_gateway.server as server

        monkeypatch.setattr(server, "_launch_configured_cwd", lambda: None)
        monkeypatch.setattr(web_server, "load_config", lambda: {"terminal": {"cwd": "."}})
        monkeypatch.delenv("TERMINAL_CWD", raising=False)

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert server._default_session_cwd() == web_server._fs_default_cwd() == str(Path.home())
