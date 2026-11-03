from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from private_pack.manifest import PackError
from private_pack.storage_macos import check_parent_acl as check_macos_acl

if TYPE_CHECKING:
    from collections.abc import Generator
    from io import BufferedReader


def write_private(path: Path, data: bytes) -> None:
    with _directory(path.absolute().parent) as directory:
        check_parent_acl(directory)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(path.name, dir_fd=directory)
            raise


@contextmanager
def _directory(path: Path) -> Generator[int]:
    absolute = path.absolute()
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory
    finally:
        os.close(directory)


@contextmanager
def open_source(root: Path, relative: str) -> Generator[BufferedReader]:
    """Walk every component without following links, including the input root."""
    absolute = root.absolute()
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = (*absolute.parts[1:], *Path(relative).parts)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                msg = "Source file type changed during packing."
                raise PackError(msg)
            yield stream
    finally:
        os.close(directory)


def check_parent_acl(fd: int) -> None:
    # macOS inherited allow ACEs are not bounded by the POSIX mode mask.
    if os.uname().sysname == "Darwin":
        check_macos_acl(fd)


def make_private_directory(path: Path) -> None:
    with _directory(path.absolute().parent) as directory:
        check_parent_acl(directory)
        os.mkdir(path.name, mode=0o700, dir_fd=directory)
