from __future__ import annotations

import hashlib
from contextlib import suppress
from json import JSONDecodeError
from typing import TYPE_CHECKING

from .items import native_item, same_duration
from .models import (
    Conversion,
    Event,
    Json,
    Object,
    Session,
    array,
    decode,
    encode,
    integer,
    obj,
    string,
    timestamp,
)
from .sources import database_parts

if TYPE_CHECKING:
    from pathlib import Path

TERMINAL = {"completed", "failed", "interrupted", "cancelled", "success", "error"}
ACTIVE = {"inProgress", "in_progress", "running", "pending"}
PASSTHROUGH = {
    "turn_context",
    "compacted",
    "token_usage_record",
    "world_state",
    "inter_agent_communication_metadata",
}


def stable_id(source: Object) -> str:
    return "export-" + hashlib.sha256(encode(source).encode()).hexdigest()[:24]


def parse_event(line: bytes) -> tuple[Object, str, Object]:
    record = obj(decode(line))
    kind = string(record.get("type"))
    payload = obj(record.get("payload"))
    if not kind or not payload:
        msg = "Event requires type and object payload"
        raise ValueError(msg)
    if kind in {"event_msg", "response_item"} and not isinstance(payload.get("type"), str):
        msg = "Event payload requires a string type"
        raise ValueError(msg)
    return record, kind, payload


def normalize_payload(kind: str, payload: Object, source: Object) -> None:
    completed = kind == "event_msg" and payload.get("type") == "item_completed"
    if completed:
        payload["item"] = native_item(obj(payload.get("item")))
    identity_object = obj(payload.get("item")) if completed else payload
    message = kind == "response_item" and payload.get("type") in {"message", "reasoning"}
    if (message or completed) and not identity_object.get("id"):
        identity_object["id"] = stable_id(source)
        source["generated_id"] = identity_object["id"]


def read_rollout(path: Path, origin: str, rank: int, result: Conversion) -> None:
    turn_id = ""
    with path.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            source: Object = {"file": origin, "line": number}
            try:
                record, kind, payload = parse_event(line)
                normalize_payload(kind, payload, source)
            except (ValueError, UnicodeError) as error:
                if not line.endswith(b"\n") and isinstance(error, (JSONDecodeError, UnicodeError)):
                    result.partial = True
                    result.warnings.append({"code": "partial_tail", "source": source})
                else:
                    result.error("invalid_jsonl", source, str(error))
                continue
            if kind == "session_meta":
                continue
            ordinal = integer(record.get("ordinal"), number - 1)
            source["original_ordinal"] = record.get("ordinal")
            if record.get("ordinal") is None:
                source["order_fallback"] = "line_number"
            if kind == "event_msg" and payload.get("type") == "task_started":
                turn_id = string(payload.get("turn_id"))
            result.events.append(
                Event(
                    kind,
                    payload,
                    timestamp(record.get("timestamp")),
                    (ordinal, rank, 0),
                    [source],
                    string(payload.get("turn_id")) or turn_id,
                )
            )


def item_view(event: Event, item: Object) -> Event | None:
    kind = item.get("type")
    payload: Object = {"type": "message", "id": item.get("id")}
    if kind == "UserMessage":
        content: list[Json] = []
        for value in array(item.get("content")):
            part = obj(value)
            if part.get("type") == "text":
                content.append({"type": "input_text", "text": part.get("text")})
            elif part.get("type") == "image":
                content.append({"type": "input_image", "image_url": part.get("url")})
        # Skill/local image references remain in the structured user item.
        payload.update(role="user", content=content)
    elif kind == "AgentMessage":
        payload.update(
            role="assistant",
            content=[
                {
                    "type": "output_text",
                    "text": "".join(
                        string(obj(part).get("text")) for part in array(item.get("content"))
                    ),
                }
            ],
        )
        if item.get("phase") is not None:
            payload["phase"] = item["phase"]
    elif kind == "Reasoning":
        payload = {
            "type": "reasoning",
            "id": item.get("id"),
            "summary": [
                {"type": "summary_text", "text": text} for text in array(item.get("summary_text"))
            ],
            "content": [
                {"type": "reasoning_text", "text": text} for text in array(item.get("raw_content"))
            ],
        }
    else:
        return None
    order = (event.order[0], event.order[1], -1)
    return Event("response_item", payload, event.time, order, event.sources, event.turn_id)


