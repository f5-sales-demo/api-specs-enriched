#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

# Preserve regenerated tracked files before selecting exact release-merge bytes.
set -euo pipefail

target_commit="${1:?release merge commit is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required for preserved generation evidence}"
[[ "$target_commit" =~ ^[0-9a-f]{40}$ ]] || exit 1
git cat-file -e "${target_commit}^{commit}"

evidence_dir="$RUNNER_TEMP/release-checkout-evidence"
mkdir -p "$evidence_dir"
git diff --binary HEAD >"$evidence_dir/generated-tracked.patch"
git status --porcelain=v1 >"$evidence_dir/status-before.txt"
if ! git diff --quiet HEAD; then
  git stash push -m "release-checkout-$target_commit"
  git rev-parse refs/stash >"$evidence_dir/preserved-stash.txt"
fi
# Untracked and ignored inputs are retained. Any checkout conflict fails closed.
git checkout --detach "$target_commit"
[[ "$(git rev-parse HEAD)" = "$target_commit" ]]
git diff --exit-code HEAD
