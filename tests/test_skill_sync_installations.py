from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest
from skill_sync.config import (
    LOCAL_SCHEMA_VERSION,
    atomic_write_json,
    config_path,
    init_config,
    installations_path,
)
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    installation_status,
    read_managed_installations,
    write_managed_installations,
)
from skill_sync.installer import install_skill
from skill_sync.repository import init_repository

from tests.skill_sync_helpers import make_local_repository, make_personal_config, write_skill

if TYPE_CHECKING:
    from pathlib import Path


def legacy_record(path: Path, name: str = "demo-skill") -> dict[str, str]:
    return {"skill": name, "target": "codex", "commit": "abc123", "path": str(path)}


def test_v1_config_and_multiple_use_paths_migrate_with_exact_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    state = tmp_path / "state"
    old = init_config("git@example.com:me/skills.git", branch="main", state_home=state)
    old_skill = write_skill(old.checkout / "skills")
    standard = home / ".agents/skills/demo-skill"
    standard.parent.mkdir(parents=True)
    standard.symlink_to(old_skill, target_is_directory=True)
    custom = tmp_path / "custom/demo-skill"
    custom.parent.mkdir()
    custom.symlink_to(old_skill, target_is_directory=True)
    raw = json.dumps(
        {"codex:demo-skill": legacy_record(standard), "other:demo-skill": legacy_record(custom)}
    ).encode()
    installations_path(state).write_bytes(raw)
    config_before = config_path(state).read_bytes()
    new = make_local_repository(tmp_path)
    (new / "skills").mkdir()
    shutil.copytree(old_skill, new / "skills/demo-skill")

    config = init_repository(new, state_home=state)
    records = read_managed_installations(state)

    assert set(records) == {str(standard), str(custom)}
    assert records[str(custom)].target == "local-path"
    assert records[str(standard)].target == "codex"
    assert records[str(standard)].source_path == old_skill
    assert installation_status(records[str(standard)], new) == "previous-repository"
    assert next(state.glob("config.json.*.bak")).read_bytes() == config_before
    assert next(state.glob("installations.json.*.bak")).read_bytes() == raw
    assert standard.readlink() == old_skill
    install_skill("demo-skill", config, target="codex", force=True)
    assert standard.readlink() == new / "skills/demo-skill"
    assert custom.readlink() == old_skill
    assert old_skill.is_dir()


def test_m1_legacy_records_can_be_read_without_mutation_then_migrated(
    tmp_path: Path,
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    old_root = tmp_path / "previous-repository"
    old_skill = write_skill(old_root / "skills")
    target_root = tmp_path / "installed"
    target_root.mkdir()
    path = target_root / "demo-skill"
    path.symlink_to(old_skill, target_is_directory=True)
    raw = json.dumps({"codex:demo-skill": legacy_record(path)}).encode()
    installations_path(config.state_home).write_bytes(raw)
    (config.state_home / "config.json.legacy.bak").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "checkout": str(old_root),
            }
        )
    )

    records = read_managed_installations(config.state_home)

    assert records[str(path)].source_path == old_skill
    assert installations_path(config.state_home).read_bytes() == raw
    install_skill("demo-skill", config, target="codex", target_root=target_root, force=True)
    assert path.readlink() == config.repository_path / "skills/demo-skill"
    assert next(config.state_home.glob("installations.json.*.bak")).read_bytes() == raw
    assert (
        json.loads(installations_path(config.state_home).read_text())["schemaVersion"]
        == LOCAL_SCHEMA_VERSION
    )


def test_unknown_legacy_source_cannot_authorize_force(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    third_party = write_skill(tmp_path / "third-party")
    root = tmp_path / "installed"
    root.mkdir()
    path = root / "demo-skill"
    path.symlink_to(third_party, target_is_directory=True)
    raw = json.dumps({"codex:demo-skill": legacy_record(path)}).encode()
    installations_path(config.state_home).write_bytes(raw)

    record = read_managed_installations(config.state_home)[str(path)]
    assert record.source_path is None
    assert installation_status(record, config.repository_path) == "unresolved"
    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)
    assert path.readlink() == third_party
    assert installations_path(config.state_home).read_bytes() == raw


