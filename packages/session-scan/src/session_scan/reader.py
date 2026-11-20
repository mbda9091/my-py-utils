from __future__ import annotations

import io
import os
import re
from contextlib import ExitStack
from typing import TYPE_CHECKING, Never, Self, cast

import zstandard

from .models import Header, Issue, ScanError, decode, natural, obj, text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from types import TracebackType
    from typing import BinaryIO

    from _typeshed import WriteableBuffer

    from .models import Object, Source

HEADER_LIMIT = 1024 * 1024
RECORD_LIMIT = 16 * 1024 * 1024
BUFFER_SIZE = 8192
DSH_NAME = re.compile(r"session(?:\.v(\d+))?\.jsonl(?:\.zstd)?\Z")
DSH_VERSIONS = {0, 3, 4}
ZSTD_MAGIC = 0xFD2FB528
SKIPPABLE_MIN = 0x184D2A50
SKIPPABLE_MAX = 0x184D2A5F
RESERVED_BLOCK = 3


class _Snapshot(io.RawIOBase):
    def __init__(self, raw: BinaryIO, size: int) -> None:
        self.raw = raw
        self.remaining = size

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: WriteableBuffer) -> int:
        view = memoryview(buffer).cast("B")
        data = self.raw.read(min(len(view), self.remaining))
        view[: len(data)] = data
        self.remaining -= len(data)
        return len(data)


def _skip_blocks(take: Callable[[int], bytes], skip: Callable[[int], None]) -> None:
    while True:
        block = int.from_bytes(take(3), "little")
        kind = (block >> 1) & 3
        if kind == RESERVED_BLOCK:
            msg = "Invalid Zstandard block type"
            raise ValueError(msg)
        skip(1 if kind == 1 else block >> 3)
        if block & 1:
            return


def _check_frames(raw: BinaryIO, size: int) -> None:
    """Check frame boundaries at EOF: stream_reader permits an incomplete final frame."""
    raw.seek(0)

    def take(count: int) -> bytes:
        if raw.tell() + count > size:
            msg = "Incomplete Zstandard frame"
            raise ValueError(msg)
        data = raw.read(count)
        if len(data) != count:
            msg = "Compressed source was truncated"
            raise ValueError(msg)
        return data

    def skip(count: int) -> None:
        if raw.tell() + count > size:
            msg = "Incomplete Zstandard frame"
            raise ValueError(msg)
        raw.seek(count, io.SEEK_CUR)

    while raw.tell() < size:
        magic = int.from_bytes(take(4), "little")
        if SKIPPABLE_MIN <= magic <= SKIPPABLE_MAX:
            skip(int.from_bytes(take(4), "little"))
            continue
        if magic != ZSTD_MAGIC:
            msg = "Invalid Zstandard frame magic"
            raise ValueError(msg)
        descriptor = take(1)[0]
        single = bool(descriptor & 0x20)
        content_size = (1 if single else 0, 2, 4, 8)[descriptor >> 6]
        skip((0 if single else 1) + (0, 1, 2, 4)[descriptor & 3] + content_size)
        _skip_blocks(take, skip)
        if descriptor & 4:
            skip(4)


