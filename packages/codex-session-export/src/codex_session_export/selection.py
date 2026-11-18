from __future__ import annotations

import os
from pathlib import Path

from .models import Session, string


def normalize(value: str) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))  # noqa: PTH100 - lexical history paths


def select_cwd(session: Session) -> None:
    candidates: list[tuple[str, str]] = [("state_db", string(session.attributes.get("cwd")))]
    candidates.extend(
        (str(path), string(session.file_meta[path].get("cwd"))) for path in session.files
    )
    valid = [
        (source, str(normalize(value))) for source, value in candidates if Path(value).is_absolute()
    ]
    if not valid:
        return
    session.cwd_source, session.cwd = valid[0]
    if len({value for _, value in valid}) > 1:
        session.warnings.append(
            {
                "code": "cwd_conflict",
                "candidates": [{"source": source, "cwd": value} for source, value in valid],
            }
        )


def matches(session: Session, directories: list[Path], *, exact: bool = False) -> bool:
    if not directories:
        return True
    if session.cwd is None:
        return False
    cwd = Path(session.cwd)
    session.matched_directories = [
        str(directory)
        for directory in directories
        if cwd == directory or (not exact and cwd.is_relative_to(directory))
    ]
    return bool(session.matched_directories)
