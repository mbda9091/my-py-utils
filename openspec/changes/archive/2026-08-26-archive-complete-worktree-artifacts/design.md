## Context

The active requirement directory under `worktrees/` contains the requirement-level durable artifacts, while its child directories are Git worktree checkouts used only for implementation. The archive operation should preserve the former and discard the latter as a single lifecycle action. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**

- Treat the requirement directory, not individual files or document types, as the archive unit.
- Remove all manifest-listed child worktrees before moving the requirement directory.
- Move the remaining requirement directory in one operation to `doc/<task-root-name>/`.
- Validate the destination before destructive worktree removal.
- Preserve branch retention and report the complete archive action in human and JSON modes.

**Non-Goals:**

- No Git-tracked versus untracked file inventory.
- No content parsing, extension rules, document classification, or distillation.
- No archive compression or generated snapshot.
- No branch deletion.

## Decisions

### 1. Archive the task root as the unit

Resolve the task root from the manifest and use `doc/<task-root-name>/` as its sole destination. After preflight, remove each registered child worktree through the existing Git integration, then move the task-root directory itself. This eliminates per-file movement and leaves all requirement-level artifacts intact.

### 2. Keep destructive worktree removal explicit and safe

Reuse the existing dirty-worktree safety policy. Archive should reject dirty child worktrees unless an explicit force option is supplied, because removal discards code checkout changes. The destination and manifest bindings are validated before any child worktree is removed.

### 3. Preserve the requirement directory exactly

The move operation does not enumerate or interpret the remaining files. The task manifest and any other requirement-level entries move with the directory, preserving their relative paths and filesystem types. The child worktree directories are absent because they were removed before the move.

### 4. Make archive terminal for active worktrees

Archive owns removal of child worktrees. Cleanup remains available for active task roots that have not been archived, but it must not be required or rerun against the moved archive. Existing branch retention remains unchanged because `git worktree remove` does not delete a local branch.

### 5. Prefer preflight over rollback

Check task-root containment, manifest validity, repository bindings, worktree registration, dirty state, and destination collisions before destructive actions. A failed directory move after successful worktree removal cannot restore code safely; therefore the implementation should make post-removal move failure rare and report the source task-root state clearly rather than pretending to provide a full rollback.

## Risks / Trade-offs

- [Forced archive discards uncommitted code checkout changes] -> Require an explicit force option and report every removed worktree.
- [A filesystem move can fail after worktree removal] -> Preflight all discoverable conditions, use one final directory move, and leave the remaining task root in place for manual recovery if that move fails.
- [Existing callers expect archive followed by cleanup] -> Keep cleanup compatible for active roots, update templates and runbook to make archive terminal, and add lifecycle tests.
- [An existing archive destination may contain unrelated data] -> Reject non-empty destinations before any worktree removal.

## Migration Plan

1. Update the controller to preflight and remove child worktrees, then move the entire task root.
2. Add an archive `--force` option if needed by the existing dirty-worktree policy; remove doc-specific flags and result fields.
3. Update integration tests for multi-project moves, arbitrary task-root artifacts, collision preflight, dirty worktree safety, branch retention, and cleanup compatibility.
4. Update the execution runbook, context, ADRs, templates, and CLI help to describe task-root moves rather than doc-tree extraction.
5. Run the normal workspace checks and package build.

Existing archived directories are not migrated; the new operation applies to active task roots only.

## Open Questions

None. The requirement-level durable artifacts are assumed to be outside child code worktrees as specified for this change.
