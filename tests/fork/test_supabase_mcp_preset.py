"""Guard: the Supabase MCP preset stays wired and stays read-only.

Supabase is added as a PRESET pointing at Supabase's own MCP server rather than as a
hand-written tool: they maintain it, it tracks their API, and it covers projects,
migrations, SQL, edge functions, logs and advisors in one surface.

The read-only default is the part most worth pinning. It maps the connection to a
read-only Postgres role, so the agent can inspect schema and query data but cannot
DROP, DELETE or migrate. Dropping that flag silently would hand an agent
unrestricted DDL on a live database — the textbook hard-to-reverse action.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

from hermes_cli.mcp_config import _MCP_PRESETS, _apply_mcp_preset


def apply(preset: str) -> dict:
    """Resolve a preset the way `hermes mcp add --preset <name>` does."""
    config: dict = {}

    _apply_mcp_preset(
        "supabase",
        preset_name=preset,
        url=None,
        command=None,
        cmd_args=[],
        server_config=config,
    )

    return config


class TestSupabasePreset:
    def test_preset_exists(self):
        assert "supabase" in _MCP_PRESETS

    def test_resolves_to_supabases_own_server(self):
        config = apply("supabase")

        assert config["command"] == "npx"
        assert any("@supabase/mcp-server-supabase" in arg for arg in config["args"])

    def test_is_read_only_by_default(self):
        """The whole point: no DDL, no DELETE, no migrations without opting in."""
        config = apply("supabase")

        assert "--read-only" in config["args"], (
            "the Supabase preset lost --read-only, which gives the agent unrestricted "
            "DDL on a live database"
        )

    def test_uses_npx_dash_y_so_it_does_not_prompt(self):
        # Without -y, npx asks to install and the server never starts.
        config = apply("supabase")

        assert "-y" in config["args"]

    def test_pins_no_project_ref_by_default(self):
        """A project ref is per-user, so it must not be baked into the preset."""
        config = apply("supabase")

        assert not any("--project-ref" in arg for arg in config["args"])

    def test_explicit_transport_wins_over_the_preset(self):
        """A user passing --command must not be overridden by preset defaults."""
        config: dict = {}
        url, command, args, applied = _apply_mcp_preset(
            "supabase",
            preset_name="supabase",
            url=None,
            command="my-own-binary",
            cmd_args=["--flag"],
            server_config=config,
        )

        assert applied is False
        assert command == "my-own-binary"
        assert config == {}

    def test_codex_preset_still_works(self):
        config = apply("codex")

        assert config["command"] == "codex"


class TestPresetsAreDiscoverable:
    def _preset_action(self):
        """The argparse action behind `hermes mcp add --preset`."""
        import argparse

        from hermes_cli.subcommands.mcp import build_mcp_parser

        root = argparse.ArgumentParser(prog="hermes")
        build_mcp_parser(root.add_subparsers(dest="command"), cmd_mcp=lambda args: None)

        # mcp -> add -> --preset
        mcp = next(
            action for action in root._subparsers._group_actions if action.choices
        ).choices["mcp"]
        add = next(
            action for action in mcp._subparsers._group_actions if action.choices
        ).choices["add"]

        return next(action for action in add._actions if action.dest == "preset")

    def test_cli_lists_the_presets_as_choices(self):
        """Otherwise the flag is undiscoverable — you would have to read
        mcp_config.py to learn that presets exist at all."""
        action = self._preset_action()

        assert action.choices is not None, "--preset has no choices, so --help lists nothing"
        assert "supabase" in action.choices
        assert "codex" in action.choices

    def test_choices_track_the_preset_registry(self):
        """A new preset must show up without touching the CLI module."""
        action = self._preset_action()

        assert sorted(action.choices) == sorted(_MCP_PRESETS)
