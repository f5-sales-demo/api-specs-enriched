#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
#
# Decide whether a sync-and-enrich run has anything to release, and classify
# the change for semantic versioning.
#
# Writes to $GITHUB_OUTPUT:
#   has_changes   true|false  Whether the run should publish a release.
#   change_type   source|pipeline
#                 `source`   a new upstream api-specs release is in play.
#                 `pipeline` output moved without a new upstream release.
#                 Omitted when the pipeline produced no output at all.
#
# Environment:
#   GITHUB_OUTPUT      Required. Step output file appended to.
#   DETECT_RELEASE_GH  Override the `gh` command (tests inject a fake).
#
# Why this is output-driven
# -------------------------
# The retired inline version asked "did the *previous commit* change the
# inputs?" with `git diff HEAD~1 HEAD -- specs/original/ .github_release
# scripts/ config/ requirements.txt <this workflow>`. That could never see an
# upstream release (#1094):
#
#   * `specs/original/` is gitignored and has zero tracked files, so a diff on
#     it is structurally silent.
#   * `.github_release` is tracked, but the download step rewrites it in the
#     WORKING TREE, which a comparison of two committed revisions cannot see.
#
# So the gate only ever fired when this repo's own previous commit touched
# code, and every release in the history was a code merge. The only correct
# question is "does what we just generated differ from what is committed?",
# which is what this script asks. Being output-driven, it needs no
# per-trigger special-casing: a code change that alters output releases, and
# one that does not has nothing to release.

set -euo pipefail

OUTPUT_DIR="docs/specifications/api"
INDEX_FILE="${OUTPUT_DIR}/index.json"
API_REFERENCE_DIR="docs/api-reference"
OPENAPI_CONFIG="docs/openapi-specs-config.json"
UPSTREAM_STATE=".github_release"
CATALOG_FILE="release/api-catalog.json"
RELEASE_README="release/README.md"
BASE_REF="${DETECT_RELEASE_BASE:-HEAD}"

# Every path used as a change signal is guarded below. Some generated paths
# match .gitignore and are tracked only because the release commit force-adds
# them, so the invariant that matters is trackedness, not ignore membership.
SIGNAL_PATHS=(
  "$OUTPUT_DIR"
  "$API_REFERENCE_DIR"
  "$OPENAPI_CONFIG"
  "$UPSTREAM_STATE"
  "$CATALOG_FILE"
  "$RELEASE_README"
)

# Fields that move without the content moving, stripped before comparing:
#
#   timestamp, generated_at  A fresh wall clock on every pipeline run
#                            (scripts/pipeline.py create_spec_index, plus the
#                            namespace-profile and validation report writers).
#   version, info.version    Version STAMPS, assigned by the release process
#                            itself in `Update version in specs` — after this
#                            gate has already run. Treating them as a change
#                            signal is circular: the pipeline writes the
#                            previous tag, the committed files carry the tag
#                            they shipped as, so a cache-restored tree always
#                            looks "changed" and every run would publish a
#                            content-free patch release.
#
# A diff made only of these must not publish an otherwise empty release. Root
# `version` is not generically volatile: validation.json uses it as its schema
# format version. Only the four measured release-stamped artifacts may drop it.
VOLATILE_FILTER='del(.timestamp, .generated_at) | if has("openapi") then del(.info.version) else . end'

GH_CMD="${DETECT_RELEASE_GH:-gh}"

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must point at the step output file}"

if ! git rev-parse --verify "${BASE_REF}^{commit}" >/dev/null 2>&1; then
  echo "::error::Release comparison base is not a commit: ${BASE_REF}" >&2
  exit 1
fi
if ! git merge-base --is-ancestor "$BASE_REF" HEAD; then
  echo "::error::Release comparison base ${BASE_REF} is not an ancestor of HEAD" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

emit() {
  printf '%s\n' "$@" >>"$GITHUB_OUTPUT"
}

# Canonicalise one generated JSON artifact into $1, dropping volatile metadata.
# Invalid JSON is a broken candidate, not a byte-comparison compatibility case.
normalize_to() {
  local dest="$1" artifact="$2" filter="$VOLATILE_FILTER" expected_type="object"
  local raw="${WORK_DIR}/raw"
  case "$artifact" in
    "$INDEX_FILE" | "$OUTPUT_DIR/minimal-export-defaults.json" | \
      "$OUTPUT_DIR/namespace_profiles.json" | "$CATALOG_FILE")
      filter="$filter | del(.version)"
      ;;
    "$OPENAPI_CONFIG")
      filter='.'
      expected_type="array"
      ;;
  esac
  cat >"$raw"
  if ! jq -eS "select(type == \"$expected_type\") | $filter" "$raw" >"$dest"; then
    echo "::error::Generated publication signal has the wrong JSON type" >&2
    exit 1
  fi
}

# Extract and validate the immutable part of the upstream release receipt.
# Download timestamps are deliberately excluded, but malformed receipt state
# must never collapse to an empty identity and compare equal.
upstream_identity() {
  jq -er '
    if type != "object"
      or (.version | type) != "string"
      or (.tag_name | type) != "string"
      or .tag_name != ("v" + .version)
      or (.published_at | type) != "string"
      or (.published_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")) != true
      or (.asset_name | type) != "string"
      or (.asset_name | test("^[^/[:cntrl:]]+$")) != true
      or (.asset_digest | type) != "string"
      or (.asset_digest | test("^sha256:[0-9a-f]{64}$")) != true
    then error("invalid upstream release state")
    else [.tag_name, .asset_name, .asset_digest] | @tsv
    end
  ' || {
    echo "::error::Invalid upstream release state" >&2
    return 1
  }
}