class SessionReader:
    """Read a header immediately; inspect messages only when records() is advanced."""

    def __init__(self, path: Path, source: Source) -> None:
        self.path = path
        self.source: Source = source
        self.errors: list[Issue] = []
        self.complete = False
        self.line_number = 0
        self._stack = ExitStack()
        self._raw: BinaryIO | None = None
        self._stream: io.BufferedIOBase | None = None
        self._initial: os.stat_result | None = None
        self._iterated = False
        self.header: Header | None = None

    def __enter__(self) -> Self:
        try:
            self._raw = self._stack.enter_context(self.path.open("rb"))
            self._initial = os.fstat(self._raw.fileno())
            row = self._line(HEADER_LIMIT, self._open_stream())
            if row is None:
                self._fail("invalid_header", "Missing session header")
            self.header = self._header(row)
        except (OSError, ValueError, zstandard.ZstdError) as error:
            self._stack.close()
            raise ScanError(
                Issue("header_read_error", self.path, "Cannot read session header")
            ) from error
        except BaseException:
            self._stack.close()
            raise
        else:
            return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stack.close()

    def _fail(self, code: str, message: str) -> Never:
        raise ScanError(Issue(code, self.path, message, self.line_number))

    def _open_stream(self) -> bytes | None:
        if self._raw is None or self._initial is None:
            msg = "Reader is not open"
            raise RuntimeError(msg)
        bounded = _Snapshot(self._raw, self._initial.st_size)
        if not self.path.name.endswith(".zstd"):
            self._stream = self._stack.enter_context(io.BufferedReader(bounded, BUFFER_SIZE))
            return None
        decoder = self._stack.enter_context(
            zstandard.ZstdDecompressor().stream_reader(
                cast("BinaryIO", bounded),
                read_size=BUFFER_SIZE,
                read_across_frames=True,
                closefd=False,
            )
        )
        # Do not fill a decoded 8 KiB buffer across the independent DSH header frame.
        first = bytearray()
        while len(first) <= HEADER_LIMIT:
            byte = decoder.read(1)
            if not byte:
                break
            first.extend(byte)
            if byte == b"\n":
                break
        self._stream = self._stack.enter_context(io.BufferedReader(decoder, BUFFER_SIZE))
        return bytes(first)

    def _line(self, limit: int, first: bytes | None = None) -> Object | None:
        if self._stream is None:
            msg = "Reader is not open"
            raise RuntimeError(msg)
        raw = self._stream.readline(limit + 1) if first is None else first
        if not raw:
            return None
        self.line_number += 1
        if len(raw) > limit:
            if self.line_number == 1:
                self._fail("header_too_large", "Session header exceeds the decoded size limit")
            while not raw.endswith(b"\n"):
                raw = self._stream.readline(BUFFER_SIZE)
                if not raw:
                    break
            self._fail("record_too_large", "JSONL record exceeds the decoded size limit")
        try:
            return decode(raw)
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            code = "invalid_record" if raw.endswith(b"\n") else "truncated_record"
            raise ScanError(
                Issue(code, self.path, "Invalid JSON object", self.line_number)
            ) from error

    def _header(self, row: Object) -> Header:
        if self.source == "codex":
            data = obj(row.get("payload"))
            if row.get("type") != "session_meta":
                self._fail("invalid_header", "Expected Codex session_meta")
            version = None
        else:
            data = row
            version = natural(row.get("version"))
            match = DSH_NAME.fullmatch(self.path.name)
            if row.get("type") != "session" or match is None:
                self._fail("invalid_header", "Expected a DSH session header and filename")
            if version not in DSH_VERSIONS:
                self._fail("unsupported_version", "Unsupported DSH session version")
            if match and int(match.group(1) or 0) != version:
                self._fail("version_conflict", "DSH filename and header versions differ")
            if "isSeeded" in data and not isinstance(data["isSeeded"], bool):
                self._fail("invalid_header", "DSH isSeeded must be boolean")
            if "seedLength" in data and natural(data["seedLength"]) is None:
                self._fail("invalid_header", "DSH seedLength must be a nonnegative integer")
        session_id = text(data.get("id"))
        if session_id is None:
            self._fail("invalid_header", "Missing session ID")
        return Header(self.source, self.path, session_id, data, version)

    def records(self) -> Iterator[tuple[int, Object]]:
        if self._iterated:
            msg = "Each reader supports one lazy record iteration"
            raise RuntimeError(msg)
        self._iterated = True
        try:
            while True:
                try:
                    row = self._line(RECORD_LIMIT)
                except ScanError as error:
                    self.errors.append(error.issue)
                    continue
                if row is None:
                    self._finish()
                    return
                yield self.line_number, row
        except (OSError, ValueError, zstandard.ZstdError):
            self.errors.append(
                Issue("record_read_error", self.path, "Cannot decode session records")
            )

    def rewind(self) -> None:
        """Reuse the same bounded source after a seeded-history boundary prepass."""
        if not self.complete or self._raw is None or self._initial is None:
            msg = "Only a fully checked source can be rewound"
            raise RuntimeError(msg)
        if self._stream is not None:
            self._stream.close()
        self._raw.seek(0)
        self.complete = False
        self._iterated = False
        self.line_number = 0
        self._line(HEADER_LIMIT, self._open_stream())

    def _finish(self) -> None:
        if self._raw is None or self._initial is None:
            msg = "Reader is not open"
            raise RuntimeError(msg)
        if self.path.name.endswith(".zstd"):
            _check_frames(self._raw, self._initial.st_size)
        current = self.path.stat()
        initial = self._initial
        if (
            current.st_ino != initial.st_ino
            or current.st_dev != initial.st_dev
            or current.st_size < initial.st_size
            or (current.st_size == initial.st_size and current.st_mtime_ns != initial.st_mtime_ns)
        ):
            self.errors.append(Issue("source_changed", self.path, "Source changed during scanning"))
        self.complete = not self.errors
