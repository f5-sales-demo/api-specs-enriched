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
UPSTREAM_STATE=".github_release"

# Every path used as a change signal, guarded below. Both match a .gitignore
# pattern and are tracked only because the release commit force-adds them, so
# the invariant that matters is trackedness, not .gitignore membership.
SIGNAL_PATHS=("$OUTPUT_DIR" "$UPSTREAM_STATE")

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
# A diff made only of these must not publish an otherwise empty release.
VOLATILE_FILTER='del(.timestamp, .generated_at, .version, .info.version)'

GH_CMD="${DETECT_RELEASE_GH:-gh}"

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must point at the step output file}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

emit() {
  printf '%s\n' "$@" >>"$GITHUB_OUTPUT"
}

# Canonicalise one generated artifact into $1, dropping volatile metadata.
# Input arrives on stdin. Anything jq cannot parse is compared byte-for-byte
# instead, so a non-JSON artifact is never silently treated as unchanged.
normalize_to() {
  local dest="$1"
  local raw="${WORK_DIR}/raw"
  cat >"$raw"
  if ! jq -S "$VOLATILE_FILTER" "$raw" >"$dest" 2>/dev/null; then
    cp "$raw" "$dest"
  fi
}

# A signal path with no tracked files cannot ever produce a diff. That was
# defect (1) of #1094 and it failed silently for months; make it loud.
for path in "${SIGNAL_PATHS[@]}"; do
  if [ -z "$(git ls-files -- "$path")" ]; then
    echo "::error::Change signal '${path}' has no files tracked in git," \
      "so a diff on it can never report a change (see #1094)." >&2
    exit 1
  fi
done

if [ ! -f "$INDEX_FILE" ]; then
  emit "has_changes=false"
  echo "No enriched specs generated (${INDEX_FILE} is missing)"
  exit 0
fi

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
COMMITTED_TAG="$({ git show "HEAD:${UPSTREAM_STATE}" 2>/dev/null || true; } | jq -r '.tag_name // ""' 2>/dev/null || true)"
CURRENT_TAG=""
if [ -f "$UPSTREAM_STATE" ]; then
  CURRENT_TAG="$(jq -r '.tag_name // ""' "$UPSTREAM_STATE" 2>/dev/null || true)"
fi

SOURCE_CHANGED=false
if [ "$CURRENT_TAG" != "$COMMITTED_TAG" ]; then
  SOURCE_CHANGED=true
  echo "Upstream release changed: '${COMMITTED_TAG:-none}' -> '${CURRENT_TAG:-none}'"
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
  { git show "HEAD:${file}" 2>/dev/null || true; } | normalize_to "${WORK_DIR}/committed"
  normalize_to "${WORK_DIR}/current" <"$file"
  if ! cmp -s "${WORK_DIR}/committed" "${WORK_DIR}/current"; then
    echo "Generated output changed: ${file}"
    OUTPUT_CHANGED=true
    break
  fi
done < <(git diff --name-only HEAD -- "$OUTPUT_DIR")

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
