#!/usr/bin/env bash
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

# Pre-commit hook: Regenerate enriched specs and validate on every commit
#
# IMPORTANT: This hook validates ALL files on every commit, including:
#   - All 270 original specs in specs/original/
#   - All 25 generated specs in docs/specifications/api/ (gitignored)
#   - Linting runs on ALL generated specs, not just staged files
#
# This hook runs the same steps as GitHub Actions workflow:
#   1. Enrichment pipeline (python -m scripts.pipeline)
#   2. Spectral linting (python scripts/lint.py --input-dir docs/specifications/api)
#
# DRY Principle: All methods use the same commands:
#   - Manual: make pipeline && make lint
#   - Pre-commit: this script (runs on every commit)
#   - GitHub Actions: same python commands
#
# This ensures idempotent, deterministic output between local and CI/CD.
# Linting is NEVER skipped - Spectral must be installed.

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Detect Python interpreter
if [ -d ".venv" ]; then
  PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
else
  PYTHON="python"
fi

# =============================================================================
# STEP 0: Skip when no pipeline inputs are staged.
# =============================================================================
# The enrichment pipeline is ~13 min and deterministic in its inputs.
# If nothing staged in this commit can affect the pipeline output
# (scripts/**, config/**, specs/original/**, requirements*.txt,
# pyproject.toml, sync-and-enrich.yml), the previous run's output is
# still valid and re-running is pure waste. Override with
# FORCE_PIPELINE=1 when you need a full regeneration anyway.
if [ "${FORCE_PIPELINE:-0}" != "1" ]; then
  STAGED_INPUTS=$(git diff --cached --name-only | grep -E '^(scripts/|config/|specs/original/|requirements(-dev)?\.txt$|pyproject\.toml$|\.github/workflows/sync-and-enrich\.yml$)' | head -1 || true)
  if [ -z "$STAGED_INPUTS" ]; then
    echo -e "${GREEN}No pipeline inputs staged — skipping enrichment + lint.${NC}"
    echo -e "${GREEN}(Set FORCE_PIPELINE=1 to force a full run.)${NC}"
    exit 0
  fi
fi

# =============================================================================
# Keep generated output transactional: a failing, interrupted, or rejected
# pipeline must leave the working tree and index exactly as it was before this
# hook began. Staging is deliberately deferred until all validation succeeds.
OUTPUT_DIR="docs/specifications/api"
BACKUP_DIR=$(mktemp -d)
OUTPUT_EXISTED=false
INDEX_EXISTED=false
PIPELINE_COMPLETED=false
if [ -d "$OUTPUT_DIR" ]; then
  cp -a "$OUTPUT_DIR" "$BACKUP_DIR/api"
  OUTPUT_EXISTED=true
fi
INDEX_PATH=$(git rev-parse --git-path index)
if [ -f "$INDEX_PATH" ]; then
  cp "$INDEX_PATH" "$BACKUP_DIR/index"
  INDEX_EXISTED=true
fi

# shellcheck disable=SC2317,SC2329 # Invoked by the EXIT/INT/TERM traps below.
restore_generated_output() {
  local status=$?
  trap - EXIT INT TERM
  if [ "$PIPELINE_COMPLETED" != true ]; then
    echo -e "${YELLOW}Restoring generated specs after unsuccessful pre-commit pipeline.${NC}"
    rm -rf "$OUTPUT_DIR"
    if [ "$OUTPUT_EXISTED" = true ]; then
      mkdir -p "$(dirname "$OUTPUT_DIR")"
      cp -a "$BACKUP_DIR/api" "$OUTPUT_DIR"
    fi
    if [ "$INDEX_EXISTED" = true ]; then
      cp "$BACKUP_DIR/index" "$INDEX_PATH"
    else
      rm -f "$INDEX_PATH"
    fi
  fi
  rm -rf "$BACKUP_DIR"
  exit "$status"
}
trap restore_generated_output EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# STEP 1: Run Enrichment Pipeline
# =============================================================================
echo -e "${YELLOW}[1/2] Running F5 XC API enrichment pipeline...${NC}"
echo -e "${YELLOW}Executing: $PYTHON -m scripts.pipeline${NC}"

