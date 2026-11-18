"""Audit trail of what the agent did: one JSON log line per tool call and confirmation.

Free text can carry personal data (e.g. a customer name typed by the user), so text
values are replaced by their length; ids, numbers and booleans are kept.
"""

import json
import logging
from typing import Literal

AUDIT_LOGGER = "app.audit"

_logger = logging.getLogger(AUDIT_LOGGER)

type Outcome = Literal["ok", "error"]
type Redacted = None | bool | int | float | str | list[Redacted] | dict[str, Redacted]


def redact_arguments(arguments: str) -> Redacted:
    try:
        parsed: object = json.loads(arguments)
    except json.JSONDecodeError:
        return f"<invalid-json:{len(arguments)}>"
    return _redact(parsed)


def _redact(value: object) -> Redacted:
    if isinstance(value, str):
        return f"<text:{len(value)}>"
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return f"<{type(value).__name__}>"


def tool_call(
    *, conversation_id: str, tool: str, arguments: str, outcome: Outcome, duration: float
) -> None:
    _emit(
        {
            "event": "tool_call",
            "conversation_id": conversation_id,
            "tool": tool,
            "arguments": redact_arguments(arguments),
            "outcome": outcome,
            "duration_ms": round(duration * 1000, 1),
        }
    )


def quotation_created(
    *,
    draft_id: str,
    partner_id: int,
    quotation_id: int | None,
    outcome: Outcome,
    duration: float,
) -> None:
    _emit(
        {
            "event": "quotation_created",
            "draft_id": draft_id,
            "partner_id": partner_id,
            "quotation_id": quotation_id,
            "outcome": outcome,
            "duration_ms": round(duration * 1000, 1),
        }
    )


def _emit(event: dict[str, object]) -> None:
    _logger.info("%s", json.dumps(event, ensure_ascii=False))