# A signal must exist either in the comparison base or as a generated file in
# the worktree. An empty directory has no diffable state and is not measurable.
# This permits a first publication while still making an unmeasurable signal
# loud (defect (1) of #1094 failed silently for months).
worktree_signal_exists() {
  local path="$1"
  if [ -f "$path" ]; then
    return 0
  fi
  [ -d "$path" ] && [ -n "$(find "$path" -type f -print -quit)" ]
}

for path in "${SIGNAL_PATHS[@]}"; do
  if ! git cat-file -e "${BASE_REF}:${path}" 2>/dev/null && \
    ! worktree_signal_exists "$path"; then
    echo "::error::Change signal '${path}' is absent from both ${BASE_REF}" \
      "and the generated worktree, so it cannot be measured (see #1094)." >&2
    exit 1
  fi
done

for required in "$INDEX_FILE" "$CATALOG_FILE" "$UPSTREAM_STATE" "$RELEASE_README"; do
  if [ ! -f "$required" ]; then
    echo "::error::Required generated artifact is missing: ${required}" >&2
    exit 1
  fi
done

# Validate every identity that exists before the initial-release shortcut as
# well. A malformed receipt can never be published. Absence at the base is a
# measurable first publication, represented by an empty committed identity.
COMMITTED_IDENTITY=""
if git cat-file -e "${BASE_REF}:${UPSTREAM_STATE}" 2>/dev/null; then
  COMMITTED_IDENTITY="$(git show "${BASE_REF}:${UPSTREAM_STATE}" | upstream_identity)"
fi
CURRENT_IDENTITY="$(upstream_identity <"$UPSTREAM_STATE")"

# Fresh repo / migration: nothing published yet, so publish.
RELEASE_COUNT="$("$GH_CMD" release list --limit 1 2>/dev/null | wc -l | tr -d ' ')"
if [ "${RELEASE_COUNT:-0}" -eq 0 ]; then
  emit "has_changes=true" "change_type=source"
  echo "No releases found - forcing initial release"
  exit 0
fi

# --- Is a new upstream api-specs release in play? --------------------------
# Classify on the release IDENTITY, not on the bytes of .github_release: the
# download step rewrites `downloaded_at` on every forced download, so a byte
# comparison would report `source` on every run and never `pipeline`.
SOURCE_CHANGED=false
if [ "$CURRENT_IDENTITY" != "$COMMITTED_IDENTITY" ]; then
  SOURCE_CHANGED=true
  echo "Upstream release asset identity changed"
fi

# --- Did the generated output move? ---------------------------------------
OUTPUT_CHANGED=false
while IFS= read -r file; do
  [ -n "$file" ] || continue
  if [ ! -f "$file" ]; then
    echo "Generated output changed: ${file} was removed"
    OUTPUT_CHANGED=true
    break
  fi
  if ! git cat-file -e "${BASE_REF}:${file}" 2>/dev/null; then
    echo "Generated output changed: ${file} was added"
    OUTPUT_CHANGED=true
    break
  fi
  case "$file" in
    "$OUTPUT_DIR"/*.json | "$CATALOG_FILE" | "$OPENAPI_CONFIG")
      git show "${BASE_REF}:${file}" | normalize_to "${WORK_DIR}/committed" "$file"
      # The normalizer writes WORK_DIR/current, never the source artifact.
      # shellcheck disable=SC2094
      normalize_to "${WORK_DIR}/current" "$file" <"$file"
      ;;
    *)
      git show "${BASE_REF}:${file}" >"${WORK_DIR}/committed"
      dd if="$file" of="${WORK_DIR}/current" status=none
      ;;
  esac
  if ! cmp -s "${WORK_DIR}/committed" "${WORK_DIR}/current"; then
    echo "Generated output changed: ${file}"
    OUTPUT_CHANGED=true
    break
  fi
done < <(
  {
    git ls-tree -r --name-only "$BASE_REF" -- \
      "$OUTPUT_DIR" "$API_REFERENCE_DIR" "$CATALOG_FILE" "$OPENAPI_CONFIG"
    find "$OUTPUT_DIR" "$API_REFERENCE_DIR" -type f -print
    printf '%s\n' "$CATALOG_FILE" "$OPENAPI_CONFIG"
  } | LC_ALL=C sort -u
)

if [ "$OUTPUT_CHANGED" = false ]; then
  if ! git cat-file -e "${BASE_REF}:${RELEASE_README}" 2>/dev/null; then
    echo "Generated output changed: ${RELEASE_README} was added"
    OUTPUT_CHANGED=true
  elif ! git show "${BASE_REF}:${RELEASE_README}" | \
    cmp -s - "$RELEASE_README"; then
    echo "Generated output changed: ${RELEASE_README}"
    OUTPUT_CHANGED=true
  fi
fi

if [ "$SOURCE_CHANGED" = false ] && [ "$OUTPUT_CHANGED" = false ]; then
  emit "has_changes=false"
  echo "No release: generated output matches HEAD and upstream release is unchanged"
  exit 0
fi

if [ "$SOURCE_CHANGED" = true ]; then
  CHANGE_TYPE="source"
  echo "Source spec changes detected"
else
  CHANGE_TYPE="pipeline"
  echo "Pipeline/config/workflow changes detected"
fi

emit "has_changes=true" "change_type=${CHANGE_TYPE}"
