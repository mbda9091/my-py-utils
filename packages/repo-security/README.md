# repo-security

Install repository-local Gitleaks, Semgrep and Presidio scanners with uv.
The CLI provides setup, read-only planning, repeat installation, recovery,
diagnostics, manual scanning and optional GitHub Actions.
**Git 2.54.0 or newer is required.** Setup rejects older, missing or unrecognized
Git versions before changing the target repository, acquiring its setup lock,
creating environments or downloading tools. There is no legacy hook backend or
fallback. The standalone shell entry checks Git before starting uv.

The previous 0.2.0 Linux x64 and native macOS arm64 validation is recorded in
[the Linux record](../../docs/repo-security-m3-validation.md) and
[the macOS record](../../docs/repo-security-macos-validation.md).
Those historical results do not validate this release's native hook migration.
Native Windows and additional architectures remain outside the accepted scope.

From this workspace, initialize with `make dev`, then:

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
uv-managed Python 3.13 environment, and enables only the repository-local
pre-commit hook with Git-native configuration:

```gitconfig
[hook "repo-security"]
    command = ./.security/hooks/pre-commit
    event = pre-commit
```

Existing named hooks, traditional hook files, permissions and `core.hooksPath`
remain intact. If another tool already uses the name, setup chooses a stable
unused name and records it locally. No traditional dispatcher or `10-security`
link is created. Only this tool's registration is moved to the end of the managed
local/worktree config on setup; other hooks retain their relative order.
Repeating setup keeps one registration and avoids writes when it is already current.
Git runs traditional hooks after configured hooks. Later registrations, includes
and command-scope settings may also run after security; setup does not reorder
those sources or require security to be the absolute final hook at runtime.
The scan covers the index when security is invoked, including earlier hooks'
staged changes, but cannot cover later hooks' changes. Scanning preserves Git's
commit-specific index, including `git commit --only`.

After deploying reviewed files, setup enables the hook before
installing tools; the hook blocks commits while setup is incomplete or locked.
Only successful real synthetic sample checks mark the installation ready.
First installation needs Git, uv and network access. Downloads may also populate uv's shared Python and
package caches. Business dependencies, the index, global Git configuration and
remote settings are not changed; setup does not fetch or scan history.

`--dry-run` reports additions, unchanged files, executable repairs, all detected
conflicts, Git settings and potential downloads. It makes no writes, creates no
locks or environments, executes no target scripts, and performs no downloads.
Conflicts return 2, including in preview mode. Existing hook managers coexist
through Git's native scheduler; setup does not wrap or replace their scripts.
An explicitly disabled pre-commit event, a changed owned command or an ambiguous
owned migration stops setup with an actionable error and preserves the setting.
Other tools' disabled hooks remain disabled. Preview does not run target hooks.

Register additional checks using Git's native `hook.<name>.command` and
`hook.<name>.event` configuration. Rerun setup to move this tool's own registration
after newly added registrations in the managed config. Any check failure blocks
the commit according to Git's scheduling rules; Git may still run other checks
after one fails. No runtime requirement forces other checks to precede security.

Setup recognizes unchanged resources with or without an installation manifest,
preserves their modification times and unrelated files, and reuses verified
dependencies. Supported schema-1, version-0.2.0 installations are migrated
automatically with a one-time migration to schema 2 and native registrations.
Only verified owned dispatchers and security links are removed. Existing project
members are preserved and called in their original order, with their original
arguments and fail-fast behavior, from a fixed-list project script registered as
an independent native hook. New checks use native registration, not directory
discovery. The generated fixed-list adapter is machine-local and ignored by Git,
because its original members remain in the local Git directory.
Unknown edits are preserved and reported rather than overwritten.
The CLI replaces older runtime files only when their bytes still
match the recorded installation. Existing registered repository rules are kept.
Unregistered custom files, unsupported versions and corrupt records remain
conflicts; there is no force or uninstall option.
**Pre-0.2.0 installations are not automatically upgraded to 0.3.0**: review the diff and
migrate intentionally, or install into a fresh repository.

The ignored, machine-local `.security/install-manifest.json` records release and
installed file hashes, native registration names and scope, migration members,
original local hooks settings, verified Gitleaks archive
and executable digests, and the last installation phase. Missing or damaged tools
can be repaired by rerunning the same setup. Python tool health checks inspect
actual launchers, imports and versions, rather than only installed package
metadata. A failed health check triggers one locked dependency reinstall and
reverification; persistent errors leave the installation incomplete. On failure it records incomplete
state and preserves generated files for retry, while removing normal temporary
files and releasing the lock. Once the pre-commit hook has been deployed and
enabled, a failed first installation or repair keeps it active and leaves the
installation incomplete, so ordinary commits are blocked until setup succeeds.
Concurrent external hook changes are preserved. No full filesystem or cache
rollback is promised. If a killed process leaves `.git/repo-security-setup.lock`,
confirm no setup is running before removing that lock manually. For linked
worktrees, resolve the private Git directory with `git rev-parse --absolute-git-dir`.

`doctor` reports incomplete state, missing or customized files, permissions,
native hook configuration and enablement, Gitleaks provenance, real tool versions, rule validity and synthetic
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

After cloning reviewed `.security` files, install with the Shell entry point:

```bash
.security/setup.sh
.security/run staged
.security/run head
.security/run history
```

