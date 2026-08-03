#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

set -euo pipefail

required=(
  GH_TOKEN
  TARGET_OWNER
  TARGET_REPO
  EVENT_TYPE
  VERSION
  SOURCE_REPOSITORY
  SOURCE_UPDATED_AT
  SOURCE_RUN_ID
)

for name in "${required[@]}"; do
  if [ -z "${!name:-}" ]; then
    echo "[ERROR] ${name} is required" >&2
    exit 2
  fi
done

if [[ ! "$TARGET_OWNER" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]; then
  echo "[ERROR] TARGET_OWNER is malformed" >&2
  exit 2
fi
if [[ ! "$TARGET_REPO" =~ ^[A-Za-z0-9._-]{1,100}$ ]]; then
  echo "[ERROR] TARGET_REPO is malformed" >&2
  exit 2
fi
if [[ ! "$EVENT_TYPE" =~ ^[A-Za-z0-9_.-]{1,100}$ ]]; then
  echo "[ERROR] EVENT_TYPE is malformed" >&2
  exit 2
fi
if [[ ! "$VERSION" =~ ^[0-9]+(\.[0-9]+){2}([-+.][0-9A-Za-z.-]+)?$ ]]; then
  echo "[ERROR] VERSION is malformed" >&2
  exit 2
fi
if [[ ! "$SOURCE_REPOSITORY" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "[ERROR] SOURCE_REPOSITORY is malformed" >&2
  exit 2
fi
if [[ ! "$SOURCE_UPDATED_AT" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
  echo "[ERROR] SOURCE_UPDATED_AT is malformed" >&2
  exit 2
fi
if [[ ! "$SOURCE_RUN_ID" =~ ^[1-9][0-9]*$ ]]; then
  echo "[ERROR] SOURCE_RUN_ID is malformed" >&2
  exit 2
fi

dispatch_gh=${DISPATCH_GH:-gh}
if ! command -v "$dispatch_gh" >/dev/null 2>&1; then
  echo "[ERROR] DISPATCH_GH is not executable" >&2
  exit 2
fi

jq -cn \
  --arg event_type "$EVENT_TYPE" \
  --arg version "$VERSION" \
  --arg source_repository "$SOURCE_REPOSITORY" \
  --arg timestamp "$SOURCE_UPDATED_AT" \
  --arg run_id "$SOURCE_RUN_ID" \
  '{
    event_type: $event_type,
    client_payload: {
      version: $version,
      release_tag: ("v" + $version),
      release_url: (
        "https://github.com/" + $source_repository + "/releases/tag/v" + $version
      ),
      timestamp: $timestamp,
      trigger_source: $source_repository,
      run_id: $run_id
    }
  }' |
  "$dispatch_gh" api --method POST \
    "repos/${TARGET_OWNER}/${TARGET_REPO}/dispatches" \
    --input -
