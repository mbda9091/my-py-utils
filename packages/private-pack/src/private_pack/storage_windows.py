from __future__ import annotations

import ctypes
import msvcrt
import os
from contextlib import contextmanager
from ctypes import wintypes
from typing import TYPE_CHECKING, Any

from private_pack.manifest import PackError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from io import BufferedReader
    from pathlib import Path

# Imported only on Windows. Bind every signature: default ctypes integers truncate
# 64-bit handles. No additional runtime dependency is needed on other platforms.
win_dll = getattr(ctypes, "WinDLL")  # noqa: B009 - platform-specific typeshed
win_error: Callable[[int], OSError] = getattr(ctypes, "WinError")  # noqa: B009
last_error: Callable[[], int] = getattr(ctypes, "get_last_error")  # noqa: B009
open_osfhandle: Callable[[int, int], int] = getattr(msvcrt, "open_osfhandle")  # noqa: B009
kernel = win_dll("kernel32", use_last_error=True)
advapi = win_dll("advapi32", use_last_error=True)


def _bind(library: Any, name: str, result: Any, *arguments: Any) -> Any:  # noqa: ANN401
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", wintypes.LPVOID),
        ("inherit", wintypes.BOOL),
    ]


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("written", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


create_file = _bind(
    kernel,
    "CreateFileW",
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(SecurityAttributes),
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
close_handle = _bind(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
get_info = _bind(
    kernel,
    "GetFileInformationByHandle",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.POINTER(FileInformation),
)
create_directory = _bind(
    kernel, "CreateDirectoryW", wintypes.BOOL, wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)
)
local_free = _bind(kernel, "LocalFree", wintypes.HLOCAL, wintypes.HLOCAL)
get_process = _bind(kernel, "GetCurrentProcess", wintypes.HANDLE)
open_token = _bind(
    advapi,
    "OpenProcessToken",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
get_token_info = _bind(
    advapi,
    "GetTokenInformation",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
sid_to_string = _bind(
    advapi,
    "ConvertSidToStringSidW",
    wintypes.BOOL,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.LPWSTR),
)
parse_descriptor = _bind(
    advapi,
    "ConvertStringSecurityDescriptorToSecurityDescriptorW",
    wintypes.BOOL,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.LPVOID),
    ctypes.POINTER(wintypes.DWORD),
)
get_volume = _bind(
    kernel,
    "GetVolumeInformationW",
    wintypes.BOOL,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
)
get_drive_type = _bind(kernel, "GetDriveTypeW", wintypes.UINT, wintypes.LPCWSTR)
INVALID_HANDLE = ctypes.c_void_p(-1).value
REPARSE = 0x400
DIRECTORY = 0x10
DRIVE_FIXED = 3


def _checked(result: object) -> None:
    if not result:
        raise win_error(last_error())


def _path(path: Path) -> str:
    absolute = path.absolute()
    if absolute.drive.startswith("\\\\") or ":" in str(absolute)[2:]:
        msg = "UNC, device paths and alternate data streams are unsupported."
        raise PackError(msg)
    # Reject aliases Windows would otherwise silently normalize.
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if any(
        part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved
        for part in absolute.parts[1:]
    ):
        msg = "Unsupported Windows path component."
        raise PackError(msg)
    return "\\\\?\\" + str(absolute)


def _local_ntfs(path: Path) -> None:
    root = path.absolute().anchor
    filesystem = ctypes.create_unicode_buffer(32)
    flags = wintypes.DWORD()
    _checked(get_volume(root, None, 0, None, None, ctypes.byref(flags), filesystem, 32))
    if get_drive_type(root) != DRIVE_FIXED or filesystem.value != "NTFS" or not flags.value & 8:
        msg = "Private output requires a local NTFS volume with persistent ACLs."
        raise PackError(msg)


def _user_sid() -> str:
    token = wintypes.HANDLE()
    _checked(open_token(get_process(), 8, ctypes.byref(token)))  # TOKEN_QUERY
    try:
        size = wintypes.DWORD()
        get_token_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buffer = ctypes.create_string_buffer(size.value)
        _checked(get_token_info(token, 1, buffer, size, ctypes.byref(size)))
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        text = wintypes.LPWSTR()
        _checked(sid_to_string(sid, ctypes.byref(text)))
        try:
            return str(text.value)
        finally:
            local_free(ctypes.cast(text, wintypes.HLOCAL))
    finally:
        close_handle(token)


@contextmanager
def _security() -> Generator[SecurityAttributes]:
    descriptor = wintypes.LPVOID()
    # Protected DACL: only the current user and LocalSystem. Children inherit
    # these ACEs; no inherited grants from the destination parent survive.
    sddl = f"D:P(A;OICI;FA;;;{_user_sid()})(A;OICI;FA;;;SY)"
    _checked(parse_descriptor(sddl, 1, ctypes.byref(descriptor), None))
    try:
        yield SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, inherit=False)
    finally:
        local_free(descriptor)


def _open(path: Path, *, directory: bool) -> int:
    handle = create_file(
        _path(path),
        0x80 if directory else 0x80000000,
        3 if directory else 1,
        None,
        3,
        0x02200000,
        None,
    )
    if handle == INVALID_HANDLE:
        raise win_error(last_error())
    try:
        info = FileInformation()
        _checked(get_info(handle, ctypes.byref(info)))
        if info.attributes & REPARSE or bool(info.attributes & DIRECTORY) != directory:
            msg = "Symlink, junction, reparse point or unexpected file type is unsupported."
            raise PackError(msg)  # noqa: TRY301
    except BaseException:
        close_handle(handle)
        raise
    return int(handle)


@contextmanager
def _parents(path: Path) -> Generator[None]:
    handles: list[int] = []
    try:
        for parent in reversed(path.absolute().parents):
            # Omitting FILE_SHARE_DELETE prevents replacing held parent components.
            handles.append(_open(parent, directory=True))  # noqa: PERF401 - retain handles on error
        yield
    finally:
        for handle in reversed(handles):
            close_handle(handle)


@contextmanager
def open_source(root: Path, relative: str) -> Generator[BufferedReader]:
    path = root / relative
    _local_ntfs(path)
    with _parents(path):
        handle = _open(path, directory=False)
        try:
            fd = open_osfhandle(handle, os.O_RDONLY | 0x8000)
        except BaseException:
            close_handle(handle)
            raise
        # Ownership transfers to the CRT, then to the Python stream exactly once.
        with os.fdopen(fd, "rb") as stream:
            yield stream


def write_private(path: Path, data: bytes) -> None:
    _local_ntfs(path)
    with _parents(path), _security() as security:
        handle = create_file(
            _path(path), 0x40000000, 0, ctypes.byref(security), 1, 0x80, None
        )  # CREATE_NEW
        if handle == INVALID_HANDLE:
            raise win_error(last_error())
        try:
            try:
                fd = open_osfhandle(handle, os.O_WRONLY | 0x8000)
            except BaseException:
                close_handle(handle)
                raise
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def make_private_directory(path: Path) -> None:
    _local_ntfs(path)
    with _parents(path), _security() as security:
        _checked(create_directory(_path(path), ctypes.byref(security)))


def commit_directory(source: Path, target: Path) -> None:
    # Windows os.rename fails if any destination exists and never copies volumes.
    with _parents(source), _parents(target):
        os.rename(_path(source), _path(target))  # noqa: PTH104 - extended paths
