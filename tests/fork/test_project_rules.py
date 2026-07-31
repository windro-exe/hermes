"""Guards for the per-project rules + IDEA.md loader (windro's fork).

See fork/changelog/entries/ for the design and the research behind it. The short
version of why these tests are worth having: across every tool that ships this
feature, the most-reported bug is a rule that is present on disk and silently
never injected. A rule that stops loading breaks nothing and fails no other
test — the agent just quietly ignores you. So the loading behaviour itself gets
asserted.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from agent.prompt_builder import (
    _load_project_files,
    _parse_rule_frontmatter,
    _rule_is_always_on,
    build_context_files_prompt,
)


@pytest.fixture
def project(tmp_path):
    """A git-rooted project dir with a .hermes/rules directory."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    (tmp_path / ".hermes" / "rules").mkdir(parents=True)
    return tmp_path


def rule(project, name: str, body: str) -> None:
    (project / ".hermes" / "rules" / name).write_text(body, encoding="utf-8")


def load(project) -> str:
    return _load_project_files(project)


class TestAlwaysOnRules:
    def test_plain_rule_file_loads(self, project):
        rule(project, "style.md", "- use pnpm, never npm\n")

        assert "use pnpm, never npm" in load(project)

    def test_no_frontmatter_means_always_on(self, project):
        """Matches Cline and Claude Code. Copilot's inverted default (no
        selector => never loaded) is a documented source of confusion."""
        rule(project, "a.md", "- rule with no header\n")

        assert "rule with no header" in load(project)

    def test_description_only_is_still_always_on(self, project):
        """A description with no path scoping must not silently disable a rule."""
        rule(project, "a.md", "---\ndescription: things that bite\n---\n- gotcha here\n")

        assert "gotcha here" in load(project)

    def test_multiple_files_are_sorted_deterministically(self, project):
        """Load order is load-bearing: this text sits in the cached prompt
        prefix, so a filesystem-dependent order would miss the cache."""
        rule(project, "zebra.md", "- last alphabetically\n")
        rule(project, "alpha.md", "- first alphabetically\n")

        out = load(project)

        assert out.index("first alphabetically") < out.index("last alphabetically")

    def test_each_file_is_labelled_with_its_name(self, project):
        """So `/context` and the prompt itself show WHICH rules loaded."""
        rule(project, "style.md", "- x\n")

        assert "style.md" in load(project)

    def test_empty_rules_dir_produces_nothing(self, project):
        assert load(project) == ""

    def test_project_with_no_hermes_dir_produces_nothing(self, tmp_path):
        # NOTE: never `git init` a parent of tmp_path here. Doing so plants a git
        # root at the pytest session root, which gives every later test in the
        # run a git root and silently enables the parent-directory walk. That
        # cost a real debugging detour once already.
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)

        assert _load_project_files(tmp_path) == ""

    def test_blank_rule_file_is_skipped(self, project):
        rule(project, "empty.md", "\n\n")

        assert load(project) == ""

    def test_non_markdown_files_ignored(self, project):
        (project / ".hermes" / "rules" / "notes.txt").write_text("- nope\n", encoding="utf-8")

        assert load(project) == ""


