# repo-security

Install repository-local Gitleaks, Semgrep and Presidio scanners with uv.
The CLI provides setup, read-only planning, repeat installation, recovery,
diagnostics, manual scanning and optional GitHub Actions. This release is
validated on **Linux x64** (Ubuntu 22.04.5 userland, glibc 2.35, WSL2 Linux
kernel in the acceptance environment). Native **macOS arm64** acceptance,
including the exact host and Python combinations, is recorded in
[the macOS validation record](../../docs/repo-security-macos-validation.md).
Intel macOS, Linux arm64 and native Windows have not been accepted for this release.
Retained download checksums for other platforms are not evidence of support.

From this workspace, use `uv sync --all-groups --all-packages`, then:

```bash
uv run --package repo-security repo-security setup /path/to/repo --dry-run
uv run --package repo-security repo-security setup /path/to/repo
uv run --package repo-security repo-security setup /path/to/repo --github-actions
uv run --package repo-security repo-security doctor /path/to/repo
uv run --package repo-security repo-security scan /path/to/repo --mode staged
uv run --package repo-security repo-security scan /path/to/repo --mode history
```

Setup accepts a repository root or a directory inside it. It writes `.security/`,
merges only necessary `.gitignore` entries, installs locked scanners in a separate
uv-managed Python 3.13 environment, and enables repository-local pre-commit and
pre-push hooks after real synthetic sample checks pass. First installation needs
Git, uv and network access. Downloads may also populate uv's shared Python and
package caches. Business dependencies, the index, global Git configuration and
remote settings are not changed; setup does not fetch or scan history.

`--dry-run` reports additions, unchanged files, executable repairs, all detected
conflicts, Git settings and potential downloads. It makes no writes, creates no
locks or environments, executes no target scripts, and performs no downloads.
Conflicts return 2, including in preview mode. Existing hook managers are not
rewritten or chained automatically; the error identifies the configuration
source and commands to integrate manually.

Same-version setup recognizes unchanged resources with or without an installation
manifest, preserves their modification times and unrelated files, and reuses
verified dependencies. Custom rules and different-version templates are reported
as conflicts without replacement. There is no force, upgrade or uninstall option.
**M1 installations are not automatically upgraded to 0.2.0**: review the diff and
migrate intentionally, or install into a fresh repository.

The ignored, machine-local `.security/install-manifest.json` records release and
installed file hashes, original local hooks settings, verified Gitleaks archive
and executable digests, and the last installation phase. Missing or damaged tools
can be repaired by rerunning the same setup. On failure it records incomplete
state and preserves generated files for retry, while removing normal temporary
files and releasing the lock. New hooks are not left enabled after an ordinary
failed first installation; existing protection is kept on failed repair.
Concurrent external hook changes are preserved. No full filesystem or cache
rollback is promised. If a killed process leaves `.git/repo-security-setup.lock`,
confirm no setup is running before removing that lock manually.

`doctor` reports incomplete state, missing or customized files, permissions,
hooks, Gitleaks provenance, real tool versions, rule validity and synthetic
sample detection. It does not install or repair anything, and skips untrusted
modified runtime scripts/binaries. Checks use private temporary samples, not
business code; Python bytecode writes are disabled. Custom rule differences are
visible and do not alone make a valid configuration unhealthy. A healthy doctor
result does not mean the repository's business code is safe.

`--github-actions` creates `.github/workflows/repo-security.yml` only when explicitly
requested. Existing unrelated workflows are preserved; different same-name content
is a conflict. An already managed workflow remains managed on later setup calls
without the flag. Actions are pinned to verified commit SHAs, checkout permissions
are read-only, credentials are not persisted, and the full local reachable history
is scanned. Generating YAML does not enable branch protection or GitHub Push
Protection, and is not evidence of a successful remote CI run.

After cloning reviewed `.security` files, the independent entry points also work:

```bash
python3 .security/setup.py --install-hooks
.security/run staged
.security/run head
.security/run history
```

The standalone installer uses the same stdlib installation engine as the CLI.
A clone without the local manifest adopts its reviewed on-disk files as its
snapshot; it never replaces source files or downloads template updates. The CLI
compares against its bundled release defaults, so custom snapshots remain visible
as differences. Standalone setup without `--install-hooks` verifies tools and
leaves Git settings unchanged (used by CI). Do not commit the installation manifest,
virtual environment or binaries.

`scan` defaults to `staged`; `head` scans HEAD, and `history` scans locally
reachable commits without fetching. Shallow repositories show an explicit coverage
warning. Findings return 1; argument/environment/configuration errors return 2;
either blocks hooks. Doctor returns 1 for an unhealthy installation and 2 for
execution errors. Paths, lines and finding types are reported, not matched values.
Pre-push covers intermediate commits, multiple refs and new-ref ancestors; ref
deletions introduce no snapshots. Full history checks can be expensive. Hooks can
be bypassed; a pass is not a complete audit.

Rules remain limited starter Python/JS/TS patterns and offline PII recognizers,
without NLP model downloads or cloud rules. Binary/unsupported text encodings
produce PII coverage warnings; submodules and files over 5 MiB block. External
scanners time out after 60 seconds per snapshot. Review precise rule exceptions
yourself; setup does not add allowlists automatically. Bare repositories, linked
worktrees and submodule checkouts are unsupported. Native Windows is unsupported;
The observed WSL2 Linux environment does not establish native Windows or
Windows-mounted filesystem support.

PII detection distinguishes numeric IDs in GitHub artifact URLs and the service
account in GitHub SCP-style SSH remotes from ordinary phone/email text. Other
hosts, query parameters and adjacent sensitive values remain covered.
Project-specific `pii_allowlist` entries may include `paths`, a non-empty list of
exact repository-relative file paths. The entity and full value regex must also
match; omitting `paths` keeps the existing global behavior. Record the reviewed
reason alongside each project exception. For example:

```json
{
  "entity": "EMAIL_ADDRESS",
  "regex": "maintainer@example[.]com",
  "paths": ["pyproject.toml"],
  "reason": "Published package author contact in package metadata."
}
```

Keep repository-specific fixture exceptions out of the shipped defaults.
This repository's Gitleaks exception combines the exact public fixture password
and test path with `condition = "AND"`; other values in that file and the same
value in other files are still checked. The syntax follows the
[Gitleaks 8.30.1 configuration reference](https://github.com/gitleaks/gitleaks/blob/v8.30.1/README.md#configuration).
Repository-controlled `.gitleaksignore` and inline `gitleaks:allow` directives
cannot suppress findings; the ignore file's original content is still scanned.

The package has no scanner runtime dependencies. `templates/manifest.json` records
original source hashes and shipped resource hashes. Scanner inputs use `.template`
suffixes and are compiled and exercised in their separate environment; the shared
`templates/manage.py` engine is regular typed Python checked by Ruff/Pyright and
copied with an explicit hash. No binaries, virtual environments or caches ship in
wheels. See [NOTICE](NOTICE.md), [the plan](../../docs/repo-security-plan.md) and
[M2 validation](../../docs/repo-security-m2-validation.md) and
[Linux release acceptance](../../docs/repo-security-m3-validation.md).

To repeat release acceptance, build fresh workspace wheels, export runtime
constraints from the frozen root lockfile, and run
`tests/repo_security_acceptance.py` with its `--dist`, `--constraints`, `--output`
and `--python` arguments. The script installs single and aggregate wheels into
separate environments outside the checkout, loads all aggregate entry points,
executes only their help commands, and exercises repo-security with real local
Git commit/push hooks. It creates disposable synthetic findings; do not publish
its fixture repositories. Commands and artifact hashes are in the acceptance report.
