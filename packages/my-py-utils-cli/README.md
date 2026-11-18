# my-py-utils-cli

Aggregate installer for all CLI commands in this workspace. Each command keeps
its own package implementation; the aggregate package registers their console
entry points. For repo-security setup and supported environments, see
[its README](../repo-security/README.md).

Install once:

```bash
uv tool install --editable packages/my-py-utils-cli
```

Then run commands directly:

```bash
codex-hook-ctl --help
codex-session-export --help
hello-util --help
video2audio --help
mac-cache-cleaner --help
system-alarm --help
node-modules-cleaner --help
skill-sync --help
workspacectl --help
private-pack --help
repo-security --help
```

After changing command code, reinstall with `--force` if uv does not refresh the
tool environment:

```bash
uv tool install --editable packages/my-py-utils-cli --force
```
