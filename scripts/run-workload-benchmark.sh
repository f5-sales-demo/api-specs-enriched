#!/usr/bin/env bash
# Run five matched base/head pairs from one immutable upstream seed.
set -euo pipefail

: "${RUNNER_CLASS:?RUNNER_CLASS is required}"
: "${BASE_SHA:?BASE_SHA is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"
: "${WORKERS:?WORKERS is required}"
: "${CACHE_STATE:?CACHE_STATE is required}"
: "${SEED_ARCHIVE:?SEED_ARCHIVE is required}"
: "${PROFILE_DIR:?PROFILE_DIR is required}"

case "$RUNNER_CLASS" in d8 | d16) ;; *) exit 2 ;; esac
case "$WORKERS" in 1 | 2 | 4 | 8) ;; *) exit 2 ;; esac
case "$CACHE_STATE" in warm | cold) ;; *) exit 2 ;; esac

root=$(git rev-parse --show-toplevel)
helper="$root/scripts/workload_evidence.py"
mkdir -p "$PROFILE_DIR"

prepare_source() {
  local ref=$1 destination=$2
  rm -rf "$destination"
  mkdir -p "$destination/specs/original"
  git archive "$ref" | tar -xf - -C "$destination"
  tar -xzf "$SEED_ARCHIVE" -C "$destination/specs/original"
}

manifest_args() {
  local source=$1 path
  for path in \
    "$source/docs/specifications/api/concurrency_contracts.json" \
    "$source/docs/specifications/api/smsv2_parity_manifest.json" \
    "$source/release/api-catalog.json"; do
    if [[ -f "$path" ]]; then
      printf '%s\0%s\0' --manifest "$path"
    fi
  done
}

profile_pipeline() {
  local ref=$1 variant=$2 pair=$3 role=$4
  local source="$RUNNER_TEMP/source-${RUNNER_CLASS}-${WORKERS}-${CACHE_STATE}-${role}-${pair}"
  local phase="pipeline-worker-${RUNNER_CLASS}-w${WORKERS}"
  local profile="$PROFILE_DIR/${phase}-${variant}-${CACHE_STATE}-${pair}.json"
  local evidence="$PROFILE_DIR/evidence-${RUNNER_CLASS}-${WORKERS}-${variant}-${CACHE_STATE}-${pair}.json"
  local archive="$PROFILE_DIR/archive-${RUNNER_CLASS}-${WORKERS}-${variant}-${CACHE_STATE}-${pair}.zip"
  prepare_source "$ref" "$source"
  local worker_args=()
  if [[ "$variant" != baseline ]]; then
    worker_args=(--workers "$WORKERS")
  fi
  (
    cd "$source"
    API_SPECS_SKIP_BIOME=1 runner-profile \
      --name "$phase" --output "$profile" --cache-state "$CACHE_STATE" \
      --variant "$variant" --pair-id "$pair" -- \
      python -m scripts.pipeline "${worker_args[@]}"
  )
  local args=(evidence --tree "$source/docs/specifications/api" --archive "$archive" --output "$evidence")
  while IFS= read -r -d '' argument; do args+=("$argument"); done < <(manifest_args "$source")
  python "$helper" "${args[@]}"
  python "$helper" enrich-profile --profile "$profile" --evidence "$evidence" --memory "$source/reports/memory-profile.json"

  # Reuse the same measured run in the cross-runner comparison without
  # pretending a second execution occurred. The phase key prevents collector collisions.
  local route="$PROFILE_DIR/pipeline-routing-w${WORKERS}-${variant}-${CACHE_STATE}-${pair}.json"
  if [[ ("$RUNNER_CLASS" == d8 && "$variant" == baseline) || ("$RUNNER_CLASS" == d16 && "$variant" != baseline) ]]; then
    jq --arg phase "pipeline-routing-w${WORKERS}" '.phase = $phase' "$profile" >"$route"
  fi
}

profile_pytest() {
  local ref=$1 variant=$2 pair=$3
  local source="$RUNNER_TEMP/source-pytest-${RUNNER_CLASS}-${variant}-${pair}"
  local profile="$PROFILE_DIR/pytest-routing-${variant}-${pair}.json"
  local evidence="$PROFILE_DIR/pytest-evidence-${RUNNER_CLASS}-${variant}-${pair}.json"
  local junit="$PROFILE_DIR/pytest-${RUNNER_CLASS}-${variant}-${pair}.xml"
  prepare_source "$ref" "$source"
  (
    cd "$source"
    git init --quiet && git add -A
  )
  (
    cd "$source"
    runner-profile --name pytest-routing --output "$profile" --cache-state warm \
      --variant "$variant" --pair-id "$pair" -- \
      python -m pytest --junitxml "$junit"
  )
  python "$helper" evidence --pytest-xml "$junit" --output "$evidence"
  python "$helper" enrich-profile --profile "$profile" --evidence "$evidence"
}

for pair in 1 2 3 4 5; do
  profile_pipeline "$BASE_SHA" baseline "$pair" base
  profile_pipeline "$HEAD_SHA" "${RUNNER_CLASS}-w${WORKERS}" "$pair" head
done

# Pytest routing is independent of pipeline workers and dependency-cache state.
if [[ "$WORKERS" == 1 && "$CACHE_STATE" == warm ]]; then
  if [[ "$RUNNER_CLASS" == d8 ]]; then
    for pair in 1 2 3 4 5; do profile_pytest "$BASE_SHA" baseline "$pair"; done
  else
    for pair in 1 2 3 4 5; do profile_pytest "$HEAD_SHA" d16-w1 "$pair"; done
  fi
fi
