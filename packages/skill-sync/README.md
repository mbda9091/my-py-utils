# skill-sync

Manage your skills in a separate personal Git repository. Skill files belong in
`<personal-skills-repo>/skills/`; `~/.agents/skills` is a client discovery directory
that can also contain skills from other sources.

## Configure once and list skills

M1 provides local repository configuration, discovery, status, and structural
validation. Prepare an existing Git working repository yourself, then configure
its directory once:

```bash
skill-sync init --repo ~/Code/my-skills
skill-sync list
skill-sync status
skill-sync validate ~/Code/my-skills/skills/foo
```

From this utilities workspace, use `uv run --package skill-sync skill-sync` in
place of `skill-sync` if the CLI is not installed.

`init --repo` saves the single repository path in `~/.skill-sync/config.json`.
Later `list` and `status` reuse it across processes and working directories;
they do not accept repository-selection flags. Repeating initialization with
the same normalized path is idempotent. Explicitly binding another directory
replaces the single binding, preserving backup copies of the old configuration
and any installation records. It does not move old skills or repair old links.

The repository must be a Git working directory root, separate from the client
skills directory and the local state directory. Both ordinary Git repositories
and Git worktrees are supported, including repositories without their first
commit, remote, or Git identity. Skills need a real `skills/` directory, which
initialization creates if absent. A missing or replaced repository path is
reported instead of silently selecting another repository.

The saved configuration is:

```json
{
  "schemaVersion": 2,
  "repositoryPath": "<absolute-personal-skills-repo>"
}
```

`SKILL_SYNC_HOME` can relocate the local configuration and records directory;
it does not determine where skill files are stored. Skill management supports
one bound repository, with no named repositories or per-command selection.

## Skill discovery

Create or maintain skills directly in your repository:

```text
<personal-skills-repo>/
  .git/
  skills/
    foo/
      SKILL.md
      scripts/
    bar/
      SKILL.md
```

`list` reads each skill's name and description from `SKILL.md`, verifies that the
frontmatter name matches the directory, and prints name, description, and real
path in name order. It does not require `skill-sync.json` or installation
records. Existing legacy metadata is preserved and ignored. Non-skill files
and directories without a descriptor are skipped; invalid descriptors and
symlinked skill roots or descriptors produce an error.

`status` shows the configured repository, configuration file, and discovered
skill count. `validate <directory>` checks the structure of one skill without
requiring a configured repository.

These commands work with uncommitted changes and on any branch. They do not
stage, commit, fetch, pull, push, configure Git identity, or modify the Git
index. You manage Git directly, including deciding which files to stage and
which commits to push.

## Legacy configuration and remaining work

With a schema v1 registry configuration, `status` displays migration guidance
and `list` requires an explicit `init --repo <local-repo>`. Initialization backs
up the old `config.json` and any `installations.json` next to the originals as
uniquely named `*.bak` files before writing v2 configuration. It does not clone,
move or delete the old checkout, alter installation records, or rewrite links.
Malformed and unsupported configurations are not overwritten.

The earlier `add`/`install` implementation and `publish`/`sync` commands remain
legacy operations during this milestone. They do not accept v2 local repository
configuration. Local skill absorption and symlink installation will be adapted
in M2; removal of the publication and synchronization workflow is M3. Do not
use the earlier remote-URL `init` syntax, which has been replaced by `--repo`.

See the [personal repository plan](../../docs/skill-sync-personal-repository-plan.md)
for milestone scope and acceptance records.
