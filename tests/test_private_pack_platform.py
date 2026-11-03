from __future__ import annotations

import json
import os
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from private_pack.manifest import PackError, decrypt_manifest
from private_pack.planning import Snapshot
from private_pack.storage import (
    check_capabilities,
    commit_directory,
    open_source,
    private_temp_directory,
    read_key,
    write_private,
)
from private_pack.workflow import keygen, pack


@pytest.mark.parametrize("kind", ["empty", "nonempty", "symlink", "file"])
def test_exclusive_publish(tmp_path: Path, kind: str) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "batch").write_bytes(b"new")
    if kind == "symlink":
        target.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif kind == "file":
        target.write_bytes(b"old")
    else:
        target.mkdir()
        if kind == "nonempty":
            (target / "existing").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        commit_directory(source, target)
    assert (source / "batch").read_bytes() == b"new"
    assert not (target / "batch").exists()


def test_capability_probe_cleans_up(tmp_path: Path) -> None:
    check_capabilities(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_key_parent_link_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "real"
    directory.mkdir()
    keygen(directory / "key")
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises((OSError, PackError)):
        read_key(alias / "key")


def test_key_link_rejected(tmp_path: Path) -> None:
    keygen(tmp_path / "key")
    (tmp_path / "link").symlink_to(tmp_path / "key")
    with pytest.raises((OSError, PackError)):
        read_key(tmp_path / "link")


def test_cleanup_failure_reports_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path) -> None:
        msg = "occupied"
        raise PermissionError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", fail)
        with (
            pytest.raises(PackError, match=r"Could not remove.*private-pack-"),
            private_temp_directory(tmp_path),
        ):
            pass
    for residual in tmp_path.iterdir():
        shutil.rmtree(residual)


def test_legacy_v1() -> None:
    fixture = Path(__file__).parent / "fixtures/private-pack/legacy-v1.json"
    data = json.loads(fixture.read_text())
    manifest = decrypt_manifest(bytes.fromhex(data["blob"]), bytes.fromhex(data["key"]))
    assert manifest.version == 1
    assert manifest.entries[0].source == "legacy.txt"
    assert manifest.archive_password == "Aa0123456789abcdefgh"  # noqa: S105 - public fixture


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction and handle semantics")
def test_windows_junction_and_parent_lock(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    keygen(tmp_path / "key")
    (source / "file").write_bytes(b"fixture")
    junction = tmp_path / "junction"
    subprocess.run(  # noqa: S603
        [shutil.which("cmd") or "cmd", "/c", "mklink", "/J", str(junction), str(source)],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.raises(PackError):
            pack(junction, tmp_path / "output", tmp_path / "key")
        with pytest.raises(PackError), open_source(junction, "file"):
            pytest.fail("Read through a junction")
    finally:
        junction.rmdir()
    with open_source(source, "file") as stream:
        with pytest.raises(PermissionError):
            source.rename(tmp_path / "replaced")
        assert stream.read() == b"fixture"
    source.rename(tmp_path / "released")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DACL inspection")
def test_windows_private_dacl(tmp_path: Path) -> None:
    # Inspect effective ACE identities independently of the ctypes creation code.
    script = """
    $acl = Get-Acl -LiteralPath $env:PRIVATE_PACK_TEST_PATH
    $ids = @($acl.Access | ForEach-Object {
      $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    })
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    @{protected=$acl.AreAccessRulesProtected; ids=$ids; user=$user} | ConvertTo-Json -Compress
    """
    with private_temp_directory(tmp_path) as directory:
        file = directory / "key"
        write_private(file, b"test")
        for path in (directory, file):
            result = subprocess.run(  # noqa: S603
                [
                    shutil.which("powershell") or "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                env={**os.environ, "PRIVATE_PACK_TEST_PATH": str(path)},
                capture_output=True,
                text=True,
                check=True,
            )
            acl = json.loads(result.stdout)
            assert acl["protected"]
            assert set(acl["ids"]) == {acl["user"], "S-1-5-18"}


@pytest.mark.skipif(os.name != "nt", reason="Native Windows exclusive file handle")
def test_windows_occupied_key_fails(tmp_path: Path) -> None:
    key = tmp_path / "key"
    keygen(key)
    with open_source(tmp_path, "key"), pytest.raises(PermissionError):
        key.unlink()
    key.unlink()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX default ACL")
def test_linux_inherited_acl_is_masked(tmp_path: Path) -> None:
    # Linux POSIX ACL xattr v2: owner, named user, group, mask, other.
    default_acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, 7, uid)
        for tag, uid in (
            (1, 0xFFFFFFFF),
            (2, 65534),
            (4, 0xFFFFFFFF),
            (16, 0xFFFFFFFF),
            (32, 0xFFFFFFFF),
        )
    )
    os.setxattr(tmp_path, "system.posix_acl_default", default_acl)
    with private_temp_directory(tmp_path) as directory:
        write_private(directory / "key", b"fixture")
        assert stat.S_IMODE(directory.stat().st_mode) == stat.S_IRWXU
        assert stat.S_IMODE((directory / "key").stat().st_mode) == (stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extended ACL")
def test_macos_acl_parent_rejected(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603
        [
            shutil.which("chmod") or "chmod",
            "+a",
            "everyone allow read,write,file_inherit,directory_inherit",
            str(tmp_path),
        ],
        check=True,
    )
    try:
        with pytest.raises(PackError, match="ACL"):
            write_private(tmp_path / "key", b"fixture")
        assert not (tmp_path / "key").exists()
    finally:
        subprocess.run([shutil.which("chmod") or "chmod", "-N", str(tmp_path)], check=True)  # noqa: S603


@pytest.mark.parametrize("name", ["program.exe", "脚本 空间.txt"])
def test_path_and_handle_identity_agree(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"fixture")
    before = Snapshot.from_stat(path.stat())
    with open_source(tmp_path, name) as stream:
        assert Snapshot.from_stat(os.fstat(stream.fileno())) == before


def test_source_parent_replacement_never_reads_outside(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "file").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"outside")
    nested.rename(root / "moved")
    nested.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, PackError)), open_source(root, "nested/file"):
        pytest.fail("Read a substituted parent")
