from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING

from .models import (
    EPOCH,
    Conversion,
    ExportError,
    Json,
    Object,
    Session,
    array,
    decode,
    integer,
    obj,
    string,
    timestamp,
)

if TYPE_CHECKING:
    from pathlib import Path

FORMAT = "codex-rollout-export-v1"


@lru_cache(maxsize=1)
def schema() -> Object:
    return obj(
        decode(files("codex_session_export").joinpath("schemas/rollout-v1.schema.json").read_text())
    )


def type_matches(value: Json, rule: Object) -> bool:
    kind = rule.get("type")
    types = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "null": value is None,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    return not isinstance(kind, str) or types.get(kind, False)


def object_matches(value: Object, rule: Object) -> bool:
    properties = obj(rule.get("properties"))
    extras = rule.get("additionalProperties")
    return (
        all(string(key) in value for key in array(rule.get("required")))
        and (rule.get("additionalProperties") is not False or value.keys() <= properties.keys())
        and all(
            conforms(child, obj(properties[key]))
            for key, child in value.items()
            if key in properties
        )
        and (
            not isinstance(extras, dict)
            or all(conforms(child, extras) for key, child in value.items() if key not in properties)
        )
    )


def scalar_matches(value: Json, rule: Object) -> bool:
    if isinstance(value, str):
        if len(value) < integer(rule.get("minLength")):
            return False
        if "pattern" in rule and re.search(string(rule["pattern"]), value) is None:
            return False
    minimum = rule.get("minimum")
    return not (
        isinstance(minimum, (int, float)) and isinstance(value, (int, float)) and value < minimum
    )


def conforms(value: Json, rule: Object) -> bool:
    if "$ref" in rule:
        reference: Json = schema()
        for component in string(rule["$ref"]).removeprefix("#/").split("/"):
            reference = obj(reference).get(component)
        return conforms(value, obj(reference))
    checks = [
        type_matches(value, rule),
        "const" not in rule or value == rule["const"],
        "enum" not in rule or value in array(rule["enum"]),
        "anyOf" not in rule or any(conforms(value, obj(branch)) for branch in array(rule["anyOf"])),
        scalar_matches(value, rule),
    ]
    if isinstance(value, dict):
        checks.append(object_matches(value, rule))
    if isinstance(value, list) and "items" in rule:
        checks.append(all(conforms(child, obj(rule["items"])) for child in value))
    return all(checks)


def validate_record(record: Object) -> None:
    if not conforms(record, schema()) or timestamp(record.get("timestamp")) is None:
        payload = obj(record.get("payload"))
        msg = f"Invalid rollout record: {record.get('type')}/{payload.get('type')}"
        raise ExportError(msg)


def read_standard(path: Path) -> list[Object]:
    records: list[Object] = []
    session_id = ""
    with path.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            if not line.endswith("\n"):
                msg = "Standard rollout must end with a newline"
                raise ExportError(msg)
            record = obj(decode(line))
            validate_record(record)
            if record.get("ordinal") != index:
                msg = "Non-contiguous rollout ordinal"
                raise ExportError(msg)
            payload = obj(record.get("payload"))
            if index == 0:
                if record.get("type") != "session_meta":
                    msg = "First record must be session_meta"
                    raise ExportError(msg)
                session_id = string(payload.get("id"))
            elif record.get("type") == "session_meta":
                msg = "Duplicate session_meta"
                raise ExportError(msg)
            if (
                record.get("type") == "event_msg"
                and payload.get("type") == "item_completed"
                and payload.get("thread_id") != session_id
            ):
                msg = "Item belongs to a different session"
                raise ExportError(msg)
            records.append(record)
    if not records:
        msg = "Empty rollout"
        raise ExportError(msg)
    return records


def render(session: Session, meta: Object, result: Conversion) -> tuple[list[Object], list[Object]]:
    created = (
        timestamp(meta.get("timestamp"))
        or timestamp(session.attributes.get("created_at_ms"), milliseconds=True)
        or timestamp(session.attributes.get("created_at"))
        or EPOCH
    )
    meta["timestamp"] = created
    records: list[Object] = [
        {"timestamp": created, "ordinal": 0, "type": "session_meta", "payload": meta}
    ]
    origins: list[Object] = [
        {"ordinal": 0, "rule": "session_metadata", "time_fallback": created == EPOCH}
    ]
    validate_record(records[0])
    for event in sorted(result.events, key=lambda entry: entry.order):
        record: Object = {
            "timestamp": event.time or created,
            "ordinal": len(records),
            "type": event.type,
            "payload": event.payload,
        }
        try:
            validate_record(record)
        except ExportError as error:
            result.error("unconverted_record", list(event.sources), str(error))
            continue
        records.append(record)
        origin: Object = {"ordinal": record["ordinal"], "sources": list(event.sources)}
        if event.time is None:
            origin["time_fallback"] = "epoch" if created == EPOCH else "session_created_at"
        origins.append(origin)
    return records, origins


def dialogue(records: list[Object]) -> list[Object]:
    return [
        obj(record.get("payload"))
        for record in records
        if record.get("type") == "response_item"
        and obj(record.get("payload")).get("type") == "message"
    ]


def operations(records: list[Object]) -> list[Object]:
    return [
        obj(obj(record.get("payload")).get("item"))
        for record in records
        if record.get("type") == "event_msg"
        and obj(record.get("payload")).get("type") == "item_completed"
        and obj(obj(record.get("payload")).get("item")).get("type")
        not in {"UserMessage", "AgentMessage", "Reasoning"}
    ]
