#!/usr/bin/env python3
"""Read and edit this project's standing rules — ``.hermes/rules/*.md``.

Why this exists as a tool rather than leaving it to ``write_file``:

1. **The agent reached for memory instead.** Asked to "add a rule", it wrote a
   memory entry, because memory was the only write-a-fact affordance it had.
   Memory is per-agent and invisible in the repo; project rules are versioned
   files that also steer the CLI and TUI. This tool makes the right target
   discoverable, and its description says so explicitly.

2. **Reading rules should not look like disclosing the system prompt.** Asked
   what its rules were, the agent refused — the rules arrive inside the system
   prompt, and models are trained to be cagey about that. ``list`` reads the
   files from disk instead, which is an ordinary project-file read and answers
   the question honestly.

Every write goes through the same discovery the prompt loader uses
(``_find_project_rules_dir``), so the tool and the loader can never disagree
about which directory is in play. An edit takes effect on the next turn:
``refresh_project_files_if_changed`` notices the mtime change and rebuilds the
cached system prompt.
"""

import json
from pathlib import Path

from tools.registry import registry, tool_error

_DEFAULT_FILE = "rules.md"
_MAX_RULE_CHARS = 2000


def _resolve_rules_dir(create: bool) -> tuple[Path | None, str]:
    """Locate (optionally creating) this project's rules directory."""
    from agent.prompt_builder import _PROJECT_RULES_DIR, _find_project_rules_dir
    from agent.runtime_cwd import resolve_agent_cwd

    existing = _find_project_rules_dir(resolve_agent_cwd())
    if existing is not None:
        return existing, ""

    if not create:
        return None, "no rules yet"

    target = resolve_agent_cwd() / _PROJECT_RULES_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, f"could not create {target}: {exc}"

    return target, ""


