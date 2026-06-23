#!/bin/bash
set -e

# Get version from Python file
VERSION=$(grep '__version__' src/rubiksql/version.py | sed -E "s/.*\"([^\"]+)\".*/\1/")

if [ -z "$VERSION" ]; then
    echo "Version not found!"
    exit 1
fi

git checkout --orphan latest_branch
git rm -rf . --cached
git add --all
git commit -m "$VERSION" || true

git branch -D master 2>/dev/null
git branch -m master

git push -f origin master

echo "Commit History cleared. Version: $VERSION."
