## Purpose

Provide a safe, repeatable workspace lifecycle for joining existing branches across one or more registered repositories without creating independent requirement branches.

## ADDED Requirements

### Requirement: Create a collaboration from explicit project and branch mappings
The system SHALL provide a `create-collaboration` command that requires a valid collaboration slug and one or more repeatable `--project-branch <project-id>:<branch>` values. Each value SHALL bind exactly one registered project to exactly one branch, different projects MAY name different branches, and the command SHALL reject malformed mappings, invalid Git branch names, unknown projects, or duplicate project ids before creating a collaboration worktree.

#### Scenario: Create a collaboration for multiple repositories
- **WHEN** the user supplies a valid slug and valid mappings for multiple distinct registered projects
- **THEN** the system creates one collaboration containing one worktree for each supplied project and uses the branch named in that project's mapping

#### Scenario: Reject a duplicate project mapping
- **WHEN** the user supplies the same project id in more than one `--project-branch` value
- **THEN** the command fails without creating a collaboration task root or any worktree

#### Scenario: Reject an invalid mapping
- **WHEN** a mapping omits its project id or branch, names an unregistered project, or contains an invalid Git branch name
- **THEN** the command reports the invalid value and creates no collaboration worktree

### Requirement: Use an existing branch from origin safely
For every mapping, the system SHALL fetch the exact named branch from `origin` and SHALL fail when that remote branch does not exist. The resulting worktree SHALL use a same-named local branch that tracks `origin/<branch>` and SHALL NOT create a generated requirement branch, push, reset, rebase, or force-update a branch.

#### Scenario: Create a missing local tracking branch
- **WHEN** the remote branch exists and no same-named local branch exists
- **THEN** the system creates the same-named local branch tracking `origin/<branch>` and checks it out in the collaboration worktree

#### Scenario: Reuse a compatible local branch
- **WHEN** the same-named local branch already tracks `origin/<branch>`, is not checked out in another worktree, and has not diverged from the fetched remote branch
- **THEN** the system fast-forwards the local branch when it is behind, preserves it when it is equal to or ahead of the remote branch, and uses it for the new worktree

#### Scenario: Reject a conflicting local branch
- **WHEN** the same-named local branch is checked out in another worktree, tracks a different upstream, or has diverged from the fetched remote branch
- **THEN** the command fails with the conflict reason and does not reset, rebase, force-update, or delete the existing local branch

#### Scenario: Reject a missing remote branch
- **WHEN** any requested branch cannot be fetched from `origin`
- **THEN** the command identifies the affected project and branch and creates no collaboration task root or worktree

### Requirement: Create collaboration worktrees atomically
The system SHALL treat the requested mappings as one creation operation. If validation, worktree creation, generated metadata, or a post-create hook fails for any project, it SHALL remove every worktree, task-root artifact, and local branch newly created by that invocation while preserving all pre-existing local branches and all remote branches.

#### Scenario: A later project fails
- **WHEN** an earlier project has been prepared and a later project fails during the same command
- **THEN** no collaboration task root or worktree from the failed invocation remains and only local branches created by that invocation are rolled back

#### Scenario: A post-create hook fails
- **WHEN** a project post-create hook fails after one or more collaboration worktrees were created
- **THEN** the command removes the newly created collaboration worktrees and metadata and reports the hook failure

### Requirement: Persist typed collaboration task state
The system SHALL create `worktrees/<date>-<slug>/` with child worktrees named `<project-id>-<slug>/` and SHALL persist a `.workspace/task.yaml` that identifies the task as `kind: collaboration`, records the collaboration slug, and records every project id, relative worktree path, explicit branch, and prompt version needed for recovery. A collaboration slug SHALL share the existing task-slug collision namespace with active and archived requirement tasks.

#### Scenario: Persist a multi-project collaboration
- **WHEN** collaboration creation succeeds for multiple mappings
- **THEN** the task manifest records `kind: collaboration` and contains one accurate worktree entry for each requested project and branch