def turn_events(rows: list[Object], session: Session, result: Conversion) -> None:
    for row in database_parts(rows, "thread_turns"):
        turn_id = string(row.get("turn_id"))
        start = timestamp(row.get("started_at"))
        finish = timestamp(row.get("completed_at"))
        source: Object = {"table": "thread_turns", "thread_id": session.id, "turn_id": turn_id}
        ordinal = integer(row.get("rollout_ordinal"))
        if start is not None:
            result.events.append(
                Event(
                    "event_msg",
                    {
                        "type": "task_started",
                        "turn_id": turn_id,
                        "started_at": row.get("started_at"),
                    },
                    start,
                    (ordinal, 10000, -2),
                    [source],
                    turn_id,
                )
            )
        status = row.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            continue
        end_ordinal = row.get("rollout_end_ordinal")
        if not isinstance(end_ordinal, int):
            item_orders = [
                integer(item.get("rollout_ordinal"))
                for item in database_parts(rows, "thread_items")
                if item.get("turn_id") == turn_id
            ]
            end_ordinal = max([ordinal, *item_orders])
            source = {**source, "order_fallback": "after_turn_items"}
        payload: Object = {
            "type": "turn_aborted" if status == "interrupted" else "task_complete",
            "turn_id": turn_id,
        }
        for name in ("started_at", "completed_at", "duration_ms", "root_turn_id"):
            if row.get(name) is not None:
                payload[name] = row[name]
        if status == "failed":
            raw = row.get("error_json")
            try:
                payload["error"] = decode(raw) if isinstance(raw, str) else raw
            except ValueError:
                payload["error"] = {"message": raw}
            if payload["error"] is None:
                payload["error"] = {"message": "Recorded failed turn; original error unavailable"}
                result.missing.append("turn_error_details")
        result.events.append(
            Event("event_msg", payload, finish, (end_ordinal, 10000, 2), [source], turn_id)
        )


def parse_item(row: Object) -> Object:
    item = obj(decode(string(row.get("item_json"))))
    if not item:
        msg = "item_json must be a nonempty object"
        raise ValueError(msg)
    if item.get("id") and item["id"] != row.get("item_id"):
        msg = "item_json ID differs from database item_id"
        raise ValueError(msg)
    item["id"] = row.get("item_id")
    return native_item(item)


def database_events(rows: list[Object], session: Session, result: Conversion) -> None:
    turn_events(rows, session, result)
    turns = {string(row.get("turn_id")) for row in database_parts(rows, "thread_turns")}
    for row in database_parts(rows, "thread_items"):
        source = {
            "table": "thread_items",
            "thread_id": session.id,
            "turn_id": row.get("turn_id"),
            "item_id": row.get("item_id"),
        }
        try:
            item = parse_item(row)
        except ValueError as error:
            result.error("invalid_item", source, str(error))
            continue
        turn_id = string(row.get("turn_id"))
        if turn_id not in turns:
            result.warnings.append({"code": "orphan_item", "source": source})
        source["original_ordinal"] = row.get("rollout_ordinal")
        source["updated_at_ordinal"] = row.get("updated_at_ordinal")
        source["conversion"] = "database_projection_to_native_item"
        if item.get("type") == "CommandExecution":
            source["reconstructed_fields"] = ["command: POSIX parsing of recorded display command"]
        payload: Object = {
            "type": "item_completed",
            "thread_id": session.id,
            "turn_id": turn_id,
            "item": native_item(item),
            "started_at_ms": row.get("started_at_ms"),
            "completed_at_ms": row.get("completed_at_ms"),
        }
        result.events.append(
            Event(
                "event_msg",
                payload,
                timestamp(row.get("created_at_ms"), milliseconds=True),
                (integer(row.get("rollout_ordinal")), 10000, 0),
                [source],
                turn_id,
            )
        )


def event_key(event: Event) -> tuple[str, ...] | None:
    payload = event.payload
    subtype = string(payload.get("type"))
    if event.type == "event_msg":
        if subtype == "item_completed":
            return ("item", event.turn_id, string(obj(payload.get("item")).get("id")))
        if subtype in {"task_started", "task_complete", "turn_aborted"}:
            return ("turn", event.turn_id, subtype)
    if event.type == "response_item":
        identity = string(payload.get("call_id")) or string(payload.get("id"))
        if identity:
            return ("response", event.turn_id, subtype, identity)
    return ("event", event.type, subtype, str(event.order[0]), encode(payload))


def merge_objects(old: Object, new: Object, *, newer: bool = False) -> tuple[Object, bool]:
    merged = dict(old)
    conflict = False
    for key, value in new.items():
        previous = merged.get(key)
        if previous is None or previous == value:
            merged[key] = value
        elif value is None:
            continue
        elif isinstance(previous, dict) and isinstance(value, dict):
            if key == "duration" and same_duration(previous, value):
                # Database projection has millisecond precision; native duration has nanoseconds.
                continue
            merged[key], nested = merge_objects(previous, value, newer=newer)
            conflict |= nested
        elif newer:
            merged[key] = value
        else:
            conflict = True
    return merged, conflict


