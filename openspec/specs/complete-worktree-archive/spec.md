# complete-worktree-archive Specification

## Purpose

Archive a requirement's complete durable directory after its code worktrees are removed, without inspecting or transforming any artifact contents.

## Requirements

### Requirement: Archive the complete requirement directory

`archive-requirement` SHALL validate an active requirement directory under `worktrees/`, remove every child Git worktree recorded in its task manifest, and move the remaining requirement directory as one unit to `doc/<task-root-name>/`. Source code, child worktree checkout metadata, and child worktree directories SHALL not be included in the archive. Every file and directory that remains in the requirement directory SHALL be moved with it.

#### Scenario: Archive a multi-project requirement

- **WHEN** a task root contains a task manifest, durable requirement artifacts outside its child worktrees, and multiple registered child Git worktrees
- **THEN** archive removes the child worktrees, moves the task root to `doc/<task-root-name>/`, and leaves all remaining task-root contents under the same relative paths

#### Scenario: Preserve the source branch

- **WHEN** archive removes a child worktree that uses a requirement branch
- **THEN** the child checkout is removed but the requirement branch remains in the source repository

#### Scenario: Archive without content-specific documents

- **WHEN** a requirement directory contains arbitrary files and directories but no specially named document
- **THEN** archive still succeeds without requiring, recognizing, or categorizing any document

### Requirement: Move the requirement directory without content processing

The archive SHALL move the requirement directory directly and SHALL preserve all remaining relative paths, regular-file bytes, directories, and filesystem entry types. It MUST NOT inspect file contents, infer document roles, rename entries, flatten directories, create summaries, or distill artifacts.

#### Scenario: Preserve arbitrary nested artifacts

- **WHEN** the requirement directory contains nested directories, files with arbitrary extensions, and binary files outside child worktrees
- **THEN** the destination contains the same relative paths, directory layout, and byte contents

#### Scenario: Do not classify artifacts

- **WHEN** two retained files have different names, extensions, or contents
- **THEN** both are moved unchanged and neither is treated as a requirement document, implementation document, or execution snapshot by the controller

### Requirement: Preflight the destination and report archive actions

Archive SHALL validate that the destination is under the workspace `doc/` root and that an existing non-empty destination is not overwritten before removing any child worktree. Human-readable and JSON results SHALL report the source task root, destination archive root, and every child worktree removed. The obsolete `--discard-missing-docs` behavior SHALL no longer gate the command.

#### Scenario: Reject an occupied destination before deletion

- **WHEN** `doc/<task-root-name>/` already exists with content
- **THEN** archive fails before removing any child worktree and does not overwrite the existing archive

#### Scenario: Report a successful directory move

- **WHEN** the destination is valid and all child worktrees are removed
- **THEN** archive reports the moved requirement directory and removed worktree paths, and the original task-root path no longer exists

### Requirement: Keep archive and cleanup lifecycle consistent

After archive succeeds, the moved requirement directory SHALL be the durable archive and a separate cleanup invocation SHALL NOT be required to remove the archived child worktrees. Cleanup MAY continue to handle active, not-yet-archived task roots, SHALL NOT delete archived directories, and SHALL retain existing branch-deletion behavior (no automatic branch deletion).

#### Scenario: Archive is terminal for the active task root

- **WHEN** archive completes for an active task root
- **THEN** the task root is absent from `worktrees/`, its complete remaining directory is present under `doc/`, and no second cleanup is needed for its child worktrees
