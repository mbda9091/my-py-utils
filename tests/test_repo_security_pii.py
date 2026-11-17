from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

from tests import test_repo_security as fixtures
from tests.test_repo_security import git

real_scanners = fixtures.real_scanners
repository = fixtures.repository
PROJECT = Path(__file__).resolve().parents[1]
AUTHOR_PATH = "packages/video2audio/pyproject.toml"


def author_email() -> str:
    metadata = tomllib.loads((PROJECT / AUTHOR_PATH).read_text())
    return metadata["project"]["authors"][0]["email"]


def scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- real installed scanners in a disposable repository
        [str(root / ".security/run"), "staged"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_real_default_user_paths_and_placeholder_boundaries(real_scanners: Path) -> None:
    mac = "/Users/" + "synthetic-person"
    linux = "/home/" + "synthetic-person"
    windows = "C:\\Users\\" + "synthetic-person"
    cases = [
        (mac, True),
        (linux + "/private/repo", True),
        ("cache = '" + mac + "/cache'", True),
        ("# directory: " + linux + "/docs", True),
        ("file://" + mac + "/notes", True),
        (windows + "\\private\\repo", True),
        (json.dumps(windows + "\\repo"), True),
        ("d:/users/" + "Synthetic Person/repo", True),
        ("/home/" + "测试用户/repo", True),
        ("/home/" + "first.last-1_2/repo", True),
        ("safe\npath = '" + linux + "/repo'\n", True),
        ("~/private/repo", False),
        ("$HOME/private/repo", False),
        ("${HOME}/private/repo", False),
        ("%USERPROFILE%\\private\\repo", False),
        ("/Users/<username>/repo", False),
        ("/home/{username}/repo", False),
        ("/home/$USER/repo", False),
        ("/home/${USER}/repo", False),
        ("C:\\Users\\<username>\\repo", False),
        ("C:\\Users\\%USERNAME%\\repo", False),
        ("/Users/", False),
        ("/home/", False),
        ("src" + linux + "/repo", False),
        ("/srv" + linux + "/repo", False),
        ("https://example.com" + linux + "/repo", False),
        ("/usr/local/bin/tool", False),
    ]
    check = """
import json, re, sys
sys.path.insert(0, sys.argv[1])
from pii import detect, make_recognizers
from pathlib import Path
cases, project_policy = json.load(sys.stdin)
default_policy = json.loads(Path(sys.argv[1], 'policy.json').read_text())
for policy in (default_policy, project_policy):
    assert not policy['custom_patterns']
    recognizers = make_recognizers(policy)
    for number, (text, expected) in enumerate(cases):
        findings = detect(text, recognizers, policy)
        assert bool(findings) == expected, (number, findings, expected)
        assert all(entity == 'LOCAL_USER_PATH' for _, entity in findings), number
    assert detect(cases[10][0], recognizers, policy) == [(2, 'LOCAL_USER_PATH')]
    policy['pii_allowlist'].append({
        'entity': 'LOCAL_USER_PATH',
        'regex': re.escape(cases[0][0]),
        'paths': ['approved.txt'],
    })
    assert detect(cases[0][0], recognizers, policy, path='approved.txt') == []
    assert detect(cases[0][0], recognizers, policy, path='other.txt')
print(json.dumps({'cases': len(cases)}))
"""
    result = subprocess.run(  # noqa: S603 -- actual offline Presidio runtime in a disposable repo
        [
            str(real_scanners / ".security/.venv/bin/python"),
            "-I",
            "-B",
            "-c",
            check,
            str(real_scanners / ".security"),
        ],
        input=json.dumps([cases, json.loads((PROJECT / ".security/policy.json").read_text())]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cases"] == len(cases)


def test_real_default_user_path_scan_and_commit_redact_values(real_scanners: Path) -> None:
    paths = [
        "/Users/" + "synthetic-person/private",
        "/home/" + "synthetic-person/private",
        "C:\\Users\\" + "synthetic-person\\private",
    ]
    target = real_scanners / "settings.json"
    target.write_text(json.dumps(paths, indent=2) + "\n")
    git(real_scanners, "add", target.name)
    # A safe working copy must not hide sensitive paths already in the index.
    target.write_text('["~/private"]\n')
    result = scan(real_scanners)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert output.count("[LOCAL_USER_PATH]") == len(paths)
    assert "synthetic-person" not in output
    commit = git(real_scanners, "commit", "-m", "Block private user paths", check=False)
    output = commit.stdout + commit.stderr
    assert commit.returncode != 0
    assert "[LOCAL_USER_PATH]" in output
    assert "synthetic-person" not in output
    git(real_scanners, "add", target.name)
    clean = scan(real_scanners)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert git(real_scanners, "commit", "-m", "Allow portable paths").returncode == 0


def test_real_pii_context_and_exact_path_boundaries(real_scanners: Path) -> None:
    phone = "139" + "12345678"
    ssh_user = "git@" + "github.com"
    other_email = "private@" + "company.org"
    artifact = "https://github.com/acme/repo/actions/runs/42/artifacts/" + phone
    cases: list[tuple[str, str | None, list[str]]] = [
        (artifact, None, []),
        ("[" + artifact + "](" + artifact + ")", None, []),
        ("https://github.com/acme/repo/actions/artifacts/" + phone, None, []),
        ("phone = " + phone, None, ["CN_MOBILE_PHONE"]),
        (artifact + "?phone=" + phone, None, ["CN_MOBILE_PHONE"]),
        (artifact + " phone=" + phone, None, ["CN_MOBILE_PHONE"]),
        (artifact + "/notes", None, ["CN_MOBILE_PHONE"]),
        (artifact.replace("github.com", "github.com.evil.org"), None, ["CN_MOBILE_PHONE"]),
        ("https://evil.org/?redirect=" + artifact, None, ["CN_MOBILE_PHONE"]),
        (ssh_user + ":acme/repo.git", None, []),
        ("contact " + ssh_user, None, ["EMAIL_ADDRESS"]),
        (ssh_user + ":acme/repo.git " + other_email, None, ["EMAIL_ADDRESS"]),
        (author_email(), AUTHOR_PATH, []),
        (author_email(), "contact.txt", ["EMAIL_ADDRESS"]),
        (author_email(), "nested/" + AUTHOR_PATH, ["EMAIL_ADDRESS"]),
        (author_email(), None, ["EMAIL_ADDRESS"]),
        (other_email, AUTHOR_PATH, ["EMAIL_ADDRESS"]),
        (author_email() + " " + phone, AUTHOR_PATH, ["CN_MOBILE_PHONE"]),
    ]
    check = """
import json, sys
sys.path.insert(0, sys.argv[1])
from pii import detect, make_recognizers
policy, cases = json.load(sys.stdin)
recognizers = make_recognizers(policy)
for number, (text, path, expected) in enumerate(cases):
    actual = sorted({entity for _, entity in detect(text, recognizers, policy, path=path)})
    assert actual == expected, (number, actual, expected)
for paths in ([], {}, "contact.txt", [None], [""]):
    invalid = json.loads(json.dumps(policy))
    invalid["pii_allowlist"][0]["paths"] = paths
    try:
        make_recognizers(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("Malformed scoped allowlist was accepted")
print(json.dumps({"cases": len(cases)}))
"""
    result = subprocess.run(  # noqa: S603 -- isolated interpreter with the actual Presidio runtime
        [
            str(real_scanners / ".security/.venv/bin/python"),
            "-I",
            "-B",
            "-c",
            check,
            str(real_scanners / ".security"),
        ],
        input=json.dumps([json.loads((PROJECT / ".security/policy.json").read_text()), cases]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cases"] == len(cases)


def test_real_scan_keeps_public_author_exception_in_its_exact_file(real_scanners: Path) -> None:
    policy = real_scanners / ".security/policy.json"
    policy.write_bytes((PROJECT / ".security/policy.json").read_bytes())
    target = real_scanners / AUTHOR_PATH
    target.parent.mkdir(parents=True)
    target.write_text('email = "' + author_email() + '"\n')
    (real_scanners / "contact.txt").write_text(author_email())
    git(real_scanners, "add", AUTHOR_PATH, "contact.txt")
    result = scan(real_scanners)
    assert result.returncode == 1
    assert "Presidio: contact.txt:" in result.stdout
    assert "Presidio: " + AUTHOR_PATH + ":" not in result.stdout
    assert author_email() not in result.stdout + result.stderr
    git(real_scanners, "rm", "--cached", "contact.txt")
    assert scan(real_scanners).returncode == 0
    target.write_text('email = "' + "private@" + 'company.org"\n')
    git(real_scanners, "add", AUTHOR_PATH)
    result = scan(real_scanners)
    assert result.returncode == 1
    assert "Presidio: " + AUTHOR_PATH + ":" in result.stdout


def test_real_gitleaks_exception_requires_both_exact_fixture_and_path(real_scanners: Path) -> None:
    config = real_scanners / ".security/gitleaks.toml"
    config.write_bytes((PROJECT / ".security/gitleaks.toml").read_bytes())
    password = "Aa0123" + "456789abcdefgh"  # public legacy archive fixture
    allowed = "tests/test_private_pack_platform.py"
    paths = [allowed, "other.py", "nested/" + allowed]
    for name in paths:
        target = real_scanners / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'archive_password = "{password}"\n')
    with (real_scanners / allowed).open("a") as stream:
        stream.write(f'other_password = "{password}X"\n')
    git(real_scanners, "add", *paths)
    result = scan(real_scanners)
    assert result.returncode == 1
    assert result.stdout.count("Gitleaks:") == len(paths)
    assert password not in result.stdout + result.stderr
    git(real_scanners, "rm", "--cached", *paths[1:])
    (real_scanners / allowed).write_text(f'archive_password = "{password}"\n')
    git(real_scanners, "add", allowed)
    result = scan(real_scanners)
    assert result.returncode == 0, result.stdout + result.stderr
