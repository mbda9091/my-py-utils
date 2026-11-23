# skill-sync

Manage your skills in a separate personal Git repository. Real skill files live
in `<personal-skills-repo>/skills/`; client directories such as
`~/.agents/skills` contain symlinks and can also contain skills from other sources.
You manage Git staging, commits, branches and remotes yourself.

## Configure once

Prepare an existing Git working repository, then save its directory once:

```bash
skill-sync init --repo ~/Code/my-skills
skill-sync list
skill-sync status
```

From this utilities workspace, use `uv run --package skill-sync skill-sync` in
place of `skill-sync` if the CLI is not installed.

The one repository path is saved in `~/.skill-sync/config.json`. Later commands
reuse it across processes and working directories, with no repository-selection
flags. Repeating the same binding is idempotent. Explicitly binding another
repository replaces the single binding and backs up the previous configuration
and installation records, without moving old skills or rewriting their links.

The path must be a Git working directory root, separate from the client skills
directory and local state directory. Ordinary repositories and Git worktrees
are supported. A first commit, remote, identity, fixed branch or clean working
directory is not required. Initialization creates an empty real `skills/`
directory if absent. Missing or replaced repository paths are reported rather
than silently selecting another repository.

```json
{
  "schemaVersion": 2,
  "repositoryPath": "<absolute-personal-skills-repo>"
}
```

`SKILL_SYNC_HOME` relocates local configuration and records, not the skill
content. Only one bound repository is supported.

## Add an existing skill

```bash
skill-sync add ~/.agents/skills/foo
```

`add` validates the selected skill, moves its complete content into the personal
repository, and replaces the original path with an absolute symlink:

```text
<personal-skills-repo>/skills/foo/   real files
~/.agents/skills/foo                -> personal repository skill
```

The selected path can be outside the Codex directory. The tool only absorbs the
skill you specify; it does not scan or absorb other skills in the client directory.
Keep the skill unchanged until migration finishes. The operation stages a copy,
checks content and metadata for changes, then preserves the original as a backup
until the link and installation record are ready. Detected edits or failures
restore the original; a failed restoration retains its backup and reports where
it can be recovered. Preparing a copy also supports moves across filesystems.

A same-name skill already in the repository is a conflict by default. To replace
it explicitly, use:

```bash
skill-sync add ~/.agents/skills/foo --force
```

`add --force` replaces the entire stored skill with the selected directory,
including removing files that exist only in the old version. Existing links to
that repository path read the replacement automatically. The source path becomes
a link to the replacement, and its installation record is added without dropping
other use paths. The previous repository entry is held in a temporary backup
until copying, linking and recording finish. Failure before completion restores
the source, the old repository entry and previous records; failed recovery reports
the retained backup. After the replacement, source link and records have committed,
failure to clean the old temporary backup produces a warning with its full path.
The add still succeeds, and any remaining files stay there for manual cleanup.
This operation does not stage or commit Git changes.

Repeating `add` on a managed real directory or a link to that skill succeeds
without another move, with or without `--force`. Links to other locations,
symlinked repository skills or SKILL.md files, nested Git metadata and unsupported
special files remain rejected.
All local files are preserved, including caches and existing legacy metadata;
`add` never generates or refreshes `skill-sync.json`.

Relative symlinks must stay within the skill directory, including through link
chains. Outward relative links (even dangling ones) are rejected before any move,
because relocating the skill would change their meaning. Move the referenced
content into the skill or make the reference absolute before retrying. Internal
relative links and absolute links retain their stored targets.

## Install a personal skill

```bash
skill-sync install foo
skill-sync install --all
```

Installation creates `~/.agents/skills/<name>` as an absolute symlink to the
personal skill. Existing links to the same skill are idempotent. Edit through
either path to update the same real files; tracked changes appear in your
normal Git diff. There is no installation copy, commit pin or publication step.

Without `--force`, same-name directories, files and unrelated links are skipped
with a warning. Explicit replacement first preserves the entire old entry:

```bash
skill-sync install foo --force
skill-sync install --all --force
```

