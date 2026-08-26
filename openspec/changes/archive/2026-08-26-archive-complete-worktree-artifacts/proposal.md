## Why

`archive-requirement` currently moves selected documents out of each code worktree and leaves the requirement directory and its lifecycle split across archive and cleanup commands. The requirement's durable artifacts are already outside the code worktrees, so archiving can be made lossless and predictable by removing the child worktrees and moving the whole requirement directory as one unit.

## What Changes

- Make `archive-requirement` operate on the whole active requirement directory under `worktrees/`.
- Remove the requirement's child Git worktrees as part of archive; do not archive source code or Git worktree checkout metadata.
- Move the remaining requirement directory directly to `doc/<task-root-name>/` without inspecting, classifying, parsing, or distilling its contents.
- Preserve every remaining file, directory, relative path, and file type exactly as found.
- Validate the archive destination before destructive worktree removal and report the moved requirement directory plus removed worktrees.
- Remove doc-specific missing-document behavior and update lifecycle documentation and tests.

## Capabilities

### New Capabilities

- `complete-worktree-archive`: Archive a complete requirement directory after removing its code worktrees, without recognizing or transforming artifact contents.

### Modified Capabilities

None. The repository has no existing OpenSpec capability spec to modify; the lifecycle contract is defined as part of the new capability above.

## Impact

- Affects `packages/workspacectl/src/workspacectl/core.py` archive lifecycle logic, CLI arguments and result reporting, lifecycle documentation, and workspacectl tests.
- `archive-requirement` becomes the terminal archive operation for an active requirement directory; callers no longer need a separate cleanup step for those child worktrees.
- Git branches remain in their source repositories because removing a worktree does not delete its branch.
