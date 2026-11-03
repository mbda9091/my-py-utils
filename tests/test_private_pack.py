from __future__ import annotations

import errno
import hashlib
import os
import secrets
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING

import py7zr
import pytest
from private_pack import workflow
from private_pack.main import main
from private_pack.manifest import (
    HEADER,
    Entry,
    Manifest,
    PackError,
    Padding,
    decode_manifest,
    decrypt_manifest,
    encode_manifest,
    encrypt_manifest,
    make_password,
    valid_password,
)
from private_pack.planning import inventory, make_plan
from private_pack.storage import commit_directory, open_source, write_private
from private_pack.workflow import decrypt_config, keygen, pack

if TYPE_CHECKING:
    from pathlib import Path


BATCH_COUNT = 4
PADDING_BYTES = 16
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


@pytest.fixture
def complete_manifest() -> Manifest:
    return Manifest(
        make_password(),
        [
            Entry(
                "资料",
                "directory",
                "资料.7z",
                "a" * 32 + ".7z",
                Padding("padding.bin"),
                100,
                "f" * 64,
            )
        ],
    )


@pytest.fixture
def batch(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "input"
    source.mkdir()
    (source / "项目A" / "empty").mkdir(parents=True)
    (source / "项目A" / ".hidden").write_bytes(b"secret contents\x00" * 100)
    (source / "empty-dir").mkdir()
    (source / "说明.pdf").write_bytes(b"test file")
    (source / "empty-file").touch()
    key = tmp_path / "key"
    keygen(key)
    return source, tmp_path / "output", key


def test_password_plan_and_repr(batch: tuple[Path, Path, Path]) -> None:
    source, _, _ = batch
    plan = make_plan(inventory(source))
    assert valid_password(plan.archive_password)
    assert plan.archive_password not in repr(plan)
    assert len(plan.entries) == BATCH_COUNT
    assert len({e.archive_name for e in plan.entries}) == BATCH_COUNT
    assert all(e.padding.size_bytes == PADDING_BYTES for e in plan.entries)
    assert all(e.padding.archive_path != e.source for e in plan.entries)


def test_config_roundtrip(complete_manifest: Manifest) -> None:
    key = secrets.token_bytes(32)
    first = encrypt_manifest(complete_manifest, key)
    second = encrypt_manifest(complete_manifest, key)
    assert first != second
    assert complete_manifest.archive_password.encode() not in first
    assert decrypt_manifest(first, key) == complete_manifest
    assert decode_manifest(encode_manifest(complete_manifest)) == complete_manifest


@pytest.mark.parametrize("damage", ["key", "header", "nonce", "ciphertext", "tag", "truncate"])
def test_config_authentication(complete_manifest: Manifest, damage: str) -> None:
    key = secrets.token_bytes(32)
    blob = bytearray(encrypt_manifest(complete_manifest, key))
    offsets = {"header": len(HEADER) - 1, "nonce": len(HEADER), "ciphertext": -20, "tag": -1}
    if damage == "key":
        key = secrets.token_bytes(32)
    elif damage == "truncate":
        del blob[-12:]
    else:
        blob[offsets[damage]] ^= 1
    with pytest.raises(PackError):
        decrypt_manifest(bytes(blob), key)


@pytest.mark.parametrize("bad", [b"null", b"[]", b"{}", b"not-json", b'{"version": true}', b"\xff"])
def test_invalid_manifest(bad: bytes) -> None:
    with pytest.raises(PackError):
        decode_manifest(bad)


def test_manifest_rejects_unfinished_and_duplicate(complete_manifest: Manifest) -> None:
    complete_manifest.entries.append(complete_manifest.entries[0])
    with pytest.raises(PackError, match="Duplicate"):
        encode_manifest(complete_manifest)
    complete_manifest.entries.pop()
    complete_manifest.entries[0].archive_size = None
    with pytest.raises(PackError):
        encode_manifest(complete_manifest)


def test_pack_roundtrip_and_repeat(batch: tuple[Path, Path, Path]) -> None:
    source, output, key = batch
    before = inventory(source)
    assert pack(source, output, key) == BATCH_COUNT
    manifest = decrypt_manifest((output / "manifest.enc").read_bytes(), key.read_bytes())
    assert len(list(output.iterdir())) == BATCH_COUNT + 1
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == PRIVATE_DIRECTORY_MODE
        assert stat.S_IMODE((output / "manifest.enc").stat().st_mode) == PRIVATE_FILE_MODE
    digests: dict[str, str] = {}
    for entry in manifest.entries:
        archive = output / entry.archive_name
        assert entry.original_archive_name == entry.source + ".7z"
        assert archive.stat().st_size == entry.archive_size
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == entry.archive_sha256
        digests[entry.source] = hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest()
        destination = output.parent / ("extracted-" + entry.archive_name)
        with py7zr.SevenZipFile(archive, password=manifest.archive_password) as handle:
            handle.extractall(destination)
        assert len((destination / entry.padding.archive_path).read_bytes()) == PADDING_BYTES
        for relative, info in before.items():
            if relative != entry.source and not relative.startswith(entry.source + "/"):
                continue
            if stat.S_ISDIR(info.mode):
                assert (destination / relative).is_dir()
            else:
                assert (destination / relative).read_bytes() == (source / relative).read_bytes()
    assert inventory(source) == before
    other = output.parent / "second"
    pack(source, other, key)
    repeated = decrypt_manifest((other / "manifest.enc").read_bytes(), key.read_bytes())
    assert {e.archive_name for e in manifest.entries}.isdisjoint(
        e.archive_name for e in repeated.entries
    )
    for entry in repeated.entries:
        assert (
            hashlib.md5(
                (other / entry.archive_name).read_bytes(), usedforsecurity=False
            ).hexdigest()
            != digests[entry.source]
        )
    assert not list(output.parent.glob(".private-pack-*"))


def test_independent_7zip(batch: tuple[Path, Path, Path]) -> None:
    executable = os.environ.get("PRIVATE_PACK_7Z") or shutil.which("7zz") or shutil.which("7z")
    if executable is None:
        if os.environ.get("CI"):
            pytest.fail("Independent 7-Zip is required in CI")
        pytest.skip("Set PRIVATE_PACK_7Z to run real 7-Zip compatibility checks")
    source, output, key = batch
    pack(source, output, key)
    manifest = decrypt_manifest((output / "manifest.enc").read_bytes(), key.read_bytes())
    for entry in manifest.entries:
        archive = output / entry.archive_name
        for password in (None, "incorrect-test-password"):
            args = [executable, "l", str(archive)]
            if password is not None:
                args.append("-p" + password)
            result = subprocess.run(args, input=b"\n", capture_output=True, timeout=30, check=False)  # noqa: S603
            assert result.returncode != 0
            assert entry.padding.archive_path.encode() not in result.stdout
        rejected = output.parent / ("rejected-" + entry.archive_name)
        wrong = subprocess.run(  # noqa: S603
            [executable, "x", str(archive), "-pincorrect", "-o" + str(rejected), "-y"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert wrong.returncode != 0
        assert not (rejected / entry.source).exists()
        destination = output.parent / ("7zip-" + entry.archive_name)
        # Disposable fixture credentials only; the production CLI never passes passwords in argv.
        result = subprocess.run(  # noqa: S603
            [
                executable,
                "x",
                str(archive),
                "-p" + manifest.archive_password,
                "-o" + str(destination),
                "-y",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert (destination / entry.source).exists()
        assert len((destination / entry.padding.archive_path).read_bytes()) == PADDING_BYTES
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if relative.parts[0] == entry.source and path.is_file():
                assert (destination / relative).read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    "case", ["existing", "inside", "empty", "symlink", "fifo", "key-inside", "key-hardlink"]
)
def test_invalid_inputs(batch: tuple[Path, Path, Path], case: str) -> None:
    source, output, key = batch
    if case == "existing":
        output.mkdir()
    elif case == "inside":
        output = source / "output"
    elif case == "empty":
        source = source.parent / "empty"
        source.mkdir()
    elif case == "symlink":
        (source / "link").symlink_to(key)
    elif case == "fifo":
        if os.name == "nt":
            pytest.skip("POSIX FIFO is not a Windows file type")
        os.mkfifo(source / "fifo")
    elif case == "key-inside":
        key = source / "key"
        keygen(key)
    else:
        (source / "key-link").hardlink_to(key)
    with pytest.raises(PackError):
        pack(source, output, key)
    assert not list(output.parent.glob(".private-pack-*"))


def test_no_overwrite_commit(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(FileExistsError):
        commit_directory(staging, target)
    assert staging.is_dir()
    assert target.is_dir()


@pytest.mark.parametrize("step", ["create_archive", "write_private", "commit_directory"])
def test_failed_batch_cleanup(
    batch: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    source, output, key = batch
    before = inventory(source)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "injected test failure")

    monkeypatch.setattr(workflow, step, fail)
    with pytest.raises(OSError, match="injected"):
        pack(source, output, key)
    assert not output.exists()
    assert inventory(source) == before
    assert not list(output.parent.glob(".private-pack-*"))


def test_source_change_aborts(batch: tuple[Path, Path, Path]) -> None:
    source, output, key = batch

    def modify(_done: int, _total: int) -> None:
        (source / "added").touch()

    with pytest.raises(PackError, match="changed"):
        pack(source, output, key, modify)
    assert not output.exists()


def test_concurrent_target_preserved(batch: tuple[Path, Path, Path]) -> None:
    source, output, key = batch

    def create_target(_done: int, _total: int) -> None:
        output.mkdir(exist_ok=True)
        (output / "user-file").write_bytes(b"preserve me")

    with pytest.raises(FileExistsError):
        pack(source, output, key, create_target)
    assert (output / "user-file").read_bytes() == b"preserve me"
    assert list(output.iterdir()) == [output / "user-file"]
    assert not list(output.parent.glob(".private-pack-*"))


def test_symlink_output_preserved(batch: tuple[Path, Path, Path]) -> None:
    source, output, key = batch
    output.symlink_to(output.parent / "missing")
    with pytest.raises(PackError, match="exists"):
        pack(source, output, key)
    assert output.is_symlink()


def test_short_key_rejected(tmp_path: Path) -> None:
    short_key = tmp_path / "short-key"
    short_key.write_bytes(b"short")
    output = tmp_path / "plain"
    with pytest.raises(PackError, match="32 bytes"):
        decrypt_config(tmp_path / "unused", short_key, output)
    assert not output.exists()


def test_open_source_refuses_parent_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_bytes(b"outside")
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((OSError, PackError)), open_source(root, "link/secret"):
        pytest.fail("Followed a parent symlink")


def test_cli_and_private_outputs(
    batch: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    source, output, key = batch
    assert main(["pack", str(source), "--output", str(output), "--key-file", str(key)]) == 0
    manifest_path = output / "manifest.enc"
    manifest = decrypt_manifest(manifest_path.read_bytes(), key.read_bytes())
    captured = capsys.readouterr()
    assert manifest.archive_password not in captured.out + captured.err
    plain = output.parent / "manifest.json"
    assert (
        main(["decrypt-config", str(manifest_path), "--key-file", str(key), "--output", str(plain)])
        == 0
    )
    assert decode_manifest(plain.read_bytes()) == manifest
    if os.name != "nt":
        assert stat.S_IMODE(plain.stat().st_mode) == PRIVATE_FILE_MODE
        assert stat.S_IMODE(key.stat().st_mode) == PRIVATE_FILE_MODE
    assert main(["keygen", "--output", str(key)]) == 1
    original = plain.read_bytes()
    with pytest.raises(FileExistsError):
        decrypt_config(manifest_path, key, plain)
    assert plain.read_bytes() == original
    wrong = output.parent / "wrong-key"
    keygen(wrong)
    nonexistent = output.parent / "must-not-exist"
    assert (
        main(
            [
                "decrypt-config",
                str(manifest_path),
                "--key-file",
                str(wrong),
                "--output",
                str(nonexistent),
            ]
        )
        == 1
    )
    assert not nonexistent.exists()


def test_private_write_failure_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_fd: int) -> None:
        raise OSError(errno.ENOSPC, "injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    target = tmp_path / "secret"
    with pytest.raises(OSError, match="injected"):
        write_private(target, b"not persisted")
    assert not target.exists()


@pytest.mark.parametrize("spelling", ["absolute", "relative", "dot"])
def test_cli_default_output(
    batch: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    spelling: str,
) -> None:
    source, explicit_output, key = batch
    if spelling == "dot":
        monkeypatch.chdir(source)
        argument = "."
    elif spelling == "relative":
        monkeypatch.chdir(source.parent)
        argument = "./input/"
    else:
        argument = str(source)
    command = ["pack", argument, "--key-file", str(key)]
    assert main(command) == 0
    output = source.with_name(source.name + "-packed")
    encrypted = (output / "manifest.enc").read_bytes()
    assert len(decrypt_manifest(encrypted, key.read_bytes()).entries) == BATCH_COUNT
    assert not explicit_output.exists()
    assert main(command) == 1
    assert (output / "manifest.enc").read_bytes() == encrypted