class TestScopedRulesAreSkippedNotSilently:
    """Path-scoped rules can't work before the agent has read anything, so they
    are skipped — but visibly. Injecting a rule its author scoped to test files
    into every prompt would be worse than not loading it."""

    def test_glob_mode_rule_is_not_injected(self, project):
        rule(project, "t.md", '---\nmode: glob\npaths: ["**/*.test.ts"]\n---\n- scoped body\n')

        assert "scoped body" not in load(project)

    def test_paths_without_mode_is_treated_as_scoped(self, project):
        rule(project, "t.md", '---\npaths: ["src/**"]\n---\n- scoped body\n')

        assert "scoped body" not in load(project)

    def test_skipping_is_reported_not_silent(self, project):
        """The whole point: a rule that doesn't load must say so."""
        rule(project, "t.md", '---\nmode: glob\npaths: ["**/*.ts"]\n---\n- scoped\n')
        rule(project, "always.md", "- loaded\n")

        out = load(project)

        assert "not active yet" in out, (
            "a skipped rule produced no note — this is exactly the silent "
            "failure mode that generated the most bug reports in other tools"
        )

    def test_always_apply_true_overrides_path_scoping(self, project):
        """Cursor's documented precedence: alwaysApply wins, globs ignored."""
        rule(project, "t.md", '---\nalwaysApply: true\nglobs: "src/**"\n---\n- forced in\n')

        assert "forced in" in load(project)

    @pytest.mark.parametrize("mode", ["always", "always_on", "alwaysApply"])
    def test_explicit_always_modes_load(self, project, mode):
        rule(project, "t.md", f"---\nmode: {mode}\n---\n- body here\n")

        assert "body here" in load(project)


class TestMalformedFrontmatterFailsOpen:
    """Cline's documented behaviour, and the right call: a rule with a broken
    header stays active rather than vanishing without an error."""

    def test_invalid_yaml_still_loads_the_rule(self, project):
        rule(project, "bad.md", "---\nmode: [unclosed\n  bad: : :\n---\n- still applies\n")

        assert "still applies" in load(project)

    def test_unterminated_frontmatter_still_loads(self, project):
        rule(project, "bad.md", "---\nmode: glob\n- no closing fence\n")

        assert "no closing fence" in load(project)

    def test_parser_returns_empty_dict_on_garbage(self):
        assert _parse_rule_frontmatter("---\n[[[\n---\nbody") == {}
        assert _parse_rule_frontmatter("no frontmatter") == {}
        assert _rule_is_always_on({}) is True


class TestIdeaMd:
    """IDEA.md was written by the desktop dialog and read by nothing — for an
    empty project it is the only context that exists."""

    def test_idea_md_loads(self, project):
        (project / "IDEA.md").write_text("A local expense tracker.\n", encoding="utf-8")

        assert "A local expense tracker." in load(project)

    def test_lowercase_variant_loads(self, project):
        (project / "idea.md").write_text("lowercase intent\n", encoding="utf-8")

        assert "lowercase intent" in load(project)

    def test_blank_idea_md_adds_nothing(self, project):
        (project / "IDEA.md").write_text("\n", encoding="utf-8")

        assert load(project) == ""

    def test_frontmatter_is_stripped_from_idea(self, project):
        (project / "IDEA.md").write_text("---\ntitle: x\n---\nthe actual intent\n", encoding="utf-8")

        out = load(project)

        assert "the actual intent" in out
        assert "title: x" not in out


class TestDoesNotDisturbTheExistingChain:
    """The first-match-wins chain (HERMES.md > AGENTS.md > CLAUDE.md >
    .cursorrules) is deliberate override precedence. Rules load ALONGSIDE it as
    a separate section, so both must appear."""

    def test_agents_md_and_rules_both_load(self, project):
        (project / "AGENTS.md").write_text("upstream contributor guide\n", encoding="utf-8")
        rule(project, "style.md", "- fork rule\n")

        out = build_context_files_prompt(
            cwd=str(project), skip_soul=True, allow_install_tree_fallback=True
        )

        assert "upstream contributor guide" in out, "the existing chain stopped loading"
        assert "fork rule" in out, "project rules did not load alongside it"

    def test_hermes_md_still_wins_over_agents_md(self, project):
        """Rules must not change the chain's internal precedence."""
        (project / "HERMES.md").write_text("hermes wins\n", encoding="utf-8")
        (project / "AGENTS.md").write_text("agents loses\n", encoding="utf-8")

        out = build_context_files_prompt(
            cwd=str(project), skip_soul=True, allow_install_tree_fallback=True
        )

        assert "hermes wins" in out
        assert "agents loses" not in out


