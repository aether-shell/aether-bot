"""Task request detection — determines if a message should trigger Task-Mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session

# Detection prompt template
_DETECT_PROMPT = """\
You are a task-mode detector. Decide whether the user's message is a \
complex, multi-step task request that should be planned and executed \
autonomously (task mode), or a simple conversational / single-step request.

Criteria for task mode:
- The request involves MULTIPLE distinct steps or changes
- It asks for something that requires planning, file modifications, code changes, or system operations
- It would benefit from a structured plan with approval before execution

Criteria for NOT entering task mode:
- Simple questions or conversation
- Single-step requests (e.g. "read this file", "what time is it")
- Requests that are already very specific and need no planning

Reply with EXACTLY one word: YES or NO
"""


class TaskDetector:
    """Detect whether an inbound message warrants Task-Mode activation."""

    def __init__(self, provider: LLMProvider, model: str | None = None):
        self.provider = provider
        self.model = model

    async def should_enter_task_mode(
        self,
        msg: InboundMessage,
        session: Session,
    ) -> bool:
        """Return ``True`` if *msg* looks like a multi-step task request.

        Uses a lightweight LLM call to classify the message.
        """
        # Skip very short messages
        if len(msg.content.strip()) < 20:
            return False

        # Skip if it looks like a command
        if msg.content.strip().startswith("/"):
            return False

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": _DETECT_PROMPT},
                    {"role": "user", "content": msg.content},
                ],
                tools=[],
                model=self.model,
                max_tokens=8,
                temperature=0.0,
            )
            answer = (response.content or "").strip().upper()
            result = answer.startswith("YES")
            if result:
                logger.info(f"Task detector: message classified as task request")
            return result
        except Exception:
            logger.exception("Task detector failed, defaulting to non-task mode")
            return False