def merge_events(result: Conversion) -> None:
    merged: list[Event] = []
    indexed: dict[tuple[str, ...], Event] = {}
    # Process native sources before database projections; output ordering is independent.
    for event in sorted(
        result.events, key=lambda entry: (entry.order[1], entry.order[0], entry.order[2])
    ):
        key = event_key(event)
        if key is None or key not in indexed:
            merged.append(event)
            if key is not None:
                indexed[key] = event
            continue
        previous = indexed[key]
        old_item = obj(previous.payload.get("item"))
        new_item = obj(event.payload.get("item"))
        newer = (
            string(old_item.get("status")) in ACTIVE and string(new_item.get("status")) in TERMINAL
        )
        previous.payload, conflict = merge_objects(previous.payload, event.payload, newer=newer)
        previous.sources.extend(event.sources)
        previous.time = previous.time or event.time
        if conflict:
            result.error("source_conflict", list(event.sources), f"Conflicting values for {key}")
    result.events = merged
    ranks = {event.order[1] for event in merged}
    if len(ranks) > 1 and any(
        source.get("generated_id") for event in merged for source in event.sources
    ):
        result.error(
            "ambiguous_identity", None, "Generated IDs cannot establish cross-source identity"
        )
    missing_file_ordinals = any(
        source.get("order_fallback") == "line_number"
        for event in merged
        for source in event.sources
    )
    if len(ranks) > 1 and missing_file_ordinals:
        result.error("ambiguous_order", None, "Multiple sources lack a shared rollout ordinal")
        width = max((event.order[0] for event in merged), default=0) + 1
        for event in merged:
            event.order = (event.order[1] * width + event.order[0], event.order[1], event.order[2])


def add_views(result: Conversion) -> None:
    present = {event_key(event): event for event in result.events if event_key(event) is not None}
    for event in list(result.events):
        if event.type != "event_msg" or event.payload.get("type") != "item_completed":
            continue
        view = item_view(event, obj(event.payload.get("item")))
        if view is None:
            continue
        key = event_key(view)
        if key in present:
            # Native message view may contain additional provenance/content; retain it.
            present[key].sources.extend(
                source for source in event.sources if source not in present[key].sources
            )
        else:
            result.events.append(view)
            if key is not None:
                present[key] = view


def add_legacy_tools(result: Conversion, session_id: str) -> None:
    completed = {
        string(obj(event.payload.get("item")).get("id"))
        for event in result.events
        if event.payload.get("type") == "item_completed"
    }
    outputs = {
        string(event.payload.get("call_id")): event
        for event in result.events
        if event.type == "response_item"
        and event.payload.get("type") in {"function_call_output", "custom_tool_call_output"}
    }
    for event in list(result.events):
        payload = event.payload
        if event.type != "response_item" or payload.get("type") not in {
            "function_call",
            "custom_tool_call",
        }:
            continue
        call_id = string(payload.get("call_id"))
        if not call_id or call_id in completed:
            continue
        output = outputs.get(call_id)
        arguments: Json = payload.get("input", payload.get("arguments"))
        if isinstance(arguments, str):
            with suppress(ValueError):
                arguments = decode(arguments)
        item: Object = {
            "type": "dynamicToolCall",
            "id": call_id,
            "tool": payload.get("name"),
            "namespace": payload.get("namespace"),
            "arguments": arguments,
            "status": "completed" if output else "inProgress",
            "contentItems": [{"type": "text", "text": encode(output.payload.get("output"))}]
            if output
            else [],
            "success": None,
        }
        result.events.append(
            Event(
                "event_msg",
                {
                    "type": "item_completed",
                    "thread_id": session_id,
                    "turn_id": event.turn_id,
                    "item": native_item(item),
                    "started_at_ms": None,
                    "completed_at_ms": None,
                },
                output.time if output else event.time,
                output.order if output else event.order,
                [*event.sources, *(output.sources if output else [])],
                event.turn_id,
            )
        )


def session_meta(session: Session) -> Object:
    metadata = dict(session.file_meta[session.files[0]]) if session.files else {}
    metadata["id"] = session.id
    if session.cwd:
        metadata["cwd"] = session.cwd
    for name in ("model_provider", "cli_version", "source", "parent_thread_id"):
        if session.attributes.get(name) is not None:
            metadata[name] = session.attributes[name]
    return metadata
