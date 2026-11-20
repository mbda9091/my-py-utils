"""Exercise built wheels on Linux/macOS; use only disposable local Git remotes."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


NATIVE_SCHEMA = 2


@dataclass(frozen=True)
class Wheel:
    path: Path
    name: str
    version: str
    scripts: dict[str, str]


def log(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def environment(*, offline: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIR",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_CONFIG_COUNT",
    ):
        env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    if offline:
        env["UV_OFFLINE"] = "1"
    return env


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    expected: int = 0,
    offline: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 -- explicit argv, no shell, disposable target directories
        [str(x) for x in command],
        cwd=cwd,
        env=environment(offline=offline),
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if result.returncode != expected:
        message = f"{Path(command[0]).name}: expected exit {expected}, got {result.returncode}"
        details = result.stderr[-2000:]
        raise RuntimeError(message + "\n" + details)
    return result


def read_wheels(directory: Path) -> dict[str, Wheel]:
    result: dict[str, Wheel] = {}
    for path in sorted(directory.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            meta = next(x for x in archive.namelist() if x.endswith(".dist-info/METADATA"))
            data = Parser().parsestr(archive.read(meta).decode())
            name, version = data["Name"], data["Version"]
            assert isinstance(name, str)
            assert isinstance(version, str)
            assert name not in result, "Use a fresh wheel directory; duplicate package versions"
            scripts = configparser.ConfigParser(interpolation=None)
            entry = meta.removesuffix("METADATA") + "entry_points.txt"
            if entry in archive.namelist():
                scripts.read_string(archive.read(entry).decode())
            result[name] = Wheel(
                path,
                name,
                version,
                dict(scripts["console_scripts"]) if scripts.has_section("console_scripts") else {},
            )
    assert {"repo-security", "my-py-utils-cli"}.issubset(result)
    return result


def metadata_snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            path.stat().st_mode,
        )
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def configuration_snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    return {
        name: value
        for name, value in metadata_snapshot(root).items()
        if not name.startswith((".security/.venv/", ".security/bin/"))
    }


def probe_install(
    env: Path, expected: dict[str, str], scripts: dict[str, str]
) -> dict[str, object]:
    code = """
from importlib.metadata import version, distribution, distributions
from pathlib import Path
import json, platform, sys, repo_security
expected, scripts = json.loads(sys.stdin.read())
for name, value in expected.items():
    assert version(name) == value, name
