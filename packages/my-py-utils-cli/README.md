# my-py-utils-cli

Aggregate installer for all CLI commands in this workspace.

Install once:

```bash
uv tool install --editable packages/my-py-utils-cli
```

Then run commands directly:

```bash
hello-util --help
video2audio --help
mac-cache-cleaner --help
node-modules-cleaner --help
skill-sync --help
workspacectl --help
```

After changing command code, reinstall with `--force` if uv does not refresh the
tool environment:

```bash
uv tool install --editable packages/my-py-utils-cli --force
```
