from __future__ import annotations

import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from private_pack.archive import create_archive
from private_pack.manifest import (
    KEY_SIZE,
    MAX_MANIFEST_SIZE,
    PackError,
    decrypt_manifest,
    encode_manifest,
    encrypt_manifest,
)
from private_pack.planning import inventory, make_plan, validate_paths
from private_pack.storage import commit_directory, read_key, require_linux, write_private

if TYPE_CHECKING:
    from collections.abc import Callable


def keygen(output: Path) -> None:
    write_private(output, secrets.token_bytes(KEY_SIZE))


def decrypt_config(source: Path, key_file: Path, output: Path) -> None:
    key = read_key(key_file)
    with source.open("rb") as stream:
        blob = stream.read(MAX_MANIFEST_SIZE + 128)
    manifest = decrypt_manifest(blob, key)
    write_private(output, encode_manifest(manifest))


def pack(
    source: Path,
    output: Path,
    key_file: Path,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    require_linux()
    root, target = validate_paths(source, output, key_file)
    key = read_key(key_file)
    snapshot = inventory(root)
    key_info = key_file.stat()
    if any((s.device, s.inode) == (key_info.st_dev, key_info.st_ino) for s in snapshot.values()):
        msg = "Input contains a hard link to the key file."
        raise PackError(msg)
    manifest = make_plan(snapshot)
    with TemporaryDirectory(prefix=".private-pack-", dir=target.parent) as temporary:
        staging = Path(temporary) / "batch"
        staging.mkdir(mode=0o700)
        for index, entry in enumerate(manifest.entries, 1):
            create_archive(root, staging, entry, manifest.archive_password, snapshot)
            if progress is not None:
                progress(index, len(manifest.entries))
        if inventory(root) != snapshot:
            msg = "Input changed during packing; batch was not committed."
            raise PackError(msg)
        blob = encrypt_manifest(manifest, key)
        config = staging / "manifest.enc"
        write_private(config, blob)
        if decrypt_manifest(config.read_bytes(), key) != manifest:
            msg = "Encrypted manifest verification failed."
            raise PackError(msg)
        commit_directory(staging, target)
    return len(manifest.entries)
