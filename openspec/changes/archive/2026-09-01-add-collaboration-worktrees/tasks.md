## 1. Typed Task Foundation

- [x] 1.1 Add task-kind constants and manifest helpers that validate explicit kinds while treating an absent kind as `requirement`.
- [x] 1.2 Update requirement manifest creation to write `kind: requirement` without rewriting or rejecting legacy manifests.
- [x] 1.3 Add focused tests for typed requirement manifests, legacy fallback, invalid kinds, and unchanged existing requirement lifecycle behavior.

## 2. Collaboration Input and Git Preflight

- [x] 2.1 Add parsing and validation for repeatable `project-id:branch` mappings, including missing parts, invalid Git branch names, unknown projects, and duplicate project ids.
- [x] 2.2 Add Git helpers to fetch an exact `origin` branch and inspect local-branch existence, upstream, worktree occupancy, and ahead/behind/diverged ancestry.
- [x] 2.3 Add tests covering missing remote branches, missing and compatible local branches, fast-forward and ahead states, upstream conflicts, divergence, and branches occupied by another worktree.

## 3. Collaboration Creation

- [x] 3.1 Add the `create-collaboration` CLI with required `--slug` and repeatable `--project-branch` options plus `--task-date`, existing hook options, and `--json`.
- [x] 3.2 Implement full multi-project preflight before task-root creation and prepare same-named local tracking branches without reset, rebase, force-update, or push.
- [x] 3.3 Reuse the worktree preparation pipeline to create `<project-id>-<slug>` worktrees, exclusions, local guidance links, project hooks, and collaboration-specific generated AGENTS.
- [x] 3.4 Persist the typed collaboration manifest and return matching human-readable and JSON summaries with every project, branch, and worktree path.
- [x] 3.5 Implement reverse-order rollback for created worktrees, generated task-root state, and only the local branches created by the failed invocation.
- [x] 3.6 Add end-to-end tests for multi-repository creation with different branches, directory and manifest contents, hook behavior, slug collisions, and rollback after later-project or hook failure.

## 4. Workspace Lifecycle Integration

- [x] 4.1 Make generated AGENTS and `update` dispatch by task kind, and test that collaboration refresh changes no project mapping, branch, or worktree path.
- [x] 4.2 Make human and JSON `status` report collaboration identity and project-to-branch mappings while preserving existing requirement output.
- [x] 4.3 Extend `doctor` validation and tests for collaboration identity, unknown projects, missing/unregistered worktrees, path containment, and branch mismatches.
- [x] 4.4 Verify `cleanup` handles collaboration manifests, dirty-worktree safeguards, pre-clean hooks, forced cleanup, and branch preservation.
- [x] 4.5 Add early kind guards and no-mutation tests for collaboration task roots passed to `extend-requirement` or `archive-requirement`.

## 5. Documentation and Verification

- [x] 5.1 Update the package README, workspace context, execution runbook, packaged agent templates, and an ADR to document collaboration creation, safety rules, immutable scope, and cleanup-only completion.
- [x] 5.2 Run `uv run ruff check .`, `uv run pyright`, and `uv run pytest`, fixing any regressions.
- [x] 5.3 Run `uv build --all-packages` and smoke-test `uv run --package workspacectl workspacectl --help` plus representative human and JSON collaboration commands.
