## Context

`workspacectl` currently has one task shape. `create-requirement` derives a single branch name and applies it independently in every selected repository; task discovery looks for `.workspace/task.yaml`, while rendering and lifecycle code assumes that manifest contains `requirement` data. The Git, hook, rollback, cleanup, and path-safety primitives are already reusable, but task identity and branch preparation are coupled to requirement semantics.

See `proposal.md` for motivation and `specs/collaboration-worktrees/spec.md` for the behavioral contract.

## Goals / Non-Goals

**Goals:**

- Add an explicit per-project branch policy without duplicating worktree, hook, or cleanup machinery.
- Preserve backward compatibility for manifests without a task kind.
- Make branch-state checks conservative and free of destructive reconciliation.
- Keep multi-project failures from leaving partial worktrees, manifests, task directories, or newly created local branches.
- Keep type-specific presentation and generated guidance accurate.

**Non-Goals:**

- Cloning arbitrary repository URLs or configuring new workspace projects.
- Selecting a remote other than `origin`.
- Changing a collaboration's project-to-branch mappings after creation.
- Archiving collaboration tasks or producing collaboration deliverables.
- Automatically resolving diverged branches, switching a branch already used by another worktree, pushing commits, or deleting branches during cleanup.

## Decisions

### 1. Introduce a typed task model with legacy fallback

Add a task-kind discriminator with `requirement` and `collaboration` values. New manifests write `kind` explicitly; manifest readers treat an absent `kind` as `requirement`. Central helpers will validate the kind and return the type-specific identity instead of scattering `manifest.get("requirement", {})` checks.

A collaboration manifest uses this shape:

```yaml
kind: collaboration
collaboration:
  slug: search-debug
prompt_version: workspace-agent/v1
scope:
  - android
  - ios
worktrees:
  - project_id: android
    path: worktrees/2026-08-31-search-debug/android-search-debug
    branch: merchant/alice/search
  - project_id: ios
    path: worktrees/2026-08-31-search-debug/ios-search-debug
    branch: feature/search-ios
```

`scope` remains a derived ordered list of project ids so generic status, cleanup, and validation code can continue to work. The branch mapping stays authoritative in `worktrees`; a second mapping structure would create synchronization risk.

Alternative considered: infer collaboration tasks from a missing `requirement` block. This was rejected because malformed requirement manifests would be silently reclassified and future task types would remain ambiguous.

### 2. Use a repeatable compound CLI argument

The command surface is:

```bash
workspacectl create-collaboration \
  --slug search-debug \
  --project-branch android:merchant/alice/search \
  --project-branch ios:feature/search-ios
```

It also receives the existing `--task-date`, hook-selection arguments, and `--json` output option. It does not receive requirement-only `--title`, `--source-prd-url`, `--git-username`, `--upstream`, or `--reuse-existing` options.

Parse each mapping at the first colon. Existing project-id validation excludes colons and Git branch validation excludes colons, making the pairing unambiguous. Validate the complete list and reject duplicate project ids before binding repositories or creating paths.

Alternative considered: parallel repeatable `--project` and `--branch` options. This was rejected because positional pairing is fragile and produces poor errors when counts or ordering differ.

### 3. Separate branch preflight from worktree creation

Before creating the task root:

1. Validate the slug, collision namespace, mappings, and repository bindings.
2. Validate each branch with Git's branch-name rules.
3. Fetch the exact branch into its `origin/<branch>` remote-tracking ref.
4. Inspect any same-named local branch, its configured upstream, its ancestry relative to the fetched ref, and all registered worktrees.
5. Reject occupied, differently tracked, or diverged local branches before creating any worktree.

For a missing local branch, add the worktree while creating a same-named branch that tracks `origin/<branch>`. For an existing compatible branch, add its worktree, establish the upstream when it is absent, and use a normal fast-forward merge when it is behind. Equal or locally ahead branches require no content update. No path uses reset, rebase, branch-force, or push.

Fetching updates remote-tracking state even when a later preflight fails. That state is an observation of `origin`, not a collaboration artifact. A safe fast-forward of a pre-existing local branch may likewise remain if a later hook fails; rollback deliberately never rewinds a pre-existing branch.