#### Scenario: Reject an existing slug
- **WHEN** the collaboration slug collides with an active task or an archived requirement
- **THEN** the command fails without reusing or overwriting the existing path or manifest

### Requirement: Prepare each collaboration worktree environment
For each newly created collaboration worktree, the system SHALL apply the existing worktree exclusions, link the source checkout's `AGENTS.local.md` when present, execute the selected post-create project hook, and generate `.workspace/AGENTS.md` describing the collaboration task, its explicit project branch, and its workspace boundaries without presenting it as a requirement or referring to a PRD.

#### Scenario: Prepare project-local guidance and hooks
- **WHEN** a collaboration worktree is created for a project with local agent guidance and an applicable project hook
- **THEN** the worktree contains the expected local guidance link, generated collaboration guidance, exclusions, and hook-created environment

### Requirement: Expose collaboration tasks through workspace maintenance commands
The `status`, `update`, and `doctor` commands SHALL recognize typed collaboration manifests. Human-readable and JSON status SHALL identify collaboration tasks and their project-to-branch mappings; update SHALL refresh only reconstructible worktree metadata and guidance; doctor SHALL validate registered projects, worktree registration, paths, and recorded branches without treating the collaboration as a requirement.

#### Scenario: Report collaboration status
- **WHEN** the workspace contains an active collaboration task
- **THEN** human-readable and JSON status identify it as a collaboration and report its slug and worktree mappings without an empty requirement title

#### Scenario: Refresh collaboration metadata
- **WHEN** update processes a valid collaboration task
- **THEN** it refreshes exclusions, local guidance links, generated collaboration guidance, and manifest formatting without changing its project set, branches, or worktree structure

#### Scenario: Diagnose a branch mismatch
- **WHEN** a collaboration manifest records a branch that does not match the branch registered at its worktree path
- **THEN** doctor reports a collaboration worktree branch mismatch for that project

### Requirement: Clean up collaboration tasks without deleting branches
The existing `cleanup` command SHALL accept an active collaboration task root, apply existing dirty-worktree and pre-clean-hook safeguards, remove all recorded collaboration worktrees and the task root, and preserve every local and remote branch.

#### Scenario: Clean collaboration worktrees
- **WHEN** the user runs cleanup on a collaboration whose worktrees are clean
- **THEN** the system runs the applicable pre-clean hooks, removes all recorded worktrees and the task root, and leaves their branches intact

#### Scenario: Protect dirty collaboration work
- **WHEN** a collaboration worktree is dirty and cleanup is invoked without `--force`
- **THEN** cleanup fails without removing that dirty worktree or its branch

### Requirement: Keep requirement-only lifecycle operations unavailable
The `extend-requirement` and `archive-requirement` commands SHALL reject a task manifest whose kind is `collaboration` before changing its worktrees, manifest, or archive state. The error SHALL explain that collaboration mappings are immutable and that collaboration tasks are finished with `cleanup`.

#### Scenario: Reject collaboration extension
- **WHEN** the user passes a collaboration task root to `extend-requirement`
- **THEN** the command reports that collaboration mappings cannot be extended and makes no changes

#### Scenario: Reject collaboration archive
- **WHEN** the user passes a collaboration task root to `archive-requirement`
- **THEN** the command reports that collaboration tasks are not archived and makes no changes

### Requirement: Preserve existing requirement task compatibility
Existing requirement manifests that do not contain a `kind` field SHALL continue to be interpreted as requirement tasks, and all existing requirement commands, paths, branch naming, status data, update behavior, doctor checks, cleanup, and archive behavior SHALL remain compatible.

#### Scenario: Operate on a legacy requirement manifest
- **WHEN** a workspace contains a requirement task manifest created before typed collaboration manifests were introduced
- **THEN** the system treats it as `kind: requirement` without requiring a manifest migration and preserves its existing lifecycle behavior
