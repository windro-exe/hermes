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

import subprocess

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