Alternative considered: always create a differently named local branch from `FETCH_HEAD`. This was rejected because it defeats the requested shared-branch workflow and complicates normal push/pull behavior.

### 4. Reuse creation orchestration with explicit branch ownership

Refactor worktree preparation so callers provide a branch plan rather than assuming that a missing branch must be derived from a requirement source ref. Track, per repository, whether the local branch existed before the invocation. The collaboration creation transaction records created worktree paths and only those local branches that it created.

On failure, unwind created worktrees in reverse order, delete only newly created local branches, and remove the task root and generated metadata. Existing local branches and all remote refs are preserved. Existing post-create hooks remain the integration boundary; their external side effects are not generally reversible, but worktree-local hook output is removed with the worktree.

Alternative considered: implement a second independent worktree engine for collaborations. This was rejected because it would duplicate path validation, exclusions, local guidance linking, hook dispatch, and rollback behavior.

### 5. Dispatch manifest rendering and lifecycle behavior by task kind

Split generated agent rendering into shared workspace boundaries plus requirement- and collaboration-specific identity sections. Collaboration guidance names the collaboration slug and explicit branch, omits PRD language, and retains the rule that work stays inside the child worktree.

Status reports each active task's kind and type-specific identity. Human output labels collaborations and prints every project-to-branch mapping; JSON retains existing requirement fields for requirement tasks and adds collaboration data for collaboration tasks. Update calls the renderer selected by task kind and does not alter mappings or paths. Doctor validates the kind-specific identity plus the existing worktree registration, containment, and branch checks.

Cleanup remains the common destructive lifecycle endpoint because it already operates from manifest worktree entries and preserves branches. `extend-requirement` and `archive-requirement` add an immediate requirement-kind guard after loading the manifest, before any Git or filesystem mutation.

Alternative considered: allow requirement-only commands to proceed when enough generic fields happen to exist. This was rejected because it would make collaboration scope accidentally mutable and could archive a task type that has no archive contract.

### 6. Preserve one slug and directory namespace

Collaboration roots reuse `worktrees/<date>-<slug>/` and `<project-id>-<slug>/`. Slug validation and collision checks remain global across active tasks and archived requirements. No `collab-` prefix is injected, so the user-provided slug, manifest identity, displayed identity, and filesystem path stay identical.

Alternative considered: add a separate `collaborations/` root or automatic directory prefix. This was rejected because it would duplicate initialization, cleanup safety, discovery, and ignore rules without adding lifecycle value; the manifest kind is the authoritative discriminator.

## Risks / Trade-offs

- [A remote branch was force-pushed] → Fetch updates the remote-tracking ref, ancestry validation detects divergence from a pre-existing local branch, and creation stops without reconciling it.
- [A local branch advances between preflight and worktree creation] → Git's final worktree and fast-forward checks remain authoritative; any failure enters the same rollback path.
- [A hook has external side effects before failing] → Preserve the existing hook contract, roll back worktree-local state, and report the hook failure; do not claim that arbitrary external effects are transactional.
- [Typed status output affects consumers] → Preserve current requirement fields and behavior, add task kind/type-specific data, and cover both legacy and new manifests with JSON tests.
- [Fast-forwarding a pre-existing branch is not rolled back after a later failure] → Limit updates to normal fast-forwards, never rewind the branch, and document that rollback covers collaboration artifacts and newly created branches rather than fetched or safely advanced refs.
- [The compound argument is unfamiliar] → Use a descriptive `--project-branch` name, repeat it once per repository, and return mapping-specific validation errors.

## Migration Plan

1. Add kind-aware readers with absent-kind fallback before writing typed manifests.
2. Keep existing requirement fixtures and lifecycle tests unchanged to verify compatibility.
3. Write `kind: requirement` for newly created requirement manifests and `kind: collaboration` for collaboration manifests; do not rewrite existing manifests solely for migration.
4. Add the command, lifecycle dispatch, tests, and documentation in the same release.
5. Rollback consists of removing the new command and collaboration dispatch while retaining the absent-kind requirement fallback; no existing runtime data needs conversion.
