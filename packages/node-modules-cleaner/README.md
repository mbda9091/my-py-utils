# node-modules-cleaner

Delete every `node_modules` directory under a target directory.

```bash
uv run --package node-modules-cleaner node-modules-cleaner ~/Code --dry-run
uv run --package node-modules-cleaner node-modules-cleaner ~/Code
```

The scanner prunes each matched `node_modules` directory, so nested entries inside
a deleted `node_modules` tree are not reported separately.

On macOS and Linux, deletion uses the system `rm -rf` command. Other platforms use
Python's built-in recursive deletion.