def _rule_files(rules_dir: Path) -> list[Path]:
    try:
        return sorted(
            (p for p in rules_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"),
            key=lambda p: p.name.lower(),
        )
    except Exception:
        return []


def _read_rules(path: Path) -> tuple[str, list[str]]:
    """Return (frontmatter, rules) for one file."""
    from agent.prompt_builder import _strip_yaml_frontmatter

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return "", []

    body = _strip_yaml_frontmatter(raw)
    frontmatter = raw[: len(raw) - len(body)] if body and raw.endswith(body) else ""
    rules = [
        line.strip().lstrip("-*").strip()
        for line in body.split("\n")
        if line.strip()
    ]

    return frontmatter, [r for r in rules if r]


def _write_rules(path: Path, frontmatter: str, rules: list[str]) -> None:
    header = frontmatter if not frontmatter or frontmatter.endswith("\n") else f"{frontmatter}\n"
    body = "\n".join(f"- {r}" for r in rules if r.strip())
    path.write_text(f"{header}{body}\n", encoding="utf-8")


def _is_active(frontmatter: str) -> bool:
    from agent.prompt_builder import _parse_rule_frontmatter, _rule_is_always_on

    return _rule_is_always_on(_parse_rule_frontmatter(frontmatter))


def project_rule_tool(action: str, rule: str = "", file: str = "", index: int = -1) -> str:
    act = (action or "").strip().lower()

    if act not in {"list", "add", "remove"}:
        return tool_error("action must be one of: list, add, remove.")

    rules_dir, note = _resolve_rules_dir(create=act == "add")

    if rules_dir is None:
        if act == "list":
            return json.dumps(
                {
                    "success": True,
                    "rules_dir": None,
                    "files": [],
                    "note": (
                        "This project has no .hermes/rules yet. Adding a rule "
                        "creates it."
                    ),
                },
                ensure_ascii=False,
            )
        return tool_error(f"Could not open the rules directory: {note}")

    if act == "list":
        files = []
        for path in _rule_files(rules_dir):
            frontmatter, rules = _read_rules(path)
            files.append(
                {
                    "file": path.name,
                    "active": _is_active(frontmatter),
                    "rules": rules,
                }
            )
        return json.dumps(
            {
                "success": True,
                "rules_dir": str(rules_dir),
                "files": files,
                "note": (
                    "'active' false means the file is scoped or disabled, so it is "
                    "not in the system prompt."
                ),
            },
            ensure_ascii=False,
        )

    target_name = (file or "").strip() or _DEFAULT_FILE
    if not target_name.lower().endswith(".md"):
        target_name += ".md"
    if "/" in target_name or "\\" in target_name or target_name.startswith("."):
        return tool_error("file must be a plain .md filename inside the rules directory.")

    target = rules_dir / target_name
    frontmatter, rules = _read_rules(target)

    if act == "add":
        text = " ".join((rule or "").split())
        if not text:
            return tool_error("rule text is required to add a rule.")
        if len(text) > _MAX_RULE_CHARS:
            return tool_error(f"rule is too long ({len(text)} chars, max {_MAX_RULE_CHARS}).")
        if text in rules:
            return json.dumps(
                {"success": True, "unchanged": True, "file": target_name,
                 "note": "That rule is already present."},
                ensure_ascii=False,
            )
        rules.append(text)
    else:  # remove
        if index < 0 or index >= len(rules):
            return tool_error(
                f"index out of range: {target_name} has {len(rules)} rule(s). "
                "Call action='list' first."
            )
        rules.pop(index)

    try:
        _write_rules(target, frontmatter, rules)
    except Exception as exc:
        return tool_error(f"Could not write {target}: {exc}")

    return json.dumps(
        {
            "success": True,
            "file": target_name,
            "path": str(target),
            "rules": rules,
            "note": (
                "Saved. This takes effect on your next turn — the system prompt "
                "rebuilds when these files change."
            ),
        },
        ensure_ascii=False,
    )


PROJECT_RULE_SCHEMA = {
    "name": "project_rule",
    "description": (
        "Read and edit this project's standing rules, stored as markdown in "
        ".hermes/rules/. Use this — NOT the memory tool — whenever the user says "
        "\"add a rule\", \"remember for this project\", \"always/never do X here\", "
        "or asks what the current rules are. Rules are versioned project files "
        "that steer every session in this folder and are shared with the CLI and "
        "TUI; memory is private to the agent and invisible in the repo. "
        "action='list' reads the rule files from disk, which is the correct way to "
        "answer \"what are my rules\" — quote them freely, they are the user's own "
        "files. action='add' appends one rule. action='remove' deletes one by its "
        "index from list. An edit takes effect on your next turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "remove"],
                "description": "list current rules, add one, or remove one by index.",
            },
            "rule": {
                "type": "string",
                "description": (
                    "For action='add': the rule, as one short imperative line "
                    "(e.g. 'always run npm test before saying done')."
                ),
            },
            "file": {
                "type": "string",
                "description": (
                    "Optional .md filename inside .hermes/rules (default rules.md). "
                    "Group related rules in one file rather than one file per rule."
                ),
            },
            "index": {
                "type": "integer",
                "description": "For action='remove': the rule's index as shown by list.",
            },
        },
        "required": ["action"],
    },
}


# Registered into the `memory` toolset deliberately, not a `core`/`rules` one.
#
# Enabled toolsets come from per-platform tool config, which enumerates specific
# toolset names (browser, file, memory, todo, …). A tool in a toolset nobody
# enabled is invisible no matter how many tool-NAME lists it appears in — that is
# exactly how this shipped broken twice: registered under `core`, which is not in
# the desktop's enabled set, so the agent could only see `memory` and dutifully
# wrote project rules there.
#
# A new `rules` toolset would have the same problem in reverse: existing
# config.yaml files enumerate their toolsets, so a name they have never heard of
# stays off.
#
# Pairing it with `memory` makes the requirement structural instead of a list to
# maintain: the invariant is "wherever the agent can reach for memory, it must be
# able to reach for project rules", and sharing the toolset guarantees that by
# construction. They are the two halves of the same question — private recall
# versus versioned project instruction.
registry.register(
    name="project_rule",
    toolset="memory",
    schema=PROJECT_RULE_SCHEMA,
    handler=lambda args, **kw: project_rule_tool(
        action=args.get("action", ""),
        rule=args.get("rule", ""),
        file=args.get("file", ""),
        index=int(args.get("index", -1) or -1),
    ),
    emoji="📏",
)
