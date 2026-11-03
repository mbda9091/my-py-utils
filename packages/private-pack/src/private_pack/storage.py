from __future__ import annotations

import secrets
import shutil
import stat
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from private_pack.manifest import KEY_SIZE, PackError

if TYPE_CHECKING:
    import os
    from collections.abc import Generator
    from pathlib import Path

if sys.platform == "win32":
    from private_pack.storage_windows import (
        commit_directory,
        make_private_directory,
        open_source,
        write_private,
    )
else:
    from private_pack.storage_posix import make_private_directory, open_source, write_private

    if sys.platform == "darwin":
        from private_pack.storage_macos import commit_directory
    else:
        from private_pack.storage_linux import commit_directory

__all__ = [
    "check_capabilities",
    "commit_directory",
    "is_link",
    "make_private_directory",
    "open_source",
    "private_temp_directory",
    "read_key",
    "write_private",
]


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def read_key(path: Path) -> bytes:
    with open_source(path.absolute().parent, path.name) as stream:
        key = stream.read(KEY_SIZE + 1)
    if len(key) != KEY_SIZE:
        msg = "Key file must contain exactly 32 bytes."
        raise PackError(msg)
    return key


@contextmanager
def private_temp_directory(parent: Path, prefix: str = ".private-pack-") -> Generator[Path]:
    path = parent / (prefix + secrets.token_hex(16))
    make_private_directory(path)
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            msg = f"Could not remove private temporary directory: {path}"
            raise PackError(msg) from exc


def check_capabilities(parent: Path) -> None:
    if sys.platform not in {"linux", "darwin", "win32"}:
        msg = "Supported platforms are Linux, macOS and Windows."
        raise PackError(msg)
    with private_temp_directory(parent) as probe:
        source, target = probe / "source", probe / "target"
        make_private_directory(source)
        make_private_directory(target)
        try:
            commit_directory(source, target)
        except FileExistsError:
            pass
        else:
            msg = "Filesystem does not support exclusive directory publication."
            raise PackError(msg)
        target.rmdir()
        commit_directory(source, target)
