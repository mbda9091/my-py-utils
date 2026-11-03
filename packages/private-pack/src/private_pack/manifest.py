from __future__ import annotations

import json
import re
import secrets
import string
from dataclasses import asdict, dataclass, field
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADER = b"PRIVATEPACK\x01"
KEY_SIZE = 32
NONCE_SIZE = 12
TAG_SIZE = 16
PASSWORD_LENGTH = 20
PADDING_SIZE = 16
MAX_MANIFEST_SIZE = 16 * 1024 * 1024


class PackError(Exception):
    """An expected operation failure with a safe, non-secret message."""


@dataclass
class Padding:
    archive_path: str
    size_bytes: int = PADDING_SIZE


@dataclass
class Entry:
    source: str
    kind: str
    original_archive_name: str
    archive_name: str
    padding: Padding
    archive_size: int | None = None
    archive_sha256: str | None = None


@dataclass
class Manifest:
    archive_password: str = field(repr=False)
    entries: list[Entry]
    version: int = 1
    archive_format: str = "7z"


def valid_password(value: str) -> bool:
    return (
        len(value) == PASSWORD_LENGTH
        and all(c in string.ascii_letters + string.digits for c in value)
        and any(c in string.ascii_lowercase for c in value)
        and any(c in string.ascii_uppercase for c in value)
        and any(c in string.digits for c in value)
    )


def make_password() -> str:
    while True:
        value = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(PASSWORD_LENGTH)
        )
        if valid_password(value):
            return value


def safe_name(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and not any(c in value for c in ("/", "\\", "\x00"))
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = "Invalid manifest structure."
        raise PackError(msg)
    return cast("dict[str, object]", value)


def _string(data: dict[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        msg = "Invalid manifest field."
        raise PackError(msg)
    return value


def _entry(value: object) -> Entry:
    data = _object(value)
    padding = _object(data.get("padding"))
    entry = Entry(
        source=_string(data, "source"),
        kind=_string(data, "kind"),
        original_archive_name=_string(data, "original_archive_name"),
        archive_name=_string(data, "archive_name"),
        padding=Padding(_string(padding, "archive_path")),
    )
    size = data.get("archive_size")
    digest = _string(data, "archive_sha256")
    valid = (
        safe_name(entry.source)
        and entry.kind in {"file", "directory"}
        and entry.original_archive_name == entry.source + ".7z"
        and re.fullmatch(r"[0-9a-f]{32}\.7z", entry.archive_name) is not None
        and safe_name(entry.padding.archive_path)
        and entry.padding.archive_path != entry.source
        and type(padding.get("size_bytes")) is int
        and padding.get("size_bytes") == PADDING_SIZE
        and type(size) is int
        and size > 0
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )
    if not valid:
        msg = "Invalid manifest entry."
        raise PackError(msg)
    entry.archive_size = cast("int", size)
    entry.archive_sha256 = digest
    return entry


def decode_manifest(raw: bytes) -> Manifest:
    try:
        data = _object(cast("object", json.loads(raw)))
    except (ValueError, UnicodeError, RecursionError) as exc:
        msg = "Invalid manifest JSON."
        raise PackError(msg) from exc
    if type(data.get("version")) is not int or data.get("version") != 1:
        msg = "Unsupported manifest version."
        raise PackError(msg)
    password = _string(data, "archive_password")
    values = data.get("entries")
    if data.get("archive_format") != "7z" or not valid_password(password):
        msg = "Invalid manifest settings."
        raise PackError(msg)
    if not isinstance(values, list) or not values:
        msg = "Manifest must contain entries."
        raise PackError(msg)
    entries = [_entry(value) for value in cast("list[object]", values)]
    if len({e.source for e in entries}) != len(entries) or len(
        {e.archive_name for e in entries}
    ) != len(entries):
        msg = "Duplicate manifest entries."
        raise PackError(msg)
    return Manifest(password, entries)


def encode_manifest(manifest: Manifest) -> bytes:
    raw = json.dumps(asdict(manifest), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if len(raw) > MAX_MANIFEST_SIZE:
        msg = "Manifest exceeds the 16 MiB limit."
        raise PackError(msg)
    decode_manifest(raw)
    return raw


def _check_key(key: bytes) -> None:
    if len(key) != KEY_SIZE:
        msg = "Key file must contain exactly 32 bytes."
        raise PackError(msg)


def encrypt_manifest(manifest: Manifest, key: bytes) -> bytes:
    _check_key(key)
    nonce = secrets.token_bytes(NONCE_SIZE)
    return HEADER + nonce + AESGCM(key).encrypt(nonce, encode_manifest(manifest), HEADER)


def decrypt_manifest(blob: bytes, key: bytes) -> Manifest:
    _check_key(key)
    overhead = len(HEADER) + NONCE_SIZE + TAG_SIZE
    if not overhead <= len(blob) <= MAX_MANIFEST_SIZE + overhead:
        msg = "Invalid encrypted manifest length."
        raise PackError(msg)
    if not blob.startswith(HEADER):
        msg = "Unsupported encrypted manifest format or version."
        raise PackError(msg)
    nonce = blob[len(HEADER) : len(HEADER) + NONCE_SIZE]
    try:
        raw = AESGCM(key).decrypt(nonce, blob[len(HEADER) + NONCE_SIZE :], HEADER)
    except InvalidTag as exc:
        msg = "Wrong key or damaged encrypted manifest."
        raise PackError(msg) from exc
    return decode_manifest(raw)
