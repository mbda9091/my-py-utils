"""CI-only producer/consumer. All credentials and contents are disposable fixtures."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import py7zr
from private_pack.manifest import PADDING_SIZE, PackError, decrypt_manifest
from private_pack.workflow import decrypt_config

FILES = {"资料 空间/readme.txt": b"cross-platform\x00\xff", "empty-file": b"", "top.txt": b"hello"}
DIRECTORIES = {"资料 空间", "资料 空间/empty", "empty-dir"}
PRODUCERS = {"ubuntu-24.04", "macos-15", "windows-2022"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence() -> dict[str, object]:
    return {
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python": sys.version,
        "image": os.environ.get("ImageVersion", "local"),  # noqa: SIM112 - GitHub name
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("private-pack", "py7zr", "cryptography")
        },
    }


def produce(root: Path) -> None:
    root.mkdir(parents=True)
    source = root / "input"
    source.mkdir()
    for name in sorted(DIRECTORIES):
        (source / name).mkdir(parents=True, exist_ok=True)
    for name, content in FILES.items():
        (source / name).write_bytes(content)
    executable = Path(sys.executable).parent / (
        "private-pack.exe" if os.name == "nt" else "private-pack"
    )
    commands = [
        ["keygen", "--output", str(root / "test.key")],
        ["pack", str(source), "--key-file", str(root / "test.key")],
        [
            "pack",
            str(source),
            "--key-file",
            str(root / "test.key"),
            "--output",
            str(root / "batch"),
        ],
        [
            "decrypt-config",
            str(root / "batch/manifest.enc"),
            "--key-file",
            str(root / "test.key"),
            "--output",
            str(root / "plain.json"),
        ],
    ]
    for arguments in commands:
        subprocess.run([str(executable), *arguments], check=True, capture_output=True, timeout=120)  # noqa: S603
    assert (root / "input-packed/manifest.enc").is_file()
    assert json.loads((root / "plain.json").read_text(encoding="utf-8"))["version"] == 1
    shutil.rmtree(root / "input-packed")
    (root / "plain.json").unlink()
    report = evidence()
    report["sha256"] = {
        path.relative_to(root).as_posix(): digest(path)
        for path in root.rglob("*")
        if path.is_file()
    }
    (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def consume(root: Path, destination: Path) -> None:
    executable = os.environ.get("PRIVATE_PACK_7Z") or shutil.which("7zz") or shutil.which("7z")
    assert executable, "Independent 7-Zip is mandatory"
    report = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
    assert report["commit"] == os.environ.get("GITHUB_SHA", "local")
    for relative, expected in report["sha256"].items():
        path = root / relative
        assert path.resolve().is_relative_to(root.resolve())
        assert digest(path) == expected
    destination.mkdir(parents=True)
    decrypt_config(root / "batch/manifest.enc", root / "test.key", destination / "manifest.json")
    manifest = decrypt_manifest(
        (root / "batch/manifest.enc").read_bytes(), (root / "test.key").read_bytes()
    )
    try:
        decrypt_manifest((root / "batch/manifest.enc").read_bytes(), bytes(32))
    except PackError:
        pass
    else:
        msg = "Wrong key accepted"
        raise AssertionError(msg)
    assert {entry.source for entry in manifest.entries} == {
        "资料 空间",
        "empty-file",
        "top.txt",
        "empty-dir",
    }
    for index, entry in enumerate(manifest.entries):
        archive = root / "batch" / entry.archive_name
        assert digest(archive) == entry.archive_sha256
        expected_files = {
            name: content
            for name, content in FILES.items()
            if name == entry.source or name.startswith(entry.source + "/")
        }
        expected_dirs = {
            name
            for name in DIRECTORIES
            if name == entry.source or name.startswith(entry.source + "/")
        }
        expected_names = set(expected_files) | expected_dirs | {entry.padding.archive_path}
        with py7zr.SevenZipFile(archive, password=manifest.archive_password) as handle:
            assert set(handle.getnames()) == expected_names
        rejected = subprocess.run(  # noqa: S603
            [executable, "t", str(archive), "-pincorrect"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert rejected.returncode != 0
        output = destination / str(index)
        subprocess.run(  # noqa: S603
            [
                executable,
                "x",
                str(archive),
                "-p" + manifest.archive_password,
                "-o" + str(output),
                "-y",
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        assert {path.relative_to(output).as_posix() for path in output.rglob("*")} == expected_names
        for name, content in expected_files.items():
            assert (output / name).read_bytes() == content
        for name in expected_dirs:
            assert (output / name).is_dir()
        assert (output / entry.padding.archive_path).stat().st_size == PADDING_SIZE
    result = evidence()
    result["producer"] = report
    result["seven_zip"] = subprocess.run(  # noqa: S603
        [executable],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.splitlines()[:5]
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    mode, argument = sys.argv[1:3]
    if mode == "produce":
        produce(Path(argument).resolve())
    elif mode == "consume":
        artifacts = Path(argument).resolve()
        assert {path.name for path in artifacts.iterdir()} == {"fixture-" + p for p in PRODUCERS}
        for producer in sorted(artifacts.iterdir()):
            consume(producer, Path("interop-results") / producer.name)
    else:
        msg = "Expected produce or consume"
        raise ValueError(msg)
