## 1. Requirement-Level Archive

- [x] 1.1 Add archive preflight for active task-root containment, manifest worktree bindings, destination collisions, and dirty-worktree safety.
- [x] 1.2 Update `archive-requirement` to remove all manifest-listed child Git worktrees and then move the complete task-root directory to `doc/<task-root-name>/`.
- [x] 1.3 Preserve branch retention, report removed worktrees and the final directory move, and leave the task root available for recovery if the final move fails.
- [x] 1.4 Remove doc-tree scanning, per-file movement, document-missing guards, and content-specific archive logic.

## 2. CLI, Lifecycle, and Tests

- [x] 2.1 Add or reuse an explicit archive force option for dirty child worktrees and remove the obsolete `--discard-missing-docs` behavior.
- [x] 2.2 Add integration coverage for multi-project task-root moves, arbitrary requirement-level files/directories, destination collisions before deletion, dirty-worktree safety, and branch retention.
- [x] 2.3 Update cleanup behavior and tests so active unarchived task roots remain manageable while archived task roots do not require a second cleanup.
- [x] 2.4 Verify human-readable and JSON results report the source task root, archive destination, and every removed worktree.

## 3. Documentation and Verification

- [x] 3.1 Update `docs/workspacectl/execution-runbook.md`, `CONTEXT.md`, and affected ADRs to describe task-root archive moves and child-worktree removal.
- [x] 3.2 Update CLI help, agent templates, and README references that currently describe archive as doc-tree extraction followed by cleanup.
- [x] 3.3 Run `uv run ruff check .`, `uv run pyright`, `uv run pytest`, and `uv build --all-packages`; resolve regressions without reintroducing document recognition.
