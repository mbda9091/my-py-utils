from __future__ import annotations

import secrets
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

from private_pack.manifest import Entry, Manifest, PackError, Padding, make_password, safe_name

if TYPE_CHECKING:
    import os
    from pathlib import Path


@dataclass(frozen=True)
class Snapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified: int
    changed: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> Snapshot:
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )


def inventory(root: Path) -> dict[str, Snapshot]:
    result: dict[str, Snapshot] = {}

    def visit(path: Path) -> None:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            msg = "Input contains a symlink or special file."
            raise PackError(msg)
        relative = path.relative_to(root).as_posix()
        if path != root and not safe_name(path.name):
            msg = "Input contains an unsupported filename."
            raise PackError(msg)
        result[relative] = Snapshot.from_stat(info)
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return result


def make_plan(snapshot: dict[str, Snapshot]) -> Manifest:
    entries: list[Entry] = []
    used: set[str] = set()
    for source in sorted(name for name in snapshot if name != "." and "/" not in name):
        name = secrets.token_hex(16) + ".7z"
        while name in used:
            name = secrets.token_hex(16) + ".7z"
        used.add(name)
        padding = "_padding_" + secrets.token_hex(16) + ".bin"
        while padding == source:
            padding = "_padding_" + secrets.token_hex(16) + ".bin"
        kind = "directory" if stat.S_ISDIR(snapshot[source].mode) else "file"
        entries.append(Entry(source, kind, source + ".7z", name, Padding(padding)))
    if not entries:
        msg = "Input directory is empty."
        raise PackError(msg)
    return Manifest(make_password(), entries)


def validate_paths(source: Path, output: Path, key_file: Path) -> tuple[Path, Path]:
    if source.is_symlink() or not source.is_dir():
        msg = "Input must be a directory, not a symlink."
        raise PackError(msg)
    root = source.resolve(strict=True)
    if output.is_symlink() or output.exists():
        msg = "Output already exists."
        raise PackError(msg)
    target = output.parent.resolve(strict=True) / output.name
    if not safe_name(output.name) or target.is_relative_to(root):
        msg = "Output must be outside the input directory."
        raise PackError(msg)
    key_path = key_file.resolve(strict=True)
    if key_path.is_relative_to(root):
        msg = "Key file must be outside the input directory."
        raise PackError(msg)
    key_info = key_path.stat()
    if not stat.S_ISREG(key_info.st_mode):
        msg = "Key file must be a regular file."
        raise PackError(msg)
    return root, target
