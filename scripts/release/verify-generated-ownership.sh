#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

# Enforce the clean-break ownership boundary for generated release output.
# Source/configuration branches validate isolated candidates; only an exact
# semantic release branch may promote publication artifacts.

set -euo pipefail

MODE=""
BASE_REF=""
HEAD_REF=""
BRANCH=""

usage() {
  echo "Usage: $0 --cached --branch BRANCH" >&2
  echo "   or: $0 --base REF --head REF --branch BRANCH" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cached)
      [ -z "$MODE" ] || { usage; exit 2; }
      MODE="cached"
      shift
      ;;
    --base)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      [ -z "$BASE_REF" ] || { usage; exit 2; }
      BASE_REF="$2"
      shift 2
      ;;
    --head)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      [ -z "$HEAD_REF" ] || { usage; exit 2; }
      HEAD_REF="$2"
      shift 2
      ;;
    --branch)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      [ -z "$BRANCH" ] || { usage; exit 2; }
      BRANCH="$2"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ -z "$BRANCH" ]; then
  usage
  exit 2
fi
if [ "$MODE" = "cached" ]; then
  if [ -n "$BASE_REF" ] || [ -n "$HEAD_REF" ]; then
    usage
    exit 2
  fi
elif [ -z "$MODE" ]; then
  if [ -z "$BASE_REF" ] || [ -z "$HEAD_REF" ]; then
    usage
    exit 2
  fi
  MODE="range"
else
  usage
  exit 2
fi

# No prefix or loosely named release branch may acquire artifact ownership.
if [[ "$BRANCH" =~ ^release/v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  exit 0
fi

DIFF_OUTPUT=$(mktemp "${TMPDIR:-/tmp}/generated-ownership.XXXXXX")
# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap below.
cleanup() { rm -f "$DIFF_OUTPUT"; }
trap cleanup EXIT

if [ "$MODE" = "cached" ]; then
  git diff --cached --ita-visible-in-index --no-renames --name-only -z -- >"$DIFF_OUTPUT"
else
  git diff --no-renames --name-only -z "$BASE_REF" "$HEAD_REF" -- >"$DIFF_OUTPUT"
fi

OFFENDING=()
while IFS= read -r -d '' path; do
  case "$path" in
    CHANGELOG.md | .github_release | release/api-catalog.json | \
      docs/openapi-specs-config.json | docs/specifications/* | docs/api-reference/*)
      OFFENDING+=("$path")
      ;;
  esac
done <"$DIFF_OUTPUT"

if [ "${#OFFENDING[@]}" -eq 0 ]; then
  exit 0
fi

echo "ERROR: generated release output is staged on non-release branch '$BRANCH'." >&2
echo "Only release/v* branches with an exact semantic version may own these paths:" >&2
printf '  - %s\n' "${OFFENDING[@]}" >&2
exit 1