class TestSafety:
    def test_no_git_root_does_not_walk_parents(self, tmp_path):
        """Mirrors _find_hermes_md's guard: without a git root, only cwd is
        checked, so a stray .hermes/rules in /tmp or $HOME can't be picked up."""
        (tmp_path / ".hermes" / "rules").mkdir(parents=True)
        (tmp_path / ".hermes" / "rules" / "r.md").write_text("- parent rule\n", encoding="utf-8")
        child = tmp_path / "child"
        child.mkdir()

        assert "parent rule" not in _load_project_files(child)

    def test_install_tree_guard_blocks_project_files(self, monkeypatch, project):
        """The guard that stops the gateway injecting this repo's own contributor
        files must cover rules too."""
        rule(project, "style.md", "- should not load\n")
        monkeypatch.setattr("agent.runtime_cwd._is_install_tree", lambda p: True)

        out = build_context_files_prompt(
            cwd=None, skip_soul=True, allow_install_tree_fallback=False
        )

        assert "should not load" not in out

    def test_rule_content_goes_through_the_injection_scanner(self, project):
        """Rule files land verbatim in the system prompt, so they are an
        injection surface like every other context file."""
        import agent.prompt_builder as pb

        seen: list[str] = []
        original = pb._scan_context_content

        def spy(content, label):
            seen.append(label)
            return original(content, label)

        pb._scan_context_content = spy
        try:
            rule(project, "style.md", "- x\n")
            load(project)
        finally:
            pb._scan_context_content = original

        assert any("style.md" in label for label in seen), (
            "rule files bypassed _scan_context_content — that is a "
            "prompt-injection hole the other context files are protected from"
        )

    def test_file_count_is_capped(self, project):
        from agent.prompt_builder import _PROJECT_RULES_MAX_FILES

        for i in range(_PROJECT_RULES_MAX_FILES + 10):
            rule(project, f"r{i:03d}.md", f"- rule number {i}\n")

        out = load(project)

        assert f"rule number {_PROJECT_RULES_MAX_FILES + 5}" not in out, (
            "the rules directory is not capped — a runaway directory would "
            "balloon every prompt in the project"
        )