In this monorepo, `make dev` already runs the Shell installer before syncing
workspace dependencies, so it is the normal initialization command for a fresh
clone. Other projects can call `.security/setup.sh` from their existing developer
initialization command to provide the same single-command workflow.

The Shell entry point requires Git and uv on PATH. It uses
`uv run --no-project --no-config --isolated --python 3.13` to start the stdlib
installer and enables hooks by default. uv selects or downloads a compatible
Python, so a separately installed system Python is not required. It skips the
business project's dependencies and environment; arguments such as `--help` are
forwarded to the installer. Missing uv is an explicit error; the script does not
install uv automatically.

The standalone installer uses the same stdlib installation engine as the CLI.
A clone without the local manifest adopts its reviewed on-disk files as its
snapshot; it never replaces source files or downloads template updates. The CLI
compares against its bundled release defaults, so custom snapshots remain visible
as differences. After pulling updated security files, run the same project
initialization command (`make dev` here). The standalone installer renews local
registration only for files matching both Git HEAD and the index, or the approved
targets of an interrupted migration. Uncommitted working or staged edits stop
setup. It recognizes exact shipped older dispatchers and removes only the
registered, unchanged built-in legacy pre-push hook. Other checks are preserved.

Before changing a managed installation, setup automatically saves its old record,
affected files, owned entry point and original local hook settings under
`.git/repo-security-backups/upgrade-*/`. This location is private and never part of
a commit. Interrupted migrations retain their original backup and can be retried
with the same command; persistent errors keep commits blocked. A shell guard
checks the setup lock before invoking Python, including during runtime updates.
No full filesystem or dependency rollback is promised. Unknown or modified hooks
are never silently replaced. Standalone setup without
`--install-hooks` is an advanced tools-only Python entry point that verifies tools and
leaves Git settings unchanged (used by CI); migrating an older installation
requires the normal hook-enabled entry point. Do not commit the installation manifest,
virtual environment or binaries.

`scan` defaults to `staged`; `head` scans HEAD, and `history` scans locally
reachable commits without fetching. Shallow repositories show an explicit coverage
warning. Findings return 1; argument/environment/configuration errors return 2;
either blocks hooks. Doctor returns 1 for an unhealthy installation and 2 for
execution errors. Paths, lines and finding types are reported, not matched values.
No automatic pre-push hook is installed, so pushing does not rescan commits.
Use manual history scanning when required. Full history checks can be expensive.

The pre-commit entry point uses `staged --require-ready`. Before scanning it
checks the ready installation record, setup lock, unique enabled native registrations,
registered file digests and permissions, Gitleaks provenance, scanner entry
points and pinned Python package versions. Missing tools/configuration,
incomplete setup, modified managed files or scanner errors reject the commit.
This is a lightweight readiness check followed by one staged snapshot scan;
it does not run doctor's synthetic scans on every commit. Reviewed changes to
managed runtime or policy files require renewing the local registration.

The guarantee requires Git to invoke the installed hook. A new clone that has
never enabled hooks, a deleted/disabled native registration, a missing/non-executable
security entry point, an unsupported Git version, or explicit `git commit --no-verify` cannot be intercepted by
repository hook code. Initialize each clone with setup; there is no claim that an
uninitialized clone blocks its first commit. This follows
[Git's hook invocation rules](https://git-scm.com/docs/githooks).
A pass is not a complete audit.

Rules remain limited starter Python/JS/TS patterns and offline PII recognizers,
without NLP model downloads or cloud rules. Binary/unsupported text encodings
produce PII coverage warnings; submodules and files over 5 MiB block. External
scanners time out after 60 seconds per snapshot. Review precise rule exceptions
yourself; setup does not add allowlists automatically. Bare repositories and
submodule checkouts are unsupported. Linked worktrees require an already enabled
`extensions.worktreeConfig` and use their own `config.worktree`, setup lock,
installation record and scanner environment. Setup does not enable the shared
extension or delete hooks owned by the main checkout; a shared old security
entry must first be migrated in its owning checkout. Native Windows is unsupported;
The observed WSL2 Linux environment does not establish native Windows or
Windows-mounted filesystem support.

PII detection distinguishes numeric IDs in GitHub artifact URLs and the service
account in GitHub SCP-style SSH remotes from ordinary phone/email text. Other
hosts, query parameters and adjacent sensitive values remain covered.

User home paths are detected by default as `LOCAL_USER_PATH`, including macOS
`/Users/<username>/…`, Linux `/home/<username>/…`, and Windows
`C:\Users\<username>\…` with a literal username. Detection includes escaped
Windows paths in source strings and JSON, as well as code comments, configuration
and documentation. These are offline pattern checks, not an assessment of every
directory name: arbitrary private project names and custom home roots are not
covered. Existing repositories receive the default recognizer when their managed
scanner runtime is upgraded; existing allowlists and custom patterns are retained.

Use `~`, `$HOME`, `%USERPROFILE%`, or explicit username placeholders such as
`<username>` instead of personal absolute paths. Those forms are not flagged.
Findings report only the file, line and entity type, never the matched username
or directory. An intentional literal path can use a narrow `LOCAL_USER_PATH`
entry in `pii_allowlist`; its regex must match the detected prefix through the
username, without the remaining subdirectories.

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
executes only their help commands, and exercises guarded pre-commit hooks,
manual history scanning and the absence of automatic push scanning using real
local Git repositories. It creates disposable synthetic findings; do not publish
its fixture repositories. Commands and artifact hashes are in the acceptance report.
