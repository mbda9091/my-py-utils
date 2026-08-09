# skill-sync

Personal Git-backed sync CLI for agent skills.

`skill-sync` stores publishable skills in a Git registry checkout and installs
them into the Codex user skill directory.

## Commands

```bash
skill-sync init git@github.com:you/agent-skills.git --branch main \
  --git-name mbda9091 \
  --git-email bozieg@qq.com
skill-sync sync
skill-sync status
skill-sync validate ~/.agents/skills/foo
skill-sync publish ~/.agents/skills/foo
skill-sync publish ~/.agents/skills/foo --update --no-push
skill-sync list
skill-sync install foo --target codex --force
```

The default state directory is `~/.skill-sync`. Set `SKILL_SYNC_HOME` to use a
different directory for configuration, the registry checkout, and installation
records.

## Git Identity

Use `--git-name` and `--git-email` during `init` to make registry commits stable
across machines:

```bash
skill-sync init git@github.com:mbda9091/my-skills.git --branch main \
  --git-name mbda9091 \
  --git-email bozieg@qq.com
```

The identity is saved in `~/.skill-sync/config.json`. `skill-sync sync` and
`skill-sync publish` apply it to the registry checkout's local Git config before
committing, so publishes do not depend on the machine's global Git settings.
`skill-sync publish` refuses to create commits until a registry Git identity is
configured.

For an existing config, rerun `init` with the same repository and branch plus
`--git-name/--git-email`; only the saved identity is updated.
