"""Runtime logging setup helpers for nanobot."""

from __future__ import annotations

import os
import sys
from typing import Literal

from loguru import logger

_VALID_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
_DEFAULT_LEVEL = "DEBUG"
_CONFIGURED = False


def _normalize_level(level: str | None) -> str:
    if not level:
        return _DEFAULT_LEVEL
    normalized = level.strip().upper()
    if normalized in _VALID_LEVELS:
        return normalized
    return _DEFAULT_LEVEL


def configure_logging(
    *,
    level: str | None = None,
    force: bool = False,
    colorize: bool | None = None,
) -> Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]:
    """
    Configure loguru output to console with a consistent debug-friendly format.

    This function is idempotent unless ``force=True``.
    """
    global _CONFIGURED

    if _CONFIGURED and not force:
        return _normalize_level(level or os.getenv("NANOBOT_LOG_LEVEL"))

    resolved_level = _normalize_level(level or os.getenv("NANOBOT_LOG_LEVEL"))
    resolved_colorize = (
        colorize
        if colorize is not None
        else os.getenv("NANOBOT_LOG_COLOR", "1").lower() not in {"0", "false", "off"}
    )

    logger.remove()
    logger.add(
        sys.stderr,
        level=resolved_level,
        colorize=resolved_colorize,
        enqueue=False,
        backtrace=False,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
    )

    _CONFIGURED = True
    logger.debug(f"Logging configured: level={resolved_level} colorize={resolved_colorize}")
    return resolved_level  # type: ignore[return-value]
