"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []

        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})

        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def select_skills_for_message(self, message: str, max_skills: int = 2) -> list[str]:
        """
        Select relevant available skills for a user message.

        Matching strategy (in priority order):
        1. Explicit skill mention (e.g. "weather", "$weather")
        2. Skill trigger keywords from frontmatter metadata.nanobot.triggers

        Args:
            message: User message text.
            max_skills: Upper bound for returned skills.

        Returns:
            Ordered skill names by confidence.
        """
        text = (message or "").strip()
        if not text:
            return []

        lowered = text.lower()
        candidates: list[tuple[int, int, str]] = []

        for skill in self.list_skills(filter_unavailable=True):
            name = skill["name"]
            name_lower = name.lower()
            score = 0
            trigger_hits = 0

            if self._is_explicit_skill_mention(lowered, name_lower):
                score += 100

            skill_meta = self._get_skill_meta(name)
            raw_aliases = skill_meta.get("aliases", [])
            aliases = raw_aliases if isinstance(raw_aliases, list) else [raw_aliases]
            for alias in aliases:
                alias_text = str(alias).strip().lower()
                if not alias_text:
                    continue
                if self._is_explicit_skill_mention(lowered, alias_text):
                    score += 60

            raw_triggers = skill_meta.get("triggers", [])
            triggers = raw_triggers if isinstance(raw_triggers, list) else [raw_triggers]
            for trigger in triggers:
                trig = str(trigger).strip().lower()
                if not trig:
                    continue
                if self._message_matches_trigger(lowered, trig):
                    trigger_hits += 1
                    score += 20

            if score > 0:
                candidates.append((score, trigger_hits, name))

        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        limit = max(1, int(max_skills))
        return [name for _, _, name in candidates[:limit]]

    def get_allowed_tools_for_skills(self, skill_names: list[str]) -> list[str]:
        """
        Collect allowed tool names from matched skills metadata.

        Reads `metadata.nanobot.allowed_tools` from each skill and returns a
        de-duplicated, order-preserving list.
        """
        selected: list[str] = []
        seen: set[str] = set()
        for name in skill_names:
            meta = self._get_skill_meta(name)
            raw_allowed = meta.get("allowed_tools", [])
            allowed_items = raw_allowed if isinstance(raw_allowed, list) else [raw_allowed]
            for tool_name in allowed_items:
                normalized = str(tool_name).strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                selected.append(normalized)
        return selected

    def get_tool_round_limited_skills(self, skill_names: list[str]) -> list[str]:
        """
        Return matched skills that should use the tool-round hard limit.

        A skill is considered limited when one of the following is true:
        - `metadata.nanobot.tool_round_limit` is true.
        - `metadata.nanobot.tags` or `metadata.nanobot.categories` contains
          realtime/network style markers.
        """
        selected: list[str] = []
        seen: set[str] = set()
        marker_tags = {
            "realtime",
            "real-time",
            "real_time",
            "network",
            "networked",
            "live",
            "live-data",
            "external-data",
            "external",
            "weather",
        }

        for name in skill_names:
            if name in seen:
                continue
            meta = self._get_skill_meta(name)
            if not isinstance(meta, dict):
                continue

            if bool(meta.get("tool_round_limit")):
                selected.append(name)
                seen.add(name)
                continue

            tags = self._normalize_meta_list(meta.get("tags"))
            categories = self._normalize_meta_list(meta.get("categories"))
            merged = tags + [item for item in categories if item not in tags]
            if any(tag in marker_tags for tag in merged):
                selected.append(name)
                seen.add(name)

        return selected

    def _is_explicit_skill_mention(self, message_lower: str, skill_name_lower: str) -> bool:
        """Check whether the message explicitly names a skill."""
        if f"${skill_name_lower}" in message_lower:
            return True
        return bool(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(skill_name_lower)}(?![a-z0-9_])",
                message_lower,
            )
        )

    def _message_matches_trigger(self, message_lower: str, trigger_lower: str) -> bool:
        """Match a trigger against the message with basic language-aware heuristics."""
        if self._contains_cjk(trigger_lower):
            return trigger_lower in message_lower

        # Multi-word or symbol-heavy triggers use substring matching.
        if any(ch.isspace() for ch in trigger_lower) or "-" in trigger_lower or "_" in trigger_lower:
            return trigger_lower in message_lower

        # Single English-like tokens use word boundaries.
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(trigger_lower)}(?![a-z0-9])",
                message_lower,
            )
        )

    def _contains_cjk(self, value: str) -> bool:
        """Return True when the string includes CJK Unified Ideographs."""
        for ch in value:
            if "\u4e00" <= ch <= "\u9fff":
                return True
        return False

    def _normalize_meta_list(self, value: object) -> list[str]:
        """Normalize frontmatter list-like values into lower-cased strings."""
        if isinstance(value, list):
            raw_items = value
        elif value is None:
            raw_items = []
        else:
            raw_items = [value]

        normalized: list[str] = []
        for item in raw_items:
            text = str(item).strip().lower()
            if text:
                normalized.append(text)
        return normalized

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """Parse nanobot metadata JSON from frontmatter."""
        try:
            data = json.loads(raw)
            return data.get("nanobot", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_nanobot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata

        return None