The old directory, file or symlink itself is renamed into a unique private
`~/.agents/.skill-sync-backups/<name>-<time>-<id>/original`. `backup.json`
records the original path and source. Symlink targets are never moved or deleted.
Successful backups remain until you remove them manually; their full paths are
printed. Reinstalling the correct link does not create another backup. Backups
sit outside the skill discovery directory. The destination and backup directory
must share a filesystem; an unsupported cross-device rename fails safely.
Special files and mount points are preserved with warnings even with `--force`.
The installation path is always the current user's `~/.agents/skills`, regardless
of the working directory. There is no `--target` or custom CLI install directory.

Replacement checks the destination identity again after capturing it, and places
the new link without overwriting an unexpected occupant. Failures before the
link and record commit restore the original; failed recovery preserves its backup
and reports the location. Successful replacement never deletes its persistent
backup. To restore it manually, remove only the installed link after verifying
its target, then move `original` back to the path in `backup.json`. Do not overwrite
a new occupant. Relative links regain their original meaning after restoration.

Source verification still rejects intermediate aliases and invalid parent
traversal as proof of ownership in legacy installation records. Those records
alone never authorize replacement; `--force` explicitly authorizes backing up
the selected current entry. A link already resolving to the current personal
skill may be normalized without force.

`init`, `add`, `install`, `uninstall`, `remove`, and `update` share a process lock in the state directory. Concurrent
commands wait until the current read/modify/write operation, including rollback,
finishes. Batch installation holds the lock for the batch. This prevents lost
installation records and races with repository rebinding. The lock file remains
on disk; the operating system releases the lock when a process exits.

An existing same-name entry that cannot be relinked is skipped with a `[WARN]`
message on stderr, including for a single-skill installation and with `--force`.
Its content and installation records are preserved.

Batch installation continues after skips and individual failures. It reports
`Installed: N; skipped: N; failed: N` and returns a nonzero status only for real
failures, such as invalid descriptors, missing skills or link/write errors.
Each skill is validated inside its own installation attempt: a malformed
`SKILL.md` counts as one failure and does not block other skills. Conflicts alone
return success with warnings. It only enumerates the configured personal repository.

## Uninstall a personal skill

```bash
skill-sync uninstall foo
```

This removes only the verified personal link at the current user's
`~/.agents/skills/foo` and its matching installation record. The repository skill,
other use paths created by `add`, and persistent collision backups stay intact.
Uninstalling twice succeeds. Known broken links can be removed even after the
skill or entire configured repository has disappeared. A verified recorded link
to a previously bound repository can also be uninstalled without touching its
source. Unknown links, aliases, real directories and third-party replacements
are preserved with warnings; old records alone never authorize deleting them.

Links are captured privately before removal. A record-write failure restores the
link; failed recovery preserves its private container and reports the path. After
records commit, cleanup failure reports remaining files as a warning. To restore
a force-install backup, verify and uninstall the installed link, then move the
backup's `original` to the original path recorded in `backup.json`.

## Remove a repository skill

```bash
skill-sync remove foo
```

This deletes the entire `<personal-skills-repo>/skills/foo` directory, including
uncommitted and untracked files, and removes verified links at recorded use paths
and the standard user agents path. It includes original links created by `add`.
It does not restore the pre-add directory. Use `uninstall` to keep the source.
Tracked deletions appear in your normal Git diff; the tool does not stage, commit
or push them. Untracked deleted content cannot be recovered through Git.

A broken or missing descriptor does not prevent removal. Missing source content
and repeated removal are supported. Third-party replacements, unknown provenance
and links to another personal repository are preserved; warnings name skipped
entries. Unrecorded manual links elsewhere are not searched and may become broken.
Persistent installation-collision backups remain intact. Symlinked skill roots
and mounted subdirectories are rejected; internal symlinks are not followed.

The source and verified links are captured before writing records. A pre-commit
failure restores all captured entries; occupied recovery paths retain their
recovery containers. Once records commit, a cleanup failure is a success with a
warning naming the remaining container. Its content may already be partially
removed, so it is not a promised full backup.

## Discovery and installation records

`list` reads `skills/*/SKILL.md`, verifies directory/name consistency, and prints
name, description and real path in name order. It needs no publishing metadata
or installation records. Files and draft directories without a descriptor are
skipped. Invalid descriptors and symlinked skill roots/descriptors produce a
`[WARN] Invalid skill: <name>` message on stderr; `list` still prints healthy
skills. If every candidate is invalid, it prints `No valid skills found.`.
`list` and `status` return success when only individual catalog entries are
invalid; configuration and repository errors still fail the command.
`validate <directory>` checks one skill without requiring repository configuration
and returns failure for invalid content, as does installing that skill explicitly.

