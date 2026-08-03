"""Guards for the project-rules inspector in the context breakdown.

The point of this surface: a token count answers "how much", never "did my rule
land". Those are different questions, and only the second one comes up when a
rule appears to be ignored — which is what happened to windro. Four different
causes look identical from outside, so the breakdown has to distinguish them:

  * the rule is switched off        -> state "off"
  * the rule is path-scoped         -> state "scoped" (parsed, not honoured yet)
  * a different folder was resolved -> reported via "cwd"/"dir"
  * the session's prompt predates the file -> "stale"

Fork-owned file; upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from agent.context_breakdown import project_rules_detail
from agent.prompt_builder import project_files_fingerprint
from agent.runtime_cwd import clear_session_cwd, set_session_cwd


@pytest.fixture
def project(tmp_path):
    """A git-rooted project with a rules dir, pinned as the session cwd."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, check=False)
    (tmp_path / ".hermes" / "rules").mkdir(parents=True)
    set_session_cwd(str(tmp_path))
    try:
        yield tmp_path
    finally:
        clear_session_cwd()


def rule(project, name: str, body: str) -> None:
    (project / ".hermes" / "rules" / name).write_text(body, encoding="utf-8")


class TestRuleStates:
    """Each state has a different fix, so they must not be conflated."""

    def test_a_plain_rule_is_live(self, project):
        rule(project, "rules.md", "- you are ochumaa\n")

        detail = project_rules_detail(None)

        assert detail["rules"] == [{"state": "live", "text": "you are ochumaa"}]

    def test_every_bullet_becomes_its_own_row(self, project):
        rule(project, "rules.md", "- first\n- second\n- third\n")

        detail = project_rules_detail(None)

        assert [r["text"] for r in detail["rules"]] == ["first", "second", "third"]

    def test_mode_manual_reads_as_off_not_scoped(self, project):
        """Switched off by the UI toggle — fixable by toggling it back on."""
        rule(project, "rules.md", "---\nmode: manual\n---\n- disabled\n")

        detail = project_rules_detail(None)

        assert detail["rules"] == [{"state": "off", "text": "disabled"}]

    def test_path_scoped_reads_as_scoped_not_off(self, project):
        """Not a toggle — an unimplemented feature. Saying "off" would send the
        user hunting for a switch that would not help."""
        rule(project, "rules.md", '---\npaths: ["**/*.test.ts"]\n---\n- add a test\n')

        detail = project_rules_detail(None)

        assert detail["rules"] == [{"state": "scoped", "text": "add a test"}]

    def test_mixed_states_are_all_reported(self, project):
        rule(project, "a-live.md", "- live one\n")
        rule(project, "b-off.md", "---\nmode: manual\n---\n- off one\n")
        rule(project, "c-scoped.md", '---\nglobs: "src/**"\n---\n- scoped one\n')

        states = {r["text"]: r["state"] for r in project_rules_detail(None)["rules"]}

        assert states == {"live one": "live", "off one": "off", "scoped one": "scoped"}

    def test_rules_are_one_flat_list_across_files(self, project):
        """windro asked for one list; the UI does not group by file."""
        rule(project, "a.md", "- from a\n")
        rule(project, "b.md", "- from b\n")

        detail = project_rules_detail(None)

        assert [r["text"] for r in detail["rules"]] == ["from a", "from b"]


class TestStaleness:
    """The signal that explains "I saved a rule and nothing changed"."""

    def test_not_stale_when_the_prompt_matches_disk(self, project):
        rule(project, "rules.md", "- one\n")
        fingerprint = project_files_fingerprint(str(project))

        assert project_rules_detail(fingerprint)["stale"] is False

    def test_stale_after_a_rule_is_edited(self, project):
        rule(project, "rules.md", "- one\n")
        fingerprint = project_files_fingerprint(str(project))
        time.sleep(0.01)
        rule(project, "rules.md", "- one\n- two\n")

        detail = project_rules_detail(fingerprint)

        assert detail["stale"] is True, (
            "an edited rule did not report as stale — this is exactly the case "
            "that made windro think rules were broken"
        )

    def test_stale_after_a_rule_file_is_added(self, project):
        rule(project, "rules.md", "- one\n")
        fingerprint = project_files_fingerprint(str(project))
        time.sleep(0.01)
        rule(project, "extra.md", "- two\n")

        assert project_rules_detail(fingerprint)["stale"] is True

    def test_never_stale_without_a_reference_fingerprint(self, project):
        """No prompt built yet means nothing to be out of date with."""
        rule(project, "rules.md", "- one\n")

        assert project_rules_detail(None)["stale"] is False


class TestProjectShape:
    def test_reports_the_resolved_cwd_and_dir(self, project):
        rule(project, "rules.md", "- one\n")

        detail = project_rules_detail(None)

        assert detail["cwd"] == str(project)
        assert detail["dir"] is not None
        assert ".hermes" in detail["dir"]

    def test_dir_is_none_when_the_project_has_no_rules_dir(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True, check=False)
        set_session_cwd(str(tmp_path))
        try:
            detail = project_rules_detail(None)
        finally:
            clear_session_cwd()

        assert detail["dir"] is None
        assert detail["rules"] == []

    def test_reports_whether_idea_md_exists(self, project):
        assert project_rules_detail(None)["idea"] is False

        (project / "IDEA.md").write_text("an expense tracker\n", encoding="utf-8")

        assert project_rules_detail(None)["idea"] is True

    def test_survives_a_project_with_no_cwd(self):
        clear_session_cwd()
        detail = project_rules_detail(None)

        # Whatever the ambient cwd resolves to, the call must not raise and must
        # return the full shape.
        assert set(detail) == {"cwd", "dir", "idea", "rules", "stale"}


class TestInspectionDoesNotConsumeTheSignal:
    """Opening the panel must not mark the rules fresh.

    compute_session_context_breakdown calls build_system_prompt_parts to measure
    the prompt, and that call RECORDS a new fingerprint. Left alone, merely
    looking at the breakdown would clear the staleness flag and suppress the
    rebuild the next turn was going to do — inspection with a side effect, and
    the side effect is "your rule silently stops arriving".
    """

    def test_the_breakdown_snapshots_and_restores_the_fingerprint(self):
        import inspect

        from agent.context_breakdown import compute_session_context_breakdown

        src = inspect.getsource(compute_session_context_breakdown)
        snapshot = src.find("prompt_files_fingerprint = getattr(")
        build = src.find("build_system_prompt_parts(agent)")
        restore = src.find("agent._project_files_fingerprint = prompt_files_fingerprint")

        assert snapshot != -1, "the breakdown no longer snapshots the fingerprint"
        assert build != -1
        assert restore != -1, (
            "the breakdown does not restore the fingerprint — opening the context "
            "panel now silently marks rules as fresh and suppresses the rebuild"
        )
        assert snapshot < build, "snapshot must happen BEFORE the rebuild"
        assert build < restore, "restore must happen AFTER the rebuild"

    def test_the_breakdown_reports_rules_detail(self):
        import inspect

        from agent.context_breakdown import compute_session_context_breakdown

        src = inspect.getsource(compute_session_context_breakdown)

        assert '"rules_detail": project_rules_detail(prompt_files_fingerprint)' in src, (
            "the breakdown stopped returning rules_detail, so the UI has nothing "
            "to show"
        )
