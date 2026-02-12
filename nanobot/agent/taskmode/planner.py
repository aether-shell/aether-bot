"""Plan generation — uses LLM to produce OpenSpec artefacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.taskmode.state import WAIT_APPROVAL, StateManager, TaskState

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session


@dataclass
class PlanResult:
    change_id: str
    summary: str


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PROPOSAL_PROMPT = """\
You are a technical planner. Given the user's task description, write a concise \
proposal document in Markdown.

The proposal should include:
1. **Goal** — what the task aims to achieve
2. **Scope** — what is in and out of scope
3. **Prerequisites** — what must be true before starting (human prerequisites)
4. **Execution boundary** — what the agent is allowed to do (files, commands, etc.)
5. **Risks** — potential issues

Keep it concise and actionable. Write in the language the user used.
"""

_DESIGN_PROMPT = """\
You are a technical architect. Given the task description and proposal, write a \
short design document in Markdown.

Include:
1. **Approach** — high-level strategy
2. **Key decisions** — technology choices, patterns
3. **Files affected** — list of files that will be created or modified

Keep it concise. Write in the language the user used.
"""

_TASKS_PROMPT = """\
You are a task planner. Given the task description, proposal, and design, \
generate a tasks.md file with executable steps as a Markdown checkbox list.

Rules:
- Use `- [ ]` for each task
- For shell commands, prefix with `cmd: `, e.g. `- [ ] cmd: npm install`
- For file operations or complex steps, describe what needs to be done
- Include preflight checks at the beginning (verify prerequisites)
- Include verification steps at the end
- Order tasks logically
- Keep each task atomic and clear

Write in the language the user used.
"""


class TaskPlanner:
    """Generate OpenSpec artefacts (proposal, design, tasks) via LLM."""

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        state_mgr: StateManager | None = None,
        model: str | None = None,
    ):
        self.workspace = workspace
        self.provider = provider
        self.state_mgr = state_mgr or StateManager()
        self.model = model

    async def generate_plan(
        self,
        description: str,
        session: Session,
    ) -> PlanResult:
        """Generate a complete OpenSpec change with artefacts.

        1. Create change directory
        2. Generate proposal.md, design.md, tasks.md via LLM
        3. Initialise state.json (WAIT_APPROVAL)
        4. Compute plan digest
        """
        change_id = self._make_change_id(description)
        change_dir = self.state_mgr.change_dir(self.workspace, change_id)
        change_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Generating plan for change '{change_id}'")

        # Generate artefacts
        proposal = await self._generate_proposal(description, session)
        design = await self._generate_design(description, proposal, session)
        tasks = await self._generate_tasks(description, proposal, design, session)

        # Write artefacts
        (change_dir / "proposal.md").write_text(proposal, encoding="utf-8")
        (change_dir / "design.md").write_text(design, encoding="utf-8")
        (change_dir / "tasks.md").write_text(tasks, encoding="utf-8")

        # Initialise state
        from datetime import datetime
        digest = self.state_mgr.compute_plan_digest(change_dir)
        now = datetime.utcnow().isoformat() + "Z"
        state = TaskState(
            change_id=change_id,
            repo_path=str(self.workspace),
            status=WAIT_APPROVAL,
            created_at=now,
            updated_at=now,
        )
        state.plan.plan_digest = digest
        state.plan.head_sha = self._get_head_sha()
        self.state_mgr.write(change_dir, state)

        summary = self._format_plan_summary(proposal, tasks, change_id)
        return PlanResult(change_id=change_id, summary=summary)

    # ------ LLM calls ------

    async def _generate_proposal(self, description: str, session: Session) -> str:
        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": _PROPOSAL_PROMPT},
                {"role": "user", "content": description},
            ],
            tools=[],
            model=self.model,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.content or ""

    async def _generate_design(
        self, description: str, proposal: str, session: Session
    ) -> str:
        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": _DESIGN_PROMPT},
                {
                    "role": "user",
                    "content": f"## Task\n{description}\n\n## Proposal\n{proposal}",
                },
            ],
            tools=[],
            model=self.model,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.content or ""

    async def _generate_tasks(
        self, description: str, proposal: str, design: str, session: Session
    ) -> str:
        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": _TASKS_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"## Task\n{description}\n\n"
                        f"## Proposal\n{proposal}\n\n"
                        f"## Design\n{design}"
                    ),
                },
            ],
            tools=[],
            model=self.model,
            max_tokens=4096,
            temperature=0.3,
        )
        return response.content or ""

    # ------ helpers ------

    @staticmethod
    def _make_change_id(description: str) -> str:
        """Derive a short, filesystem-safe change ID from *description*."""
        from datetime import datetime
        slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", description[:40]).strip("-").lower()
        if not slug:
            slug = "change"
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"{slug}-{ts}"

    def _get_head_sha(self) -> str | None:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    @staticmethod
    def _format_plan_summary(proposal: str, tasks: str, change_id: str) -> str:
        lines = [
            f"## Task Plan: `{change_id}`\n",
            proposal[:1500],
            "\n---\n",
            "### Tasks\n",
            tasks[:2000],
            "\n---\n",
            f"To approve and start execution: `/approve {change_id}`\n"
            f"To cancel: `/cancel {change_id}`\n"
            f"To check status: `/status {change_id}`",
        ]
        return "\n".join(lines)
