from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class _HeldLocks(threading.local):
    def __init__(self) -> None:
        self.paths: set[tuple[int, Path]] = set()


_held = _HeldLocks()


@contextmanager
def mutation_lock(state_home: Path) -> Generator[None]:
    home = state_home.expanduser().resolve()
    key = (os.getpid(), home)
    if key in _held.paths:
        yield
        return
    home.mkdir(parents=True, exist_ok=True)
    # Keep the lock file: unlinking it would let another process lock a new inode.
    descriptor = os.open(home / ".mutation.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _held.paths.add(key)
        try:
            yield
        finally:
            _held.paths.remove(key)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
