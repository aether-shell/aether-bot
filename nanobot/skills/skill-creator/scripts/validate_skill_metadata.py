#!/usr/bin/env python3
"""Validate skill frontmatter metadata for routing quality."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


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


def parse_metadata_json(raw: str) -> tuple[dict | None, str | None]:
    if not raw:
        return None, "missing metadata field"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"metadata is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "metadata must decode to a JSON object"
    return parsed, None


def validate_string_list(value: object, field_name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return [f"{field_name} must be a non-empty list"]
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field_name}[{idx}] must be a non-empty string")
    return errors


def validate_skill_file(skill_file: Path) -> list[str]:
    errors: list[str] = []

    if not skill_file.exists():
        return [f"{skill_file}: missing SKILL.md"]

    text = skill_file.read_text(encoding="utf-8")
    frontmatter = extract_frontmatter(text)
    if frontmatter is None:
        return [f"{skill_file}: missing YAML frontmatter block"]

    fields = parse_frontmatter(frontmatter)
    skill_name = fields.get("name", "").strip()
    expected_name = skill_file.parent.name
    if not skill_name:
        errors.append(f"{skill_file}: missing frontmatter field 'name'")
    elif skill_name != expected_name:
        errors.append(f"{skill_file}: name '{skill_name}' should match folder '{expected_name}'")

    description = fields.get("description", "").strip().strip('"').strip("'")
    if not description:
        errors.append(f"{skill_file}: missing frontmatter field 'description'")

    metadata_raw = fields.get("metadata", "")
    metadata_obj, metadata_err = parse_metadata_json(metadata_raw)
    if metadata_err:
        errors.append(f"{skill_file}: {metadata_err}")
        return errors

    nanobot = metadata_obj.get("nanobot") if isinstance(metadata_obj, dict) else None
    if not isinstance(nanobot, dict):
        errors.append(f"{skill_file}: metadata.nanobot must be an object")
        return errors

    emoji = nanobot.get("emoji")
    if not isinstance(emoji, str) or not emoji.strip():
        errors.append(f"{skill_file}: metadata.nanobot.emoji must be a non-empty string")

    triggers = nanobot.get("triggers")
    errors.extend(f"{skill_file}: {msg}" for msg in validate_string_list(triggers, "metadata.nanobot.triggers"))

    aliases = nanobot.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, list):
            errors.append(f"{skill_file}: metadata.nanobot.aliases must be a list when present")
        else:
            for idx, item in enumerate(aliases):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"{skill_file}: metadata.nanobot.aliases[{idx}] must be a non-empty string")

    allowed_tools = nanobot.get("allowed_tools")
    errors.extend(
        f"{skill_file}: {msg}"
        for msg in validate_string_list(allowed_tools, "metadata.nanobot.allowed_tools")
    )

    return errors


def discover_skill_files(skills_root: Path) -> list[Path]:
    return sorted(p / "SKILL.md" for p in skills_root.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def resolve_skill_inputs(inputs: list[str], skills_root: Path) -> list[Path]:
    resolved: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            skill_file = path / "SKILL.md"
            if skill_file.exists():
                resolved.append(skill_file)
                continue

            root_relative = skills_root / path.name / "SKILL.md"
            if root_relative.exists():
                resolved.append(root_relative)
                continue
        elif path.is_file() and path.name == "SKILL.md":
            resolved.append(path)
            continue

        candidate = (skills_root / raw / "SKILL.md").resolve()
        if candidate.exists():
            resolved.append(candidate)
            continue

        raise FileNotFoundError(f"Cannot resolve skill path: {raw}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate skill metadata for routing and discoverability.")
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Root directory containing skill subdirectories (default: nanobot/skills).",
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Skill name/path to validate. Repeatable. If omitted, validates all skills in --skills-root.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    skills_root = args.skills_root.expanduser().resolve()
    if not skills_root.exists() or not skills_root.is_dir():
        print(f"skills root does not exist or is not a directory: {skills_root}", file=sys.stderr)
        return 2

    try:
        skill_files = (
            resolve_skill_inputs(args.skill, skills_root)
            if args.skill
            else discover_skill_files(skills_root)
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    all_errors: dict[str, list[str]] = {}
    for skill_file in skill_files:
        errs = validate_skill_file(skill_file)
        if errs:
            all_errors[str(skill_file)] = errs

    if args.json:
        payload = {
            "skills_root": str(skills_root),
            "checked": [str(p) for p in skill_files],
            "ok": not all_errors,
            "errors": all_errors,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Checked {len(skill_files)} skill(s) under {skills_root}")
        if all_errors:
            for skill_file, errs in all_errors.items():
                print(f"\n{skill_file}:")
                for err in errs:
                    print(f"  - {err}")
            print("\nValidation failed.")
        else:
            print("Validation passed.")

    return 1 if all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