`status` shows the repository, skill count and registered installation paths.
Each entry is classified as `ok`, `missing`, `broken`, `replaced`,
`previous-repository`, or `unresolved`. It checks recorded paths, without
inventorying other skills in the client directory. Invalid descriptors do not
block this output: `skills` counts valid skills, `invalid skills` appears when
needed, and each invalid skill is named in a warning. Installation statuses
describe the recorded links independently of descriptor validity.

Local `installations.json` uses schema v2, keyed by the full use path, with
`skill`, `target`, `path`, and `sourcePath` fields. Multiple paths for one skill
remain separate. Arbitrary paths absorbed by `add` use target `local-path`;
Codex installations use `codex`. There is no commit field or version lock.

## Migrating old configuration

With a schema v1 registry configuration, `status` displays migration guidance;
use `init --repo <local-repo>` explicitly. Initialization preserves uniquely
named `config.json.*.bak` and `installations.json.*.bak` beside the originals,
then converts the configuration and installation records together. If writing
fails, it restores their previous contents; failed recovery reports the saved
backups.

Old installation source paths are recovered only when an actual link matches a
known old repository from configuration or its backups. Otherwise the record is
preserved with an unknown source and cannot itself authorize overwriting content. M1's previously
unversioned records can also be read without modification; their first update
preserves a backup before writing schema v2.

Prepare and compare a new repository yourself before changing the binding.
Use `status` to inspect remaining old-source links. Standard user agents paths can be
relinked using the backup-and-replace `install --force` flow; arbitrary original paths
can be updated manually. Keep old content until all intended links are checked.
Existing `skill-sync.json` files are retained and ignored by local management.

## Git workflow

The CLI supports `init`, `add`, `install`, `uninstall`, `remove`, `update`, `list`,
`status`, and `validate`.
The earlier `publish` and `sync` commands have been removed. Old invocations
exit with a migration message before loading configuration or running a process.
The old remote-URL initialization and branch/identity flags are also rejected.

Use Git directly when you want to save or share your edits:

```bash
git -C ~/Code/my-skills status
git -C ~/Code/my-skills diff
git -C ~/Code/my-skills add skills/foo
git -C ~/Code/my-skills commit -m "Update foo"
git -C ~/Code/my-skills push
```

To explicitly pull the current branch of your configured personal repository:

```bash
skill-sync update
```

This runs `git pull --ff-only --no-rebase --no-autostash --no-recurse-submodules`
with the branch's existing upstream. It requires a committed HEAD, a checked-out
branch, an upstream, no unfinished Git operation, and a clean working tree
(including non-ignored untracked files). Ignored caches do not block it.
After `add` or `remove`, handle your local changes yourself before updating.
Divergence stops the command without merging, rebasing, stashing or pushing.
Already-current or locally-ahead branches succeed without discarding commits.
Ordinary Git working trees and linked worktrees are supported.

Existing skill links read fast-forwarded content immediately. New upstream skills
are not automatically installed. Deleted or renamed skills can leave broken
links; update warns about known affected paths, and uninstall/remove can clean
them. Invalid skill descriptors or catalog structure produce warnings after a
successful pull, without resetting Git. Configuration and installation records
are not rewritten. Git failures report the actual current HEAD when available;
a failed pull may still have fetched objects or remote refs, and the tool does
not reset them. Authentication uses your normal Git environment. Repository-owned
Git hooks/filters retain their normal behavior; they are not sandboxed by this CLI.

Only `update` can run fixed Git preflight queries and this fixed pull. Other
commands only run the read-only `git --no-optional-locks rev-parse --show-toplevel`
root check. There is no generic Git runner, publisher or identity configuration.
Git staging, commits, branches, remotes, pushes and commit checks stay under your
control. Read-only v1 migration remains available without restoring old workflows.
The legacy installation record `target` labels remain for schema compatibility;
they do not select installation paths or authorize deletion.

See the [personal repository plan](../../docs/skill-sync-personal-repository-plan.md)
and the [lifecycle plan](../../docs/skill-sync-lifecycle-plan.md) for implementation
scope and acceptance records.
