from __future__ import annotations

import ctypes
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from private_pack.manifest import KEY_SIZE, PackError

if TYPE_CHECKING:
    from collections.abc import Generator
    from io import BufferedReader


def require_linux() -> None:
    if sys.platform != "linux":
        msg = "Packing currently requires Linux with renameat2 support."
        raise PackError(msg)


def commit_directory(source: Path, target: Path) -> None:
    require_linux()
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        msg = "This system does not provide renameat2; output was not committed."
        raise PackError(msg) from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # AT_FDCWD=-100; RENAME_NOREPLACE=1. Never fall back to an overwriting rename.
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_key(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            msg = "Key must be a regular file."
            raise PackError(msg)
        key = stream.read(KEY_SIZE + 1)
    if len(key) != KEY_SIZE:
        msg = "Key file must contain exactly 32 bytes."
        raise PackError(msg)
    return key


@contextmanager
def open_source(root: Path, relative: str) -> Generator[BufferedReader]:
    """Walk every component without following links, including the input root."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
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
