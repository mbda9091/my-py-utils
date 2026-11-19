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

A same-name skill already in the repository is a conflict; neither side is
overwritten or merged. Repeating `add` on a managed real directory or a link to
that skill succeeds without another move. Links to other locations, symlinked
SKILL.md files, nested Git metadata and unsupported special files are rejected.
All local files are preserved, including caches and existing legacy metadata;
`add` never generates or refreshes `skill-sync.json`.

## Install a personal skill

```bash
skill-sync install foo --target codex
skill-sync install --all --target codex
```

Installation creates `~/.agents/skills/<name>` as an absolute symlink to the
personal skill. Existing links to the same skill are idempotent. Edit through
either path to update the same real files; tracked changes appear in your
normal Git diff. There is no installation copy, commit pin or publication step.

Unknown directories, files, third-party links and unverified broken links are
preserved even with `--force`. After explicitly rebinding a new personal
repository, `--force` can relink a previously registered personal installation
only when its actual link still matches the recorded old source. Replaced or
unresolved records do not authorize overwriting content. The old source files
are never deleted by relinking.

Batch installation continues after individual failures, prints installed/failed
counts and returns a nonzero status if any skill failed. It only enumerates the
configured personal repository.

## Discovery and installation records

`list` reads `skills/*/SKILL.md`, verifies directory/name consistency, and prints
name, description and real path in name order. It needs no publishing metadata
or installation records. Files and draft directories without a descriptor are
skipped; invalid descriptors and symlinked skill roots/descriptors are errors.
`validate <directory>` checks one skill without requiring repository configuration.

`status` shows the repository, skill count and registered installation paths.
Each entry is classified as `ok`, `missing`, `broken`, `replaced`,
`previous-repository`, or `unresolved`. It checks recorded paths, without
inventorying other skills in the client directory.

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
preserved with an unknown source and cannot authorize `--force`. M1's previously
unversioned records can also be read without modification; their first update
preserves a backup before writing schema v2.

Prepare and compare a new repository yourself before changing the binding.
Use `status` to inspect remaining old-source links. Standard Codex paths can be
relinked using the verified `install --force` flow; arbitrary original paths
can be updated manually. Keep old content until all intended links are checked.
Existing `skill-sync.json` files are retained and ignored by local management.

## Git workflow

The CLI supports `init`, `add`, `install`, `list`, `status`, and `validate`.
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

The tool's only Git subprocess is a fixed `git --no-optional-locks rev-parse
--show-toplevel` check of the local working directory. It has no generic Git
runner, publisher, network synchronization or identity configuration. `validate`
checks skill structure; Git staging, commits, remotes and any commit checks are
under your control. Read-only v1 migration remains available without restoring
the old workflows.

See the [personal repository plan](../../docs/skill-sync-personal-repository-plan.md)
for implementation scope and acceptance records.
