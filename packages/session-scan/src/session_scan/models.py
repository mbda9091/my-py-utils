from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias, cast

Json: TypeAlias = "None | bool | int | float | str | list[Json] | dict[str, Json]"
Object: TypeAlias = dict[str, Json]
Source: TypeAlias = Literal["codex", "dsh"]


def obj(value: Json) -> Object:
    return value if isinstance(value, dict) else {}


def text(value: Json) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def natural(value: Json) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))  # noqa: PTH100 - lexical cwd


def absolute(value: Json) -> str | None:
    value = text(value)
    return str(normalize(value)) if value and Path(value).is_absolute() else None


def _invalid_constant(value: str) -> None:
    raise ValueError(value)


def decode(raw: bytes) -> Object:
    value = cast("Json", json.loads(raw, parse_constant=_invalid_constant))
    if not isinstance(value, dict):
        msg = "Record must be a JSON object"
        raise TypeError(msg)
    return value


def timestamp(value: Json, *, milliseconds: bool = False) -> datetime | None:
    try:
        if isinstance(value, str) and not milliseconds:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(UTC) if parsed.tzinfo else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return datetime.fromtimestamp(value / 1000 if milliseconds else value, UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None


@dataclass(frozen=True)
class Issue:
    code: str
    path: Path
    message: str
    line_number: int | None = None

    def as_json(self) -> Object:
        return {
            "code": self.code,
            "path": str(self.path),
            "message": self.message,
            "line_number": self.line_number,
        }


class ScanError(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass(frozen=True)
class Header:
    source: Source
    path: Path
    session_id: str
    data: Object
    version: int | None = None


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    timestamp: datetime
    line_number: int
    position: int | None
    time_source: str


@dataclass
class SessionFile:
    header: Header
    cwd: str | None
    cwd_source: str | None
    title: str | None
    updated_at: datetime
    updated_at_source: str

    def as_json(self) -> Object:
        return {
            "source": self.header.source,
            "session_id": self.header.session_id,
            "path": str(self.header.path),
            "cwd": self.cwd,
            "cwd_source": self.cwd_source,
            "title": self.title,
            "updated_at": self.updated_at.isoformat(),
            "updated_at_source": self.updated_at_source,
            "match": None,
        }


@dataclass
class Discovery:
    files: list[SessionFile] = field(default_factory=list[SessionFile])
    errors: list[Issue] = field(default_factory=list[Issue])
    warnings: list[Issue] = field(default_factory=list[Issue])
    candidates: int = 0
    available_roots: int = 0
