#!/bin/sh
# Add independent executable checks to .git/hooks/pre-commit.d/.
set -eu
repo_root=$(git rev-parse --show-toplevel)
hook_dir="$repo_root/.git/hooks/pre-commit.d"
if [ ! -f "$hook_dir/10-security" ] || [ ! -x "$hook_dir/10-security" ]; then
    echo 'Security pre-commit check is missing or not executable; rerun setup.' >&2
    exit 2
fi
for hook in "$hook_dir"/*; do
    if [ "$hook" = "$hook_dir/10-security" ]; then
        continue
    fi
    if [ ! -e "$hook" ] && [ ! -L "$hook" ]; then
        continue
    fi
    if [ ! -f "$hook" ] || [ ! -x "$hook" ]; then
        echo "Pre-commit check is not an executable file: $hook" >&2
        exit 2
    fi
    "$hook" "$@"
done
exec "$hook_dir/10-security" "$@"
