from __future__ import annotations

import ctypes
import os
from typing import TYPE_CHECKING

from private_pack.manifest import PackError

if TYPE_CHECKING:
    from pathlib import Path


def commit_directory(source: Path, target: Path) -> None:
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
