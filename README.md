# my-py-utils

Personal Python CLI utilities managed as a uv workspace.

All commands in this README are intended to run from this repository root.

## Layout

```text
packages/
  my-py-utils-common/   Shared helpers used by CLI packages
  hello-cli/            Small example CLI package
  video2audio/          CLI for extracting audio from video files with ffmpeg
  mac-cache-cleaner/    CLI for cleaning macOS build caches
  system-alarm/         Python API and CLI for native macOS Clock alarms
  node-modules-cleaner/ CLI for recursively deleting node_modules directories
  skill-sync/           Personal skill repository management and symlink installation
  private-pack/        Encrypted per-item 7z archives with a protected manifest
  repo-security/       Repository-local scanners, setup recovery and Git hooks
  codex-hook-ctl/      Record and inject custom Codex hooks by name
  codex-session-export/ Export Codex sessions as normalized rollout JSONL
  session-scan/        Discover DSH/Codex files; lazy message adapters (M1)
  workspacectl/         Controller for requirement and collaboration Git worktrees
  my-py-utils-cli/      Aggregate installer for all workspace CLI commands
tests/                  Workspace-level tests
```

See [docs/uv-workspace-notes.md](docs/uv-workspace-notes.md) for the concepts
behind this layout.

skill-sync 个人技能仓库的配置、纳管与安装方案见
[docs/skill-sync-personal-repository-plan.md](docs/skill-sync-personal-repository-plan.md)，
当前可用范围见 [使用说明](packages/skill-sync/README.md)。

Codex 会话导出：
[方案文档](docs/codex-session-export-design.md) ·
[实施文档](docs/codex-session-export-implementation.md)。

macOS 原生闹钟（首次需要配置快捷指令）：
[system-alarm 使用说明](<packages/system-alarm/README.md>)。

## Common Commands

```bash
make dev
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
uv run --package hello-cli hello-util --name Codex
uv run --package video2audio video2audio --help
uv run --package mac-cache-cleaner mac-cache-cleaner --help
uv run --package node-modules-cleaner node-modules-cleaner ~/Code --dry-run
uv run --package codex-hook-ctl codex-hook-ctl --help
uv run --package codex-session-export codex-session-export --directory ~/Code/my-py-utils --output ~/Downloads/codex-export
uv run --package session-scan session-scan --help
uv run --package skill-sync skill-sync --help
uv run --package workspacectl workspacectl --help
uv run --package private-pack private-pack --help
uv run --package repo-security repo-security --help
uv run --package system-alarm system-alarm create --time 07:30 --label "起床"
uv run --package skill-sync skill-sync init --repo ~/Code/my-skills
```

## Initialize This Project

Initialize or refresh the development environment after cloning:

```bash
make dev
```

This configures repository security checks and Git hooks, then installs all
workspace dependencies with uv. Both steps must succeed for initialization to
complete. Git, uv and make must be available; the security installer manages its
own Python environment. Repeating the command preserves independent hook checks.
Running `uv sync` directly only installs workspace dependencies.

Run the standard checks:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

For package or build changes, also verify packaging:

```bash
uv build --all-packages
```

## Install All CLI Commands

From the repository root:

```bash
uv tool install --editable packages/my-py-utils-cli
```

Refresh an existing installation after code changes:

```bash
uv tool install --editable packages/my-py-utils-cli --force
```

This installs all workspace commands into your uv tool bin directory:

```text
hello-util
repo-security
video2audio
mac-cache-cleaner
node-modules-cleaner
codex-hook-ctl
codex-session-export
session-scan
skill-sync
workspacectl
private-pack
system-alarm
```

Encrypted archive usage: [private-pack README](packages/private-pack/README.md).

## Add A New CLI

1. Create `packages/<cli-name>/pyproject.toml`.
2. Put code under `packages/<cli-name>/src/<import_name>/`.
3. Add a command under `[project.scripts]`.
4. Add the package to `packages/my-py-utils-cli` dependencies, workspace sources, and scripts.
5. If it uses shared helpers, depend on `my-py-utils-common` and add:

```toml
[tool.uv.sources]
my-py-utils-common = { workspace = true }
```
