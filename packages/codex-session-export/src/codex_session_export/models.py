from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeAlias, cast

if TYPE_CHECKING:
    from pathlib import Path

Json: TypeAlias = "None | bool | int | float | str | list[Json] | dict[str, Json]"
Object: TypeAlias = dict[str, Json]
EPOCH = "1970-01-01T00:00:00.000Z"


class ExportError(ValueError):
    pass


def reject_constant(value: str) -> None:
    msg = f"Non-finite JSON number: {value}"
    raise ExportError(msg)


def decode(value: str | bytes) -> Json:
    return cast("Json", json.loads(value, parse_constant=reject_constant))


def obj(value: Json) -> Object:
    return value if isinstance(value, dict) else {}


def array(value: Json) -> list[Json]:
    return value if isinstance(value, list) else []


def string(value: Json) -> str:
    return value if isinstance(value, str) else ""


def integer(value: Json, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def encode(value: Json) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp(value: Json, *, milliseconds: bool = False) -> str | None:
    try:
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return None
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value / 1000 if milliseconds else value, UTC)
        else:
            return None
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


@dataclass
class Session:
    id: str
    attributes: Object = field(default_factory=dict[str, Json])
    files: list[Path] = field(default_factory=list["Path"])
    file_meta: dict[Path, Object] = field(default_factory=dict["Path", Object])
    cwd: str | None = None
    cwd_source: str | None = None
    matched_directories: list[str] = field(default_factory=list[str])
    warnings: list[Object] = field(default_factory=list[Object])


@dataclass
class Event:
    type: str
    payload: Object
    time: str | None
    order: tuple[int, int, int]
    sources: list[Object] = field(default_factory=list[Object])
    turn_id: str = ""


@dataclass
class Conversion:
    events: list[Event] = field(default_factory=list[Event])
    warnings: list[Object] = field(default_factory=list[Object])
    errors: list[Object] = field(default_factory=list[Object])
    missing: list[Json] = field(default_factory=list[Json])
    partial: bool = False

    def error(self, code: str, source: Json, message: str) -> None:
        self.partial = True
        self.errors.append({"code": code, "source": source, "message": message})
