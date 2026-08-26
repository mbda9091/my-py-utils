# codex-hook-ctl

Record custom Codex command hooks as individual records and inject them by
executable name.

```bash
codex-hook-ctl record --global
codex-hook-ctl record --directory /path/to/project
codex-hook-ctl inject --global dynamic-agents
codex-hook-ctl inject --directory /path/to/project dynamic-agents
```

Records are written under `codex-hooks/` in the current directory. Use `-f` to
explicitly overwrite an existing record or target hook.

Each record contains only one executable hook:

```json
{
  "schemaVersion": 1,
  "name": "dynamic-agents",
  "executable": "dynamic-agents.py",
  "source": "global",
  "registrations": [
    {
      "event": "PreToolUse",
      "matcher": "^Bash$",
      "command": ["python3", "{hook_file}"],
      "handler": {"type": "command", "timeout": 10}
    }
  ]
}
```

`record` reads `~/.codex/hooks.json` by default or `<dir>/.codex/hooks.json`
when `--directory` is provided. It records command handlers whose command
references a file under that layer's `.codex/hooks/` directory. Injection puts
the executable at `<target>/codex-hooks/<file>` and updates the target's
`.codex/hooks.json`.