if ! $PYTHON -m scripts.pipeline; then
  echo -e "${RED}Pipeline failed! Please fix errors before committing.${NC}"
  exit 1
fi

# =============================================================================
# STEP 2: Run Spectral Linting on ALL generated specs (same as GitHub Actions)
# =============================================================================
echo -e "${YELLOW}[2/2] Running Spectral linting on ALL generated specs...${NC}"

# Check if Spectral is installed - REQUIRED, never skip
if ! command -v spectral &>/dev/null; then
  echo -e "${RED}ERROR: Spectral CLI is not installed!${NC}"
  echo -e "${RED}Linting is REQUIRED and cannot be skipped.${NC}"
  echo -e "${YELLOW}Install with: npm install -g @stoplight/spectral-cli${NC}"
  exit 1
fi

echo -e "${YELLOW}Executing: $PYTHON scripts/lint.py --input-dir docs/specifications/api --fail-on-error --fail-on-warning${NC}"
echo -e "${YELLOW}Note: Validating ALL 25 generated specs (including gitignored files)${NC}"

# Run linting on ALL files in the directory - fail on errors AND warnings to ensure clean specs
if $PYTHON scripts/lint.py --input-dir docs/specifications/api --fail-on-error --fail-on-warning; then
  echo -e "${GREEN}Spectral linting passed (all files validated).${NC}"
else
  LINT_EXIT_CODE=$?
  echo -e "${RED}Spectral linting failed with errors!${NC}"
  echo -e "${RED}Fix linting errors before committing.${NC}"
  exit $LINT_EXIT_CODE
fi

verify_release_stamps() {
  local branch expected_version spec actual_version
  branch=$(git branch --show-current)
  case "$branch" in
  release/v*) expected_version=${branch#release/v} ;;
  *) return 0 ;;
  esac

  if ! command -v jq >/dev/null 2>&1; then
    echo -e "${RED}ERROR: jq is required to verify release-stamped artifacts.${NC}"
    return 1
  fi

  for spec in "$OUTPUT_DIR"/*.json; do
    [ -e "$spec" ] || continue
    actual_version=$(jq -r '
      if type != "object" or has("$schema") then empty
      elif ((has("openapi") or has("swagger")) and (.info | type == "object")) then .info.version // empty
      elif has("version") then .version // empty
      else empty
      end
    ' "$spec")
    if [ -n "$actual_version" ] && [ "$actual_version" != "$expected_version" ]; then
      echo -e "${RED}ERROR: $(basename "$spec") version ${actual_version} does not match release branch ${expected_version}.${NC}"
      return 1
    fi
  done
}

if ! verify_release_stamps; then
  echo -e "${RED}Refusing to stage generated specs with a release-version mismatch.${NC}"
  exit 1
fi

# Stage only validated enriched specs. The generated directory is ignored, so
# `git diff` cannot discover fresh output. Force-add every validated JSON file
# after all checks pass; unchanged tracked files leave the index untouched.
# The saved index above makes any later failure transactional as well.
ENRICHED_SPECS=()
if [ -d "$OUTPUT_DIR" ]; then
  mapfile -d '' -t ENRICHED_SPECS < <(find "$OUTPUT_DIR" -maxdepth 1 -type f -name '*.json' -print0)
fi
if [ "${#ENRICHED_SPECS[@]}" -gt 0 ]; then
  echo -e "${YELLOW}Staging ${#ENRICHED_SPECS[@]} validated enriched spec files...${NC}"
  git add -f --ignore-errors -- "${ENRICHED_SPECS[@]}"
  echo -e "${GREEN}Enriched specs updated and staged.${NC}"
else
  echo -e "${GREEN}No enriched spec changes detected.${NC}"
fi

PIPELINE_COMPLETED=true
echo -e "${GREEN}Pre-commit pipeline complete.${NC}"
exit 0
