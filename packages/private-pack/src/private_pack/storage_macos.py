from __future__ import annotations

import ctypes
import errno
import os
from typing import TYPE_CHECKING

from private_pack.manifest import PackError

if TYPE_CHECKING:
    from pathlib import Path


def commit_directory(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renamex_np
    except AttributeError as exc:
        msg = "Exclusive rename is unavailable; output was not committed."
        raise PackError(msg) from exc
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # RENAME_EXCL, not RENAME_SWAP or an ordinary overwriting rename.
    if rename(os.fsencode(source), os.fsencode(target), 4) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def check_parent_acl(fd: int) -> None:
    # Fail before creating sensitive data under any extended ACL. This deliberately
    # rejects even benign ACLs instead of guessing the effect of inherited ACEs.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    acl = libc.acl_get_fd_np(fd, 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        error = ctypes.get_errno()
        # A valid held descriptor cannot have a missing pathname. Darwin's
        # filesec_get_property uses ENOENT for an absent FILESEC_ACL property.
        if error == errno.ENOENT:
            return
        raise OSError(error, os.strerror(error))
    try:
        entry = ctypes.c_void_p()
        # Darwin returns 0 on finding an entry, -1 when no entry exists.
        if libc.acl_get_entry(acl, 0, ctypes.byref(entry)) == 0:
            msg = "Private output parent has an extended ACL; choose a directory without ACLs."
            raise PackError(msg)
    finally:
        libc.acl_free(acl)