class TestRulesTakeEffectMidSession:
    """A rule saved while a session is open must reach the agent.

    The system prompt is built once per session and cached to keep the provider's
    prefix cache warm (build_system_prompt's docstring says so explicitly). That
    meant a rule written mid-session was silently ignored until a new session —
    which is indistinguishable from the feature being broken, and is exactly what
    windro hit: the agent knew IDEA.md (present at build time) but never saw the
    rule he added afterwards.
    """

    def _fingerprint(self, project):
        from agent.prompt_builder import project_files_fingerprint

        return project_files_fingerprint(str(project))

    def test_fingerprint_changes_when_a_rule_is_added(self, project):
        before = self._fingerprint(project)
        rule(project, "a.md", "- new rule\n")

        assert self._fingerprint(project) != before

    def test_fingerprint_changes_when_a_rule_is_edited(self, project):
        rule(project, "a.md", "- one\n")
        before = self._fingerprint(project)
        time.sleep(0.01)
        rule(project, "a.md", "- one\n- two\n")

        assert self._fingerprint(project) != before, (
            "an edited rule file produced the same fingerprint, so the cached "
            "system prompt would never rebuild and the edit would be ignored"
        )

    def test_fingerprint_changes_when_idea_md_changes(self, project):
        before = self._fingerprint(project)
        (project / "IDEA.md").write_text("intent\n", encoding="utf-8")

        assert self._fingerprint(project) != before

    def test_fingerprint_is_stable_when_nothing_changes(self, project):
        """Otherwise every turn would rebuild and destroy the prefix cache."""
        rule(project, "a.md", "- one\n")

        assert self._fingerprint(project) == self._fingerprint(project)

    def test_fingerprint_empty_without_a_cwd(self):
        from agent.prompt_builder import project_files_fingerprint

        assert project_files_fingerprint(None) == ""

    def _agent(self, project):
        from agent.prompt_builder import project_files_fingerprint

        class StubAgent:
            _cached_system_prompt = "BUILT EARLIER"
            _cached_system_prompt_static = "S"
            _memory_store = None

        agent = StubAgent()
        agent._project_files_fingerprint = project_files_fingerprint(str(project))
        return agent

    def test_edit_invalidates_the_cached_prompt(self, project, monkeypatch):
        from agent.system_prompt import refresh_project_files_if_changed

        rule(project, "a.md", "- one\n")
        agent = self._agent(project)
        monkeypatch.setattr("agent.runtime_cwd.resolve_context_cwd", lambda: project)

        time.sleep(0.01)
        rule(project, "a.md", "- one\n- two\n")

        assert refresh_project_files_if_changed(agent) is True
        assert agent._cached_system_prompt is None, (
            "the cached prompt survived a rule edit, so the agent keeps running "
            "on the old rules for the rest of the session"
        )

    def test_no_change_keeps_the_cache(self, project, monkeypatch):
        """The prefix cache is expensive — never invalidate without cause."""
        from agent.system_prompt import refresh_project_files_if_changed

        rule(project, "a.md", "- one\n")
        agent = self._agent(project)
        monkeypatch.setattr("agent.runtime_cwd.resolve_context_cwd", lambda: project)

        assert refresh_project_files_if_changed(agent) is False
        assert agent._cached_system_prompt == "BUILT EARLIER"

    def test_a_never_built_agent_is_left_alone(self, project, monkeypatch):
        from agent.system_prompt import refresh_project_files_if_changed

        class Fresh:
            _cached_system_prompt = None
            _cached_system_prompt_static = None
            _memory_store = None

        monkeypatch.setattr("agent.runtime_cwd.resolve_context_cwd", lambda: project)

        assert refresh_project_files_if_changed(Fresh()) is False

    def test_the_turn_path_calls_the_refresh(self):
        """Without this wiring the fingerprint is computed and never used."""
        import inspect

        from agent import turn_context

        src = inspect.getsource(turn_context)

        assert "refresh_project_files_if_changed" in src, (
            "agent/turn_context.py no longer refreshes project files before the "
            "cached-prompt check, so mid-session rule edits are ignored again."
        )


