from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .models import Issue, Message, natural, obj, timestamp

CONTEXT_SOURCES = {
    "plugin",
    "tool",
    "agent-instructions",
    "session-reference",
    "team-message",
    "goal",
    "skill-invocation",
    "skill-catalog",
    "coordinator",
    "subagent-report",
    "subagent-settled",
    "webhook",
    "agent-message",
    "working-directory",
    "runtime-context",
    "time-context",
    "tool-jobs",
    "schedule",
    "user-approval",
    "fworkflow",
    "fcodex-subagent-input-notice",
}

if TYPE_CHECKING:
    from collections.abc import Generator

    from .models import Header, Object
    from .reader import SessionReader


def _codex_role(row: Object, header: Header) -> Literal["user", "assistant"] | None:
    payload = obj(row.get("payload"))
    metadata = obj(row.get("metadata"))
    if metadata.get("retained_source") or row.get("replayed") is True:
        return None
    if row.get("type") == "event_msg":
        kind = payload.get("type")
        roles: dict[str, Literal["user", "assistant"]] = {
            "user_message": "user",
            "agent_message": "assistant",
        }
        if isinstance(kind, str) and kind in roles:
            return roles[kind]
        if kind == "item_completed":
            item = obj(payload.get("item"))
            if item.get("type") == "UserMessage":
                return "user"
            if item.get("type") == "AgentMessage":
                return "assistant"
    # Paginated response_items include model context and mirrors of completed items.
    if (
        row.get("type") == "response_item"
        and header.data.get("history_mode") != "paginated"
        and payload.get("type") == "message"
        and payload.get("role") in ("user", "assistant")
    ):
        return "user" if payload.get("role") == "user" else "assistant"
    return None


def _dsh_role(row: Object, reader: SessionReader, line: int) -> Literal["user", "assistant"] | None:
    kind = row.get("type")
    if kind not in ("user/message", "assistant/message"):
        return None
    operation = row.get("surfaceOp")
    if isinstance(operation, dict) and operation.get("op") == "replace":
        return None
    data = obj(row.get("data"))
    message = data if kind == "user/message" else obj(data.get("message"))
    source = obj(message.get("source")).get("kind")
    if operation == "append" and source == "user" and message.get("role") == "user":
        return "user"
    if operation == "append" and source == "model" and message.get("role") == "assistant":
        return "assistant"
    # Known injected/context sources are deliberately not treated as human dialogue.
    if isinstance(source, str) and source in CONTEXT_SOURCES:
        return None
    reader.errors.append(
        Issue("ambiguous_message_origin", reader.path, "Cannot establish message origin", line)
    )
    return None


def _seed_cut(reader: SessionReader, header: Header) -> int | None:
    legacy = natural(header.data.get("seedLength"))
    if legacy is not None:
        return legacy - 1
    if header.data.get("isSeeded") is not True:
        return -1
    cut = None
    for _, row in reader.records():
        if row.get("type") == "session/end-seed" and obj(row.get("data")).get("inherited") is True:
            cut = natural(row.get("seq"))
    if not reader.complete:
        return None
    if cut is None:
        reader.complete = False
        reader.errors.append(
            Issue("missing_seed_boundary", reader.path, "Seeded history has no inherited boundary")
        )
        return None
    reader.rewind()
    return cut


def _supported_history(reader: SessionReader, header: Header) -> bool:
    if header.source == "codex" and header.data.get("history_mode") not in (
        None,
        "legacy",
        "paginated",
    ):
        reader.errors.append(
            Issue("unsupported_history_mode", reader.path, "Unsupported Codex history mode")
        )
        return False
    return True


def _after_seed_cut(reader: SessionReader, seq: int | None, cut: int, line: int) -> bool:
    if cut < 0:
        return True
    if seq is None:
        reader.errors.append(
            Issue("ambiguous_message_origin", reader.path, "Missing seeded event seq", line)
        )
        return False
    return seq > cut


def iter_messages(reader: SessionReader) -> Generator[Message, None, None]:
    """Yield message locations and event times, never dialogue text or tool output."""
    header = reader.header
    if header is None:
        msg = "Reader is not open"
        raise RuntimeError(msg)
    if not _supported_history(reader, header):
        return
    cut = _seed_cut(reader, header) if header.source == "dsh" else -1
    if cut is None:
        return
    for line, row in reader.records():
        if header.source == "dsh":
            position = natural(row.get("seq"))
            if not _after_seed_cut(reader, position, cut, line):
                continue
            role = _dsh_role(row, reader, line)
            value = row.get("time")
            stamp = timestamp(value, milliseconds=True)
            source = "dsh_event_time"
        else:
            role = _codex_role(row, header)
            position = natural(row.get("ordinal"))
            value = row.get("timestamp")
            stamp = timestamp(value)
            source = "codex_event_timestamp"
        if role is None:
            continue
        if stamp is None:
            code = "missing_message_time" if value is None else "invalid_message_time"
            reader.errors.append(Issue(code, reader.path, "Message has no valid event time", line))
            continue
        yield Message(role, stamp, line, position, source)
