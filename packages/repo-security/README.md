# repo-security

Repository-local Gitleaks, Semgrep and Presidio scanners, installed with uv.
This is the **M1 preview**: first installation into a fresh `.security/` and
manual Git snapshot scanning are available. Repeat installation, `--dry-run`,
`doctor`, recovery manifests and optional CI generation are planned for M2.

From this workspace, install with `uv sync --all-groups --all-packages`, then:

```bash
uv run --package repo-security repo-security setup /path/to/repo
uv run --package repo-security repo-security scan /path/to/repo --mode staged
uv run --package repo-security repo-security scan /path/to/repo --mode history
```

Setup writes `.security/`, appends only necessary entries to `.gitignore`,
downloads pinned scanners, and uses a separate uv-managed Python 3.13 environment.
It verifies a clean sample and synthetic findings from all three scanners before
enabling repository-local pre-commit/pre-push hooks. It does not stage files,
fetch, scan business history automatically, or modify global Git configuration.
Review generated scripts and rules before committing them.

M1 refuses existing `.security/`, existing hooks, bare repositories, linked
worktrees and submodule checkouts. Installation failure can leave generated
files and a partial environment; it does not enable new hooks. Inspect these
before manually retrying the generated standalone installer. Full recovery is
scheduled for M2. Setup requires Git, uv, network access and Linux/macOS x64 or
arm64; **only Linux x64 has been exercised in M1**. Other platforms are untested.
Use WSL on Windows; native Windows is unsupported.

The generated repository also works without this CLI:

```bash
# After cloning a repository containing the reviewed .security files:
python3 .security/setup.py --install-hooks
.security/run staged
.security/run head
.security/run history
```

`scan` defaults to `staged`; `head` scans HEAD, and `history` scans locally
reachable history without fetching. Findings return 1; environment/configuration
errors return 2; either blocks hooks. Outputs show paths, locations and types,
never the matched values. A full history scan can be expensive, particularly on
first push of a new ref. Hooks can be bypassed; a pass is not a complete audit.

Rules are deliberately limited: Semgrep covers starter Python/JS/TS patterns;
Presidio uses offline patterns without NLP downloads. Binary/unsupported text
encodings produce PII coverage warnings; submodules and files over 5 MiB block.
External scanners time out after 60 seconds per snapshot. Review precise rule
exceptions yourself; the tool does not add allowlists automatically.

The packaged `templates/manifest.json` records original source hashes and shipped
resource hashes separately. Template inputs use `.template` suffixes because
they are deployed into an independent scanner environment, not imported into
the CLI environment. Their syntax and behavior are checked by template compilation
and real scanner integration tests. No scanner binaries, virtual environments,
or caches are shipped. See [NOTICE](NOTICE.md) for provenance and distribution
boundaries, and [the plan](../../docs/repo-security-plan.md) for the full scope.