class TestProjectRuleTool:
    """The agent must be able to read and write rules itself.

    Asked to "add a rule" it previously wrote a MEMORY entry, because memory was
    the only write-a-fact affordance it had. Asked what its rules were it
    refused, because they arrive inside the system prompt. Both are fixed by
    giving it a tool that touches the files directly.
    """

    def _run(self, project, monkeypatch, **kwargs):
        from tools.project_rule_tool import project_rule_tool

        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: project)
        return json.loads(project_rule_tool(**kwargs))

    def test_add_creates_the_rules_file(self, tmp_path, monkeypatch):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)

        result = self._run(tmp_path, monkeypatch, action="add", rule="always run the tests")

        assert result["success"] is True
        assert (tmp_path / ".hermes" / "rules" / "rules.md").read_text(encoding="utf-8") == (
            "- always run the tests\n"
        )

    def test_added_rule_reaches_the_prompt(self, project, monkeypatch):
        self._run(project, monkeypatch, action="add", rule="you are ochumaa")

        assert "you are ochumaa" in load(project), (
            "a rule the agent added itself did not load into the prompt"
        )

    def test_list_reads_from_disk(self, project, monkeypatch):
        rule(project, "rules.md", "- one\n- two\n")

        result = self._run(project, monkeypatch, action="list")

        assert result["files"][0]["rules"] == ["one", "two"]
        assert result["files"][0]["active"] is True

    def test_list_marks_scoped_files_inactive(self, project, monkeypatch):
        """So the agent never claims a rule is in force when it isn't."""
        rule(project, "s.md", '---\nmode: glob\npaths: ["src/**"]\n---\n- scoped\n')

        result = self._run(project, monkeypatch, action="list")

        assert result["files"][0]["active"] is False

    def test_list_on_a_project_with_no_rules(self, tmp_path, monkeypatch):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)

        result = self._run(tmp_path, monkeypatch, action="list")

        assert result["success"] is True
        assert result["files"] == []

    def test_remove_by_index(self, project, monkeypatch):
        rule(project, "rules.md", "- one\n- two\n")

        result = self._run(project, monkeypatch, action="remove", index=0)

        assert result["rules"] == ["two"]

    def test_duplicate_add_is_a_no_op(self, project, monkeypatch):
        self._run(project, monkeypatch, action="add", rule="same")
        result = self._run(project, monkeypatch, action="add", rule="same")

        assert result.get("unchanged") is True

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"action": "nope"},
            {"action": "add", "rule": "   "},
            {"action": "remove", "index": 99},
            {"action": "add", "rule": "x", "file": "../escape.md"},
            {"action": "add", "rule": "x", "file": "sub/dir.md"},
        ],
    )
    def test_bad_input_is_rejected(self, project, monkeypatch, kwargs):
        rule(project, "rules.md", "- one\n")

        assert "error" in self._run(project, monkeypatch, **kwargs)

    def test_registered_alongside_memory(self):
        """It only gets chosen over `memory` if the model can see it."""
        from toolsets import _HERMES_CORE_TOOLS

        assert "project_rule" in _HERMES_CORE_TOOLS
        assert "memory" in _HERMES_CORE_TOOLS

    @pytest.mark.parametrize("toolset", ["coding", "hermes-acp", "hermes-api-server"])
    def test_present_in_every_surface_that_has_memory(self, toolset):
        """The list that matters is the resolved toolset, not _HERMES_CORE_TOOLS.

        This test exists because the first version of this guard only checked
        _HERMES_CORE_TOOLS and passed while the tool was completely invisible in
        the desktop app. The desktop and `hermes --tui` collapse to the `coding`
        posture toolset (tui_gateway/server.py::_load_enabled_toolsets), which is
        built from its own explicit list — so a tool can be in the core list and
        still never reach the model.

        Anywhere the agent can reach for `memory`, it must also be able to reach
        for `project_rule`, or "add a rule" resolves to a private memory entry.
        """
        from toolsets import resolve_toolset

        tools = resolve_toolset(toolset)

        assert "memory" in tools, f"{toolset} no longer has memory — update this guard"
        assert "project_rule" in tools, (
            f"project_rule is missing from the '{toolset}' toolset, so the agent "
            "cannot see it there and will write rules into memory instead. "
            "See fork/changelog/entries/."
        )

    def test_no_surface_offers_memory_without_project_rule(self):
        """Catch the next surface someone adds, not just today's three."""
        from toolsets import TOOLSETS, resolve_toolset

        gaps = []
        for name in TOOLSETS:
            try:
                tools = resolve_toolset(name)
            except Exception:
                continue
            if "memory" in tools and "project_rule" not in tools:
                gaps.append(name)

        # The single-purpose `memory` toolset is deliberately exempt.
        assert gaps == ["memory"], (
            f"these toolsets offer memory but not project_rule: {gaps}. "
            "The agent will store project rules as private memory there."
        )

    def test_description_steers_away_from_memory(self):
        """The wording is the whole mechanism — assert it stays."""
        from tools.project_rule_tool import PROJECT_RULE_SCHEMA

        description = PROJECT_RULE_SCHEMA["description"].lower()

        assert "memory" in description, "the description must contrast with memory"
        assert "add a rule" in description


class TestPromptLicensesDiscussion:
    """The agent refused to say what its rules were. They are windro's own files;
    refusing is both unhelpful and wrong."""

    def test_header_says_the_rules_are_not_confidential(self, project):
        rule(project, "a.md", "- one\n")

        out = load(project)

        assert "not confidential" in out
        assert "project_rule" in out, "the header must point at the tool"
