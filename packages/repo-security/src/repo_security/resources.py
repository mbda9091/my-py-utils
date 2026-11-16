from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from repo_security.errors import RepoSecurityError

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class TemplateFile:
    destination: str
    content: bytes
    executable: bool


def template_files(*, github_actions: bool = False) -> tuple[TemplateFile, ...]:
    root = files("repo_security").joinpath("templates")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    result: list[TemplateFile] = []
    for entry in manifest["files"]:
        destination = entry["destination"]
        resource = entry["resource"]
        for name in (destination, resource):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                msg = "Unsafe path in bundled template manifest"
                raise RepoSecurityError(msg)
        content = root.joinpath(*PurePosixPath(resource).parts).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            msg = f"Bundled template checksum mismatch: {destination}"
            raise RepoSecurityError(msg)
        if destination.startswith(".github/") and not github_actions:
            continue
        result.append(TemplateFile(destination, content, executable=entry["executable"]))
    return tuple(result)


def write_template(root: Path) -> None:
    """Materialize verified resources in a fresh .security directory only."""
    resources = template_files()
    security = root / ".security"
    security.mkdir()  # Exclusive creation: an existing installation is never overwritten.
    for resource in resources:
        target = root / resource.destination
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(resource.content)
        target.chmod(0o755 if resource.executable else 0o644)
