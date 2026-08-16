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
  SOURCE_TARGET_COMMIT
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
if [[ ! "$SOURCE_TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "[ERROR] SOURCE_TARGET_COMMIT is malformed" >&2
  exit 2
fi

dispatch_gh=${DISPATCH_GH:-gh}
if ! command -v "$dispatch_gh" >/dev/null 2>&1; then
  echo "[ERROR] DISPATCH_GH is not executable" >&2
  exit 2
fi

target="${TARGET_OWNER}/${TARGET_REPO}"
tag="v${VERSION}"

delivery_id=$(jq -cn \
  --arg commit "$SOURCE_TARGET_COMMIT" \
  --arg event_type "$EVENT_TYPE" \
  --arg source "$SOURCE_REPOSITORY" \
  --arg tag "$tag" \
  --arg target "$target" \
  --arg version "$VERSION" \
  '{
    commit: $commit,
    event_type: $event_type,
    source: $source,
    tag: $tag,
    target: $target,
    version: $version
  }' | sha256sum | awk '{print $1}')

jq -cn \
  --arg event_type "$EVENT_TYPE" \
  --arg version "$VERSION" \
  --arg source_repository "$SOURCE_REPOSITORY" \
  --arg target_commit "$SOURCE_TARGET_COMMIT" \
  --arg delivery_id "$delivery_id" \
  '{
    event_type: $event_type,
    client_payload: {
      delivery_id: $delivery_id,
      release_tag: ("v" + $version),
      release_url: (
        "https://github.com/" + $source_repository + "/releases/tag/v" + $version
      ),
      target_commit: $target_commit,
      trigger_source: $source_repository,
      version: $version
    }
  }' |
  "$dispatch_gh" api --method POST \
    "repos/${TARGET_OWNER}/${TARGET_REPO}/dispatches" \
    --input -
