#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

# Calculate the next API release version from the complete unreleased commit
# range. A required repair after a breaking contract change must not erase the
# pending major-version signal.

set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must point at the step output file}"
: "${CHANGE_TYPE:?CHANGE_TYPE must be source, pipeline, or forced}"

INDEX_FILE="docs/specifications/api/index.json"
LATEST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
CURRENT_VERSION="${LATEST_TAG#v}"

if ! [[ "$CURRENT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "::warning::Invalid or absent version tag, starting at 0.0.0"
  CURRENT_VERSION="0.0.0"
  LATEST_TAG=""
fi

echo "current_version=$CURRENT_VERSION" >>"$GITHUB_OUTPUT"
echo "Current version from tags: $CURRENT_VERSION"

IFS='.' read -r MAJOR MINOR PATCH <<<"$CURRENT_VERSION"

if [ -n "$LATEST_TAG" ]; then
  UNRELEASED_MESSAGES="$(git log "${LATEST_TAG}..HEAD" --pretty=%B)"
else
  UNRELEASED_MESSAGES="$(git log -1 --pretty=%B 2>/dev/null || true)"
fi

if [[ "$UNRELEASED_MESSAGES" == *"[major]"* ]] || \
  [[ "$UNRELEASED_MESSAGES" == *"BREAKING CHANGE"* ]]; then
  NEW_VERSION="$((MAJOR + 1)).0.0"
  BUMP_TYPE="major"
elif [ "$CHANGE_TYPE" = "source" ]; then
  PREVIOUS_COUNT="$(git show "HEAD:${INDEX_FILE}" 2>/dev/null | jq '.specifications | length' 2>/dev/null || echo 0)"
  CURRENT_COUNT="$(jq '.specifications | length' "$INDEX_FILE" 2>/dev/null || echo 0)"
  if [ "$CURRENT_COUNT" -gt "$PREVIOUS_COUNT" ]; then
    NEW_VERSION="${MAJOR}.$((MINOR + 1)).0"
    BUMP_TYPE="minor"
  else
    NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
    BUMP_TYPE="patch"
  fi
elif [ "$CHANGE_TYPE" = "pipeline" ] || [ "$CHANGE_TYPE" = "forced" ]; then
  NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))"
  BUMP_TYPE="patch"
  echo "::notice::${CHANGE_TYPE^} release detected - patch bump"
else
  echo "::error::Unknown change type: $CHANGE_TYPE" >&2
  exit 1
fi

echo "new_version=$NEW_VERSION" >>"$GITHUB_OUTPUT"
echo "bump_type=$BUMP_TYPE" >>"$GITHUB_OUTPUT"
echo "::notice::Version: $CURRENT_VERSION -> $NEW_VERSION ($BUMP_TYPE)"
