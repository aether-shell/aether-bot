#!/usr/bin/env python3
"""Audit built-in skill consistency beyond basic metadata validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
ABSOLUTE_PATH_RE = re.compile(r"(/Users/|[A-Za-z]:\\\\)")

KNOWN_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "exec",
    "web_search",
    "web_fetch",
    "claude",
    "message",
    "spawn",
    "cron",
}

KNOWLEDGE_SKILLS = {"learn", "deep-learn", "recall", "forget"}


@dataclass
class AuditResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class SkillDoc:
    name: str
    path: Path
    description: str
    metadata: dict
    body: str


def extract_frontmatter(text: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    return match.group(1)


def parse_frontmatter(frontmatter: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in frontmatter.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def load_skill_doc(path: Path) -> SkillDoc:
    text = path.read_text(encoding="utf-8")
    frontmatter = extract_frontmatter(text)
    if frontmatter is None:
        raise ValueError("missing YAML frontmatter block")

    fields = parse_frontmatter(frontmatter)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip().strip('"').strip("'")
    metadata_raw = fields.get("metadata", "")
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata is not valid JSON: {exc}") from exc

    body = text[len(frontmatter) + 8 :]  # leading/trailing ---\n markers
    return SkillDoc(name=name, path=path, description=description, metadata=metadata, body=body)


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        return []
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _workflow_requires_learning_write(workflow: dict) -> bool:
    completion = workflow.get("completion")
    if not isinstance(completion, dict):
        return False
    rules = completion.get("require_tool_calls")
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("name") or "").strip().lower() != "write_file":
            continue
        args = rule.get("args")
        if not isinstance(args, dict):
            continue
        pattern = str(args.get("path_regex") or "")
        if pattern.startswith("^memory/learnings/"):
            return True
    return False


def audit_skill(doc: SkillDoc) -> AuditResult:
    result = AuditResult()

    if not doc.name:
        result.errors.append("missing frontmatter field: name")
        return result
    if doc.name != doc.path.parent.name:
        result.errors.append(f"name '{doc.name}' does not match folder '{doc.path.parent.name}'")

    if not doc.description:
        result.errors.append("missing frontmatter field: description")
    elif "use when" not in doc.description.lower():
        result.errors.append("description should include 'Use when ...' intent guidance")

    nanobot = doc.metadata.get("nanobot") if isinstance(doc.metadata, dict) else None
    if not isinstance(nanobot, dict):
        result.errors.append("metadata.nanobot must be a JSON object")
        return result

    triggers = _as_list(nanobot.get("triggers"))
    if not triggers:
        result.errors.append("metadata.nanobot.triggers must be a non-empty list")
    else:
        normalized = [item.lower() for item in triggers]
        dupes = sorted({item for item in normalized if normalized.count(item) > 1})
        if dupes:
            result.warnings.append(f"duplicate triggers found: {', '.join(dupes)}")
        if len(triggers) < 3:
            result.warnings.append("trigger coverage is narrow (<3 entries)")

    allowed_tools = _as_list(nanobot.get("allowed_tools"))
    if not allowed_tools:
        result.errors.append("metadata.nanobot.allowed_tools must be a non-empty list")
    else:
        unknown = sorted({tool for tool in allowed_tools if tool not in KNOWN_TOOLS})
        if unknown:
            result.warnings.append(f"unknown tool names in allowed_tools: {', '.join(unknown)}")

    body_lower = doc.body.lower()
    if doc.name in KNOWLEDGE_SKILLS:
        if "memory/learnings/" not in doc.body:
            result.errors.append("knowledge skill must reference workspace-relative memory/learnings/ path")
        if ABSOLUTE_PATH_RE.search(doc.body):
            result.errors.append("knowledge skill should not contain absolute host paths in instructions")

    if doc.name in {"learn", "deep-learn"}:
        workflow = nanobot.get("workflow")
        if not isinstance(workflow, dict):
            result.errors.append("learn/deep-learn should define metadata.nanobot.workflow")
        else:
            kickoff = workflow.get("kickoff")
            if not isinstance(kickoff, dict) or not bool(kickoff.get("require_substantive_action")):
                result.warnings.append("workflow.kickoff.require_substantive_action is not explicitly true")
            if not _workflow_requires_learning_write(workflow):
                result.errors.append(
                    "workflow.completion.require_tool_calls should enforce write_file path_regex '^memory/learnings/'"
                )
            progress = workflow.get("progress")
            if isinstance(progress, dict):
                milestones = progress.get("milestones")
                if isinstance(milestones, dict) and milestones.get("enabled"):
                    templates = milestones.get("templates")
                    if not isinstance(templates, dict) or "kickoff" not in templates:
                        result.warnings.append("workflow.progress.milestones enabled without kickoff template")
            else:
                result.warnings.append("workflow.progress is missing")

    if doc.name == "recall":
        required_markers = ["exact slug match", "partial filename match", "content search"]
        for marker in required_markers:
            if marker not in body_lower:
                result.errors.append(f"recall workflow should include step: '{marker}'")

    if doc.name == "forget":
        if "confirm" not in body_lower and "确认" not in doc.body:
            result.errors.append("forget workflow must explicitly require confirmation before deletion")
        if "never delete without explicit user confirmation" not in body_lower:
            result.warnings.append("consider adding explicit 'never delete without confirmation' guardrail")

    return result


def discover_skill_files(skills_root: Path) -> list[Path]:
    return sorted(p / "SKILL.md" for p in skills_root.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def render_text_report(skills_root: Path, results: dict[str, AuditResult]) -> str:
    checked = len(results)
    failed = sum(1 for res in results.values() if res.errors)
    warned = sum(1 for res in results.values() if (not res.errors and res.warnings))
    passed = checked - failed - warned
    total_errors = sum(len(res.errors) for res in results.values())
    total_warnings = sum(len(res.warnings) for res in results.values())

    lines = [
        "Skill consistency audit report",
        f"Root: {skills_root}",
        f"Checked: {checked} | Passed: {passed} | Warned: {warned} | Failed: {failed}",
        f"Issues: {total_errors} errors, {total_warnings} warnings",
        "",
    ]

    for skill_name in sorted(results.keys()):
        res = results[skill_name]
        status = "FAIL" if res.errors else ("WARN" if res.warnings else "PASS")
        lines.append(f"[{status}] {skill_name}")
        for err in res.errors:
            lines.append(f"  - ERROR: {err}")
        for warn in res.warnings:
            lines.append(f"  - WARN: {warn}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit skill consistency and implementation conventions.")
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Root directory containing skill subdirectories (default: nanobot/skills).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON report.")
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Return non-zero exit code when warnings exist.",
    )
    args = parser.parse_args()

    skills_root = args.skills_root.expanduser().resolve()
    if not skills_root.exists() or not skills_root.is_dir():
        print(f"skills root does not exist or is not a directory: {skills_root}", file=sys.stderr)
        return 2

    results: dict[str, AuditResult] = {}
    for skill_file in discover_skill_files(skills_root):
        skill_name = skill_file.parent.name
        try:
            doc = load_skill_doc(skill_file)
        except Exception as exc:  # noqa: BLE001
            results[skill_name] = AuditResult(errors=[str(exc)])
            continue
        results[skill_name] = audit_skill(doc)

    has_errors = any(res.errors for res in results.values())
    has_warnings = any(res.warnings for res in results.values())

    if args.json:
        payload = {
            "skills_root": str(skills_root),
            "checked": sorted(results.keys()),
            "ok": not has_errors and (not has_warnings or not args.fail_on_warn),
            "has_errors": has_errors,
            "has_warnings": has_warnings,
            "results": {
                name: {"errors": res.errors, "warnings": res.warnings}
                for name, res in sorted(results.items())
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text_report(skills_root, results))

    if has_errors:
        return 1
    if args.fail_on_warn and has_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
