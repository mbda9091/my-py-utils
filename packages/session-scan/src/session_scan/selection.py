from __future__ import annotations

import re
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .messages import iter_messages
from .models import Issue, Message, ScanError, normalize
from .reader import SessionReader

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .models import Discovery, Json, Object, SessionFile

DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _boundary(value: str | None, *, upper: bool) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, None
    value = value.strip()
    try:
        if DATE.fullmatch(value):
            day = date.fromisoformat(value)
            if upper:
                day += timedelta(days=1)
            # Convert each local calendar boundary independently, including its DST offset.
            local = datetime.combine(day, time.min).astimezone()
            return local.astimezone(UTC), local.isoformat()
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            return stamp.astimezone(UTC), None
    except (ValueError, OverflowError, OSError):
        pass
    msg = "Time must be YYYY-MM-DD or an ISO 8601 datetime with a timezone"
    raise ValueError(msg)


@dataclass(frozen=True)
class Filters:
    directories: tuple[Path, ...] = ()
    exact: bool = False
    since: datetime | None = None
    until: datetime | None = None
    local_date_boundaries: dict[str, Json] = field(default_factory=dict[str, "Json"])

    @property
    def timed(self) -> bool:
        return self.since is not None or self.until is not None

    def directory_reason(self, cwd: str | None) -> str | None:
        if not self.directories:
            return None
        if cwd is None:
            return "unknown_cwd_excluded_files"
        path = Path(cwd)
        if any(
            path == directory or (not self.exact and path.is_relative_to(directory))
            for directory in self.directories
        ):
            return None
        return "directory_excluded_files"

    def contains(self, message: Message) -> bool:
        return (self.since is None or message.timestamp >= self.since) and (
            self.until is None or message.timestamp < self.until
        )

    def as_json(self) -> Object:
        return {
            "directories": [str(path) for path in self.directories],
            "directory_match": "exact" if self.exact else "descendants",
            "since": self.since.isoformat() if self.since is not None else None,
            "until": self.until.isoformat() if self.until is not None else None,
            "local_date_boundaries": self.local_date_boundaries,
        }


def compile_filters(
    directories: Sequence[str] = (),
    *,
    exact: bool = False,
    since: str | None = None,
    until: str | None = None,
) -> Filters:
    if any(not value.strip() for value in directories):
        msg = "Directories must not be empty"
        raise ValueError(msg)
    if exact and not directories:
        msg = "--exact-directory requires --directory"
        raise ValueError(msg)
    lower, local_lower = _boundary(since, upper=False)
    upper, local_upper = _boundary(until, upper=True)
    if lower is not None and upper is not None and lower >= upper:
        msg = "--since must be earlier than the effective --until boundary"
        raise ValueError(msg)
    locals_: dict[str, Json] = {}
    if local_lower is not None:
        locals_["since"] = local_lower
    if local_upper is not None:
        locals_["until"] = local_upper
    return Filters(
        tuple(dict.fromkeys(normalize(path) for path in directories)), exact, lower, upper, locals_
    )


@dataclass
class _TimeScan:
    message: Message | None = None
    errors: list[Issue] = field(default_factory=list[Issue])
    started: bool = False
    complete: bool = False


def _scan_time(session: SessionFile, filters: Filters) -> _TimeScan:
    result = _TimeScan()
    try:
        with SessionReader(session.header.path, session.header.source) as reader:
            if reader.header != session.header:
                result.errors.append(
                    Issue("source_changed", session.header.path, "Header changed after discovery")
                )
                return result
            result.started = True
            with closing(iter_messages(reader)) as messages:
                result.message = next(
                    (message for message in messages if filters.contains(message)), None
                )
            result.errors.extend(reader.errors)
            result.complete = reader.complete
    except ScanError as error:
        result.errors.append(error.issue)
    except OSError:
        result.errors.append(
            Issue("file_read_error", session.header.path, "Cannot scan message times")
        )
    return result


def _match_json(message: Message, *, partial: bool, source: str) -> Object:
    result: Object = {
        "timestamp": message.timestamp.isoformat(),
        "time_source": message.time_source,
        "line_number": message.line_number,
        "scan_state": "first_match_with_errors" if partial else "first_match",
    }
    if message.position is not None:
        result["ordinal" if source == "codex" else "seq"] = message.position
    return result


def select_files(
    found: Discovery, filters: Filters
) -> tuple[list[Object], dict[str, int], list[Issue]]:
    summary = {
        "candidate_files": found.candidates,
        "valid_files": len(found.files),
        "matched_files": 0,
        "directory_excluded_files": 0,
        "unknown_cwd_excluded_files": 0,
        "time_excluded_files": 0,
        "indeterminate_time_files": 0,
        "header_only_files": len(found.files),
        "body_scanned_files": 0,
        "early_stopped_files": 0,
        "error_count": 0,
    }
    errors = list(found.errors)
    selected: list[tuple[datetime, str, str, Object]] = []
    for session in found.files:
        reason = filters.directory_reason(session.cwd)
        if reason is not None:
            summary[reason] += 1
            continue
        row = session.as_json()
        sort_time = session.updated_at
        if filters.timed:
            scan = _scan_time(session, filters)
            errors.extend(scan.errors)
            summary["body_scanned_files"] += int(scan.started)
            summary["header_only_files"] -= int(scan.started)
            summary["indeterminate_time_files"] += int(bool(scan.errors))
            if scan.message is None:
                if scan.complete and not scan.errors:
                    summary["time_excluded_files"] += 1
                continue
            summary["early_stopped_files"] += 1
            row["match"] = _match_json(
                scan.message, partial=bool(scan.errors), source=session.header.source
            )
            sort_time = scan.message.timestamp
        selected.append((sort_time, session.header.source, str(session.header.path), row))
    selected.sort(key=lambda item: (-item[0].timestamp(), item[1], item[2]))
    summary["matched_files"] = len(selected)
    summary["error_count"] = len(errors)
    return [item[3] for item in selected], summary, errors
