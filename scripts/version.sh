#!/usr/bin/env bash
# The release number: MAJOR.MINOR from the VERSION file, then the number of commits since VERSION
# last changed (2.0.0 is the first release after VERSION became 2.0, then 2.0.1, 2.0.2…).
# Bump VERSION (2.0 → 2.1) for a release with new features; fixes just count up.
set -euo pipefail
cd "$(dirname "$0")/.."
base="$(tr -d '[:space:]' < VERSION)"
since="$(git log -1 --format=%H -- VERSION 2>/dev/null || true)"
if [[ -n "$since" ]]; then
  patch="$(git rev-list --count "$since"..HEAD)"
else
  patch=0
fi
echo "$base.$patch"
