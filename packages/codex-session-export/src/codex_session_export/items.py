from __future__ import annotations

import re
import shlex
from pathlib import Path

from .models import Json, Object, array, obj, string


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def duration(value: Json) -> Object | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    nanos = round(value * 1_000_000)
    seconds, nanos = divmod(nanos, 1_000_000_000)
    return {"secs": seconds, "nanos": nanos}


def native_changes(value: Json) -> Object:
    changes: Object = {}
    for entry in array(value):
        change = obj(entry)
        kind = change.get("kind")
        kind_obj = obj(kind)
        change_type = string(kind_obj.get("type")) or string(kind)
        patch: Object = {"type": change_type, "unified_diff": change.get("diff")}
        if change_type == "update":
            patch["move_path"] = kind_obj.get("movePath")
        if change_type in {"add", "delete"}:
            # The display diff is retained verbatim, not treated as recovered file content.
            patch["content"] = None
        changes[string(change.get("path"))] = patch
    return changes


def command_fields(item: Object, result: Object) -> None:
    command = string(item.get("command"))
    result["command"] = list(shlex.split(command))
    cwd = string(item.get("cwd"))
    result["cwd"] = Path(cwd).as_uri() if Path(cwd).is_absolute() else cwd
    result.pop("command_actions", None)
    result["parsed_cmd"] = [
        {
            ("cmd" if key == "command" else snake(key)): snake(value)
            if key == "type" and isinstance(value, str)
            else value
            for key, value in obj(action).items()
        }
        for action in array(item.get("commandActions"))
    ]
    if isinstance(item.get("source"), str):
        result["source"] = snake(string(item["source"]))


def native_mcp_fields(item: Object, result: Object) -> None:
    # These native McpToolCall fields deliberately retain camel-case spellings.
    for key in ("pluginId", "readOnlyHint"):
        if key in item:
            result.pop(snake(key), None)
            result[key] = item[key]


def native_item(item: Object) -> Object:
    kind = string(item.get("type"))
    if not kind or kind[0].isupper():
        return dict(item)
    result: Object = {snake(key): value for key, value in item.items()}
    result["type"] = kind[0].upper() + kind[1:]
    if kind == "agentMessage":
        result.pop("text", None)
        result["content"] = [{"type": "Text", "text": item.get("text")}]
    elif kind == "reasoning":
        result.pop("summary", None)
        result.pop("content", None)
        result["summary_text"] = item.get("summary", [])
        result["raw_content"] = item.get("content", [])
    elif kind == "commandExecution":
        command_fields(item, result)
    elif kind == "fileChange":
        result["changes"] = native_changes(item.get("changes"))
    elif kind in {"webSearch", "plan", "sleep"}:
        result["type"] = "Extension"
        result["kind"] = {"webSearch": "web.search", "plan": "plan", "sleep": "sleep"}[kind]
    if "durationMs" in item:
        result.pop("duration_ms", None)
        result["duration"] = duration(item["durationMs"])
    if kind == "mcpToolCall":
        native_mcp_fields(item, result)
    return result


def same_duration(left: Object, right: Object) -> bool:
    if set(left) != {"secs", "nanos"} or set(right) != {"secs", "nanos"}:
        return False

    def milliseconds(value: Object) -> int | None:
        seconds, nanos = value.get("secs"), value.get("nanos")
        if isinstance(seconds, int) and isinstance(nanos, int):
            return seconds * 1000 + nanos // 1_000_000
        return None

    return milliseconds(left) is not None and milliseconds(left) == milliseconds(right)
