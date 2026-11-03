from __future__ import annotations

import hashlib
import os
import secrets
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import py7zr

from private_pack.manifest import PADDING_SIZE, PackError
from private_pack.planning import Snapshot
from private_pack.storage import open_source

if TYPE_CHECKING:
    from private_pack.manifest import Entry


def create_archive(
    root: Path,
    staging: Path,
    entry: Entry,
    password: str,
    snapshot: dict[str, Snapshot],
) -> None:
    with TemporaryDirectory(prefix=".item-", dir=staging) as temporary:
        scratch = Path(temporary)
        original = scratch / entry.original_archive_name
        empty_directory = scratch / "directory"
        empty_directory.mkdir()
        # Explicit AES filter avoids relying on password/filter default interactions.
        filters = [{"id": py7zr.FILTER_LZMA2}, {"id": py7zr.FILTER_CRYPTO_AES256_SHA256}]
        with py7zr.SevenZipFile(
            original,
            "x",
            password=password,
            header_encryption=True,
            filters=filters,
        ) as archive:
            for relative, before in snapshot.items():
                if relative != entry.source and not relative.startswith(entry.source + "/"):
                    continue
                if stat.S_ISDIR(before.mode):
                    # A synthetic empty directory prevents recursive traversal or link following.
                    archive.write(empty_directory, arcname=relative)
                else:
                    with open_source(root, relative) as stream:
                        if Snapshot.from_stat(os.fstat(stream.fileno())) != before:
                            msg = "Source changed during packing."
                            raise PackError(msg)
                        archive.writef(stream, arcname=relative)
                        if Snapshot.from_stat(os.fstat(stream.fileno())) != before:
                            msg = "Source changed during packing."
                            raise PackError(msg)
            archive.writestr(secrets.token_bytes(PADDING_SIZE), entry.padding.archive_path)
        target = staging / entry.archive_name
        original.rename(target)
        entry.archive_size = target.stat().st_size
        with target.open("rb") as stream:
            entry.archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