def test_binding_failure_restores_config_and_records_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    init_config("git@example.com:me/skills.git", branch="main", state_home=state)
    raw = json.dumps(
        {"codex:demo-skill": legacy_record(tmp_path / "installed/demo-skill")}
    ).encode()
    installations_path(state).write_bytes(raw)
    before = config_path(state).read_bytes()
    new = make_local_repository(tmp_path)
    writer = atomic_write_json

    def fail_config(path: Path, payload: object) -> None:
        if path == config_path(state):
            message = "config write failed"
            raise OSError(message)
        writer(path, payload)

    monkeypatch.setattr("skill_sync.repository.atomic_write_json", fail_config)
    with pytest.raises(OSError, match="config write failed"):
        init_repository(new, state_home=state)
    assert config_path(state).read_bytes() == before
    assert installations_path(state).read_bytes() == raw
    assert next(state.glob("config.json.*.bak")).read_bytes() == before
    assert next(state.glob("installations.json.*.bak")).read_bytes() == raw


@pytest.mark.parametrize(
    "state", ["ok", "missing", "broken", "replaced", "unresolved", "previous-repository"]
)
def test_status_reports_only_recorded_entry_states(tmp_path: Path, state: str) -> None:
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    path = tmp_path / "installed/demo-skill"
    path.parent.mkdir()
    source: Path | None = skill
    if state == "previous-repository":
        source = write_skill(tmp_path / "previous/skills")
    if state == "unresolved":
        path.symlink_to(skill, target_is_directory=True)
        source = None
    elif state == "replaced":
        path.mkdir()
    elif state != "missing":
        path.symlink_to(source, target_is_directory=True)
    if state == "broken":
        skill.rename(skill.with_name("moved"))
    record = ManagedInstallation("demo-skill", "local-path", path, source)

    assert installation_status(record, config.repository_path) == state


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schemaVersion": 9},
        {"schemaVersion": True},
        {"schemaVersion": 2, "installations": []},
        {"codex:foo": {"path": "relative"}},
    ],
)
def test_malformed_records_are_not_silently_discarded(tmp_path: Path, payload: object) -> None:
    installations_path(tmp_path).write_text(json.dumps(payload))
    before = installations_path(tmp_path).read_bytes()
    with pytest.raises(SkillSyncError):
        read_managed_installations(tmp_path)
    assert installations_path(tmp_path).read_bytes() == before


def test_multiple_entries_with_same_skill_round_trip_without_commit_fields(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    records = {
        str(tmp_path / use / "demo-skill"): ManagedInstallation(
            "demo-skill",
            "local-path",
            tmp_path / use / "demo-skill",
            source,
        )
        for use in ("first", "second")
    }
    write_managed_installations(records, config.state_home)
    assert read_managed_installations(config.state_home) == records
    payload = json.loads(installations_path(config.state_home).read_text())
    assert len(payload["installations"]) == len(records)
    assert all("commit" not in value for value in payload["installations"].values())


@pytest.mark.parametrize("version", [99, True])
def test_invalid_backup_version_cannot_establish_personal_ownership(
    tmp_path: Path,
    version: object,
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    foreign_root = tmp_path / "foreign-repository"
    foreign = write_skill(foreign_root / "skills")
    path = tmp_path / "installed/demo-skill"
    path.parent.mkdir()
    path.symlink_to(foreign, target_is_directory=True)
    installations_path(config.state_home).write_text(
        json.dumps(
            {
                "codex:demo-skill": legacy_record(path),
            }
        )
    )
    (config.state_home / "config.json.invalid.bak").write_text(
        json.dumps(
            {
                "schemaVersion": version,
                "repositoryPath": str(foreign_root),
                "checkout": str(foreign_root),
            }
        )
    )

    assert read_managed_installations(config.state_home)[str(path)].source_path is None
    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target="codex", target_root=path.parent, force=True)
    assert path.readlink() == foreign