assert Path(repo_security.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
installed = {d.metadata["Name"] for d in distributions()}
assert {"semgrep", "presidio-analyzer"}.isdisjoint(installed)
owner = "my-py-utils-cli" if "my-py-utils-cli" in expected else "repo-security"
actual = {ep.name: ep for ep in distribution(owner).entry_points if ep.group == "console_scripts"}
assert set(actual) == set(scripts)
for name, value in scripts.items():
    assert actual[name].value == value
    assert callable(actual[name].load())
print(json.dumps({"system":platform.system(), "machine":platform.machine(),
                  "python":platform.python_version(), "versions":expected,
                  "entry_points":scripts}))
"""
    result = run(
        [env / "bin/python", "-I", "-c", code], cwd=env, input_text=json.dumps([expected, scripts])
    )
    data: dict[str, object] = json.loads(result.stdout)
    return data


def git(root: Path, *arguments: str) -> str:
    return run(["git", "-C", root, *arguments], cwd=root).stdout.strip()


def check_hooks(cli: Path, root: Path, remote: Path) -> None:
    git(root, "config", "user.name", "Security Acceptance")
    git(root, "config", "user.email", "test@example.com")
    app = root / "sample.py"
    safe_text = "print('safe')\n"
    app.write_text(safe_text, encoding="utf-8")
    git(root, "add", ".")
    committed = run(
        ["git", "-C", root, "commit", "-qm", "Reviewed generated configuration"], cwd=root
    )
    assert "independent-hook-ran" in committed.stdout + committed.stderr
    clean = git(root, "rev-parse", "HEAD")
    run([cli, "scan", root, "--mode", "head"], cwd=root)
    run([cli, "scan", root, "--mode", "history"], cwd=root)
    token = "ghp_" + "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5"
    phone = "139" + "12345678"
    app.write_text(
        f'token = "{token}"\nphone = "{phone}"\nresult = eval(user_input)\n', encoding="utf-8"
    )
    git(root, "add", "sample.py")
    # Preserve the staged risky snapshot despite a safe, unstaged working version.
    app.write_text(safe_text, encoding="utf-8")
    blocked = run(["git", "-C", root, "commit", "-qm", "Must be blocked"], cwd=root, expected=1)
    output = blocked.stdout + blocked.stderr
    assert all(tool + ":" in output for tool in ("Gitleaks", "Semgrep", "Presidio"))
    assert token not in output
    assert phone not in output
    assert "independent-hook-ran" in output
    assert output.index("independent-hook-ran") < output.index("Checking staged snapshot")
    assert git(root, "rev-parse", "HEAD") == clean
    git(root, "commit", "--no-verify", "-qm", "Synthetic risky intermediate")
    git(root, "add", "sample.py")
    git(root, "commit", "-qm", "Restore safe file")
    git(root, "init", "--bare", "-q", str(remote))
    git(root, "remote", "add", "test", str(remote))
    blocked = run([cli, "scan", root, "--mode", "history"], cwd=root, expected=1)
    output = blocked.stdout + blocked.stderr
    assert all(tool + ":" in output for tool in ("Gitleaks", "Semgrep", "Presidio"))
    assert token not in output
    assert phone not in output
    assert not (root / ".security/hooks/pre-push").exists()
    assert not (root / ".git/hooks/pre-commit").exists()
    assert not (root / ".git/hooks/pre-commit.d/10-security").is_symlink()
    assert "repo-security" in git(root, "hook", "list", "pre-commit").splitlines()
    pushed = git(root, "push", "test", "HEAD:refs/heads/main")
    assert "Checking " not in pushed
    assert git(root, "--git-dir", str(remote), "rev-parse", "refs/heads/main") == git(
        root, "rev-parse", "HEAD"
    )


def check_managed_upgrade(  # noqa: PLR0915 -- exercise genuine old resources and both upgrade entries
    cli: Path, root: Path, mode: str
) -> dict[str, str]:
    golden = Path(__file__).parent / "fixtures/repo-security"
    record_path = root / ".security/install-manifest.json"
    record = json.loads(record_path.read_bytes())
    reviewed = {
        relative: ((root / relative).read_bytes(), (root / relative).stat().st_mode)
        for relative in record["files"]
    }
    for registration in record.pop("native_hooks"):
        git(root, "config", "--remove-section", "hook." + registration["name"])
    record.pop("hook_scope")
    record.pop("migration_members")
    record.update(schema_version=1, template_version="0.2.0")
    for relative, source in (
        ("manage.py", "manage.py.txt"),
        ("setup.sh", "setup.sh.template"),
        ("setup.py", "setup.py.template"),
        ("hooks/pre-commit", "hooks/pre-commit.template"),
    ):
        content = (golden / "v0.2.0" / source).read_bytes()
        (root / ".security" / relative).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        record["files"][".security/" + relative].update(
            template_sha256=digest, installed_sha256=digest
        )
    (root / ".security/hook_config.py").unlink()
    record["files"].pop(".security/hook_config.py")
    policy = root / ".security/policy.json"
    policy.write_bytes(policy.read_bytes() + b"\n")
    preserved_policy = policy.read_bytes()
    record["files"][".security/policy.json"]["installed_sha256"] = hashlib.sha256(
        preserved_policy
    ).hexdigest()
    push_content = (golden / "legacy-pre-push.sh").read_bytes()
    push_digest = hashlib.sha256(push_content).hexdigest()
    record["files"][".security/hooks/pre-push"] = {
        "template_sha256": push_digest,
        "installed_sha256": push_digest,
        "executable": True,
    }
    old_push = root / ".security/hooks/pre-push"
    old_push.write_bytes(push_content)
    old_push.chmod(0o755)
    entry = root / ".git/hooks/pre-commit"
    entry.write_bytes((golden / "dispatcher-late.sh").read_bytes())
    entry.chmod(0o755)
    member = root / ".git/hooks/pre-commit.d/10-security"
    member.parent.mkdir()
    member.symlink_to("../../../.security/hooks/pre-commit")
    extra = member.parent / "20-project-checks"
    extra.write_text("#!/bin/sh\necho migrated-project-hook-ran >&2\n")
    extra.chmod(0o755)
    preserved_extra = extra.read_bytes(), extra.stat().st_mtime_ns
    if mode == "single":
        (root / ".security/setup.sh").unlink()
        record["files"].pop(".security/setup.sh")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        old_record = record_path.read_bytes()
        run([cli, "setup", root], cwd=root, offline=True)
        case = "bundled-native-migration-of-0.2.0"
    else:
        record_path.write_text(json.dumps(record), encoding="utf-8")
        old_record = record_path.read_bytes()
        for relative, (content, permissions) in reviewed.items():
            if relative not in {
                ".security/policy.json",
                ".security/semgrep.yml",
                ".security/gitleaks.toml",
            }:
                (root / relative).write_bytes(content)
                (root / relative).chmod(permissions)
        old_push.unlink()
        (root / "Makefile").write_bytes(
            (Path(__file__).resolve().parents[1] / "Makefile").read_bytes()
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "security-upgrade-acceptance"\nversion = "0.0.0"\n'
            'requires-python = ">=3.11"\n[tool.uv]\npackage = false\n'
            "[tool.uv.workspace]\nmembers = []\n",
            encoding="utf-8",
        )
        with (root / ".gitignore").open("a") as stream:
            stream.write("\n.venv/\n")
        git(root, "config", "user.name", "Security Acceptance")
        git(root, "config", "user.email", "test@example.com")
        git(root, "add", ".")
        git(root, "commit", "--no-verify", "-qm", "Pulled managed update fixture")
        git(root, "config", "core.hooksPath", ".security/hooks")
        run(["make", "dev"], cwd=root, offline=True)
        case = "standalone-native-migration-after-reviewed-pull"
    updated = json.loads(record_path.read_bytes())
    backup = root / updated["migration_backup"]
    assert updated["schema_version"] == NATIVE_SCHEMA
    assert updated["status"] == updated["phase"] == "ready"
    assert (backup / "files/.security/install-manifest.json").read_bytes() == old_record
    assert policy.read_bytes() == preserved_policy
    assert preserved_extra == (extra.read_bytes(), extra.stat().st_mtime_ns)
    assert not entry.exists()
    assert not member.is_symlink()
    assert not old_push.exists()
    assert (root / ".security/setup.sh").is_file()
    assert run(["git", "config", "--get", "core.hooksPath"], cwd=root, expected=1).stdout == ""
    log(f"PASS {mode}: native migration ({case}), backup and independent checks preserved")
    return {"case": case, "backup": updated["migration_backup"]}


def check_python_repair(cli: Path, root: Path, mode: str) -> str:
    python = root / ".security/.venv/bin/python"
    if mode == "single":
        damaged = root / ".security/.venv/bin/semgrep"
        damaged.unlink()
        case = "missing-semgrep-launcher"
    else:
        located = run(
            [
                python,
                "-B",
                "-c",
                "import importlib.util as u; print(u.find_spec('presidio_analyzer').origin)",
            ],
            cwd=root,
        )
        damaged = Path(located.stdout.strip())
        assert damaged.is_relative_to(root / ".security/.venv")
        # Break only this installation's file, never the shared uv cache inode.
        damaged.unlink()
        damaged.write_text(
            "raise ImportError('Synthetic damaged installation')\n", encoding="utf-8"
        )
        case = "damaged-presidio-import"
    run([cli, "setup", root], cwd=root, offline=True)
    assert damaged.is_file()
    assert json.loads((root / ".security/install-manifest.json").read_bytes())["status"] == "ready"
    run(
        [python, "-B", "-c", "from presidio_analyzer import Pattern, PatternRecognizer"],
        cwd=root,
        offline=True,
    )
    log(f"PASS {mode}: automatic Python runtime repair ({case})")
    return case


def exercise_mode(  # noqa: PLR0915 -- ordered installation and hook integration checkpoints
    mode: str,
    work: Path,
    wheels: dict[str, Wheel],
    constraints: Path,
    python: str,
) -> dict[str, object]:
    uv = shutil.which("uv")
    assert uv is not None
    env = work / (mode + "-env")
    run([uv, "venv", env, "--python", python], cwd=work)
    selected = [wheels["repo-security"]] if mode == "single" else list(wheels.values())
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            env / "bin/python",
            "--no-config",
            "--constraint",
            constraints,
            *(wheel.path for wheel in selected),
        ],
        cwd=work,
    )
    owner = wheels["repo-security" if mode == "single" else "my-py-utils-cli"]
    result = probe_install(env, {x.name: x.version for x in selected}, owner.scripts)
    for name in owner.scripts:
        run([env / "bin" / name, "--help"], cwd=work)
    log(f"PASS {mode}: independent wheel installation, versions, entry-point loads and --help")
    cli = env / "bin/repo-security"
    root = work / (mode + " 目标仓库")
    root.mkdir()
    git(root, "init", "-q")
    before = metadata_snapshot(root)
    run([cli, "setup", root, "--dry-run", "--github-actions"], cwd=work)
    assert metadata_snapshot(root) == before
    run([cli, "setup", root, "--github-actions"], cwd=work)
    record = json.loads((root / ".security/install-manifest.json").read_bytes())
    assert record["status"] == "ready"
    assert not (root / ".security/hooks/pre-push").exists()
    extra = root / ".project-hooks/project-checks"
    extra.parent.mkdir()
    extra.write_text("#!/bin/sh\necho independent-hook-ran >&2\n", encoding="utf-8")
    extra.chmod(0o755)
    git(root, "config", "hook.project-checks.command", "./.project-hooks/project-checks")
    git(root, "config", "hook.project-checks.event", "pre-commit")
    result["managed_upgrade"] = check_managed_upgrade(cli, root, mode)
    result["python_runtime_repair"] = check_python_repair(cli, root, mode)
    before = configuration_snapshot(root)
    run([cli, "setup", root], cwd=work, offline=True)
    assert configuration_snapshot(root) == before
    before = metadata_snapshot(root)
    run([cli, "doctor", root], cwd=work, offline=True)
    assert metadata_snapshot(root) == before
    check_hooks(cli, root, work / (mode + "-remote.git"))
    log(f"PASS {mode}: setup/preview/repeat/doctor, guarded commit and manual history scan")
    clone = work / (mode + " 独立克隆")
    git(work, "clone", "-q", str(root), str(clone))
    assert not (clone / ".security/install-manifest.json").exists()
    run([clone / ".security/setup.sh"], cwd=clone)
    run([cli, "doctor", clone], cwd=work, offline=True)
    before = configuration_snapshot(clone)
    run([clone / ".security/setup.sh"], cwd=clone, offline=True)
    assert configuration_snapshot(clone) == before
    assert not (clone / ".git/hooks/pre-commit").exists()
    assert not (clone / ".git/hooks/pre-commit.d/10-security").is_symlink()
    assert git(clone, "hook", "list", "-z", "pre-commit") == "repo-security\0"
    assert run(["git", "config", "--get", "core.hooksPath"], cwd=clone, expected=1).stdout == ""
    log(f"PASS {mode}: standalone clone installation and repeat preserve active hooks")
    scanner = run(
        [
            root / ".security/.venv/bin/python",
            "-I",
            "-c",
            "import json,platform; from importlib.metadata import version; "
            "print(json.dumps({'python':platform.python_version(), "
            "'semgrep':version('semgrep'),'presidio':version('presidio-analyzer')}))",
        ],
        cwd=work,
    )
    result["scanners"] = json.loads(scanner.stdout)
    result["gitleaks"] = run([root / ".security/bin/gitleaks", "version"], cwd=work).stdout.strip()
    result["gitleaks_provenance"] = record["gitleaks"]
    result["standalone_clone"] = "passed"
    result["automatic_hook"] = "pre-commit"
    result["pre_push_hook_absent"] = True
    result["checks"] = "passed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new directory outside the source checkout"
    )
    parser.add_argument(
        "--python", default=sys.executable, help="Interpreter for both isolated CLI environments"
    )
    parser.add_argument(
        "--uv", type=Path, help="Use this installed uv binary for all installation subprocesses"
    )
    args = parser.parse_args()
    assert (platform.system(), platform.machine()) in {
        ("Linux", "x86_64"),
        ("Darwin", "arm64"),
        ("Darwin", "x86_64"),
    }, "This acceptance targets Linux x64 or native macOS x64/arm64"
    if args.uv is not None:
        binary = args.uv.resolve()
        assert binary.name == "uv"
        assert binary.is_file()
        assert os.access(binary, os.X_OK)
        os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ.get("PATH", "")
    uv = shutil.which("uv")
    assert uv is not None
    output = args.output.resolve()
    source = Path(__file__).resolve().parents[1]
    assert not output.is_relative_to(source), "Run acceptance outside the source checkout"
    output.mkdir(parents=True)
    wheels = read_wheels(args.dist.resolve())
    report: dict[str, object] = {
        "status": "incomplete",
        "system": platform.system(),
        "machine": platform.machine(),
        "libc": platform.libc_ver(),
        "kernel": platform.release(),
        "macos": platform.mac_ver()[0],
        "source_commit": git(source, "rev-parse", "HEAD"),
        "source_worktree_dirty": bool(git(source, "status", "--porcelain")),
        "acceptance_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest(),
        "constraints_sha256": hashlib.sha256(args.constraints.read_bytes()).hexdigest(),
        "uv": run([uv, "--version"], cwd=output).stdout.strip(),
        "wheels": {
            x.path.name: hashlib.sha256(x.path.read_bytes()).hexdigest() for x in wheels.values()
        },
        "python_requested": args.python,
    }
    results: dict[str, object] = {}
    try:
        for mode in ("single", "aggregate"):
            results[mode] = exercise_mode(
                mode, output, wheels, args.constraints.resolve(), args.python
            )
        report["status"] = "passed"
    finally:
        report["modes"] = results
        (output / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    log(f"PASS all {platform.system()} acceptance modes; evidence written to result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
