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

set -e

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
# (scripts/**, config/**, specs/original/**, pyproject.toml, uv.lock,
# sync-and-enrich.yml), the previous run's output is
# still valid and re-running is pure waste. Override with
# FORCE_PIPELINE=1 when you need a full regeneration anyway.
OUTPUT_DIR="docs/specifications/api"
CATALOG_FILE="release/api-catalog.json"
CURRENT_BRANCH=$(git branch --show-current)

# Check ownership before the no-input fast path so an output-only commit cannot
# bypass the clean-break protocol.
if ! OWNERSHIP_ERROR=$(bash scripts/release/verify-generated-ownership.sh \
  --cached --branch "$CURRENT_BRANCH" 2>&1); then
  echo -e "${RED}${OWNERSHIP_ERROR}${NC}"
  exit 1
fi

PIPELINE_INPUT_PATTERN='^(scripts/|config/|specs/original/|pyproject\.toml$|uv\.lock$|\.github_release$|\.github/workflows/sync-and-enrich\.yml$)'
if [ "${FORCE_PIPELINE:-0}" != "1" ]; then
  STAGED_INPUTS=$(git diff --cached --name-only | grep -E "$PIPELINE_INPUT_PATTERN" | head -1 || true)
  if [ -z "$STAGED_INPUTS" ]; then
    echo -e "${GREEN}No pipeline inputs staged — skipping enrichment + lint.${NC}"
    echo -e "${GREEN}(Set FORCE_PIPELINE=1 to force a full run.)${NC}"
    exit 0
  fi
fi

# The candidate must be a function of the proposed commit, never of unstaged
# source/config bytes that the commit will omit. specs/original is intentionally
# release-downloaded and ignored, so only tracked pipeline code/config paths are
# subject to this index/worktree equality guard.
COMMIT_INPUT_PATTERN='^(scripts/|config/|pyproject\.toml$|uv\.lock$|\.github_release$|\.github/workflows/sync-and-enrich\.yml$)'
UNSTAGED_INPUT=$({
  git diff --name-only
  git ls-files --others --exclude-standard
} | grep -E "$COMMIT_INPUT_PATTERN" | head -1 || true)
if [ -n "$UNSTAGED_INPUT" ]; then
  echo -e "${RED}ERROR: pipeline input has unstaged or untracked changes: $UNSTAGED_INPUT${NC}"
  echo -e "${RED}Stage the complete input or restore it before committing.${NC}"
  exit 1
fi

# =============================================================================
# STEP 1: Run Enrichment Pipeline
# =============================================================================
echo -e "${YELLOW}[1/2] Running F5 XC API enrichment pipeline...${NC}"

# Never overwrite an operator's generated-output work. The pipeline is built in
# isolation below and promoted only after version verification and lint pass.
UNTRACKED_OUTPUT=$({
  git ls-files --others --exclude-standard -- "$OUTPUT_DIR"
  git ls-files --others --ignored --exclude-standard -- "$OUTPUT_DIR"
  git ls-files --others --exclude-standard -- "$CATALOG_FILE"
} | head -1)
if ! git diff --quiet -- "$OUTPUT_DIR" || \
  ! git diff --cached --quiet -- "$OUTPUT_DIR" || \
  ! git diff --quiet -- "$CATALOG_FILE" || \
  [ -n "$UNTRACKED_OUTPUT" ]; then
  echo -e "${RED}ERROR: generated output already has staged or unstaged changes.${NC}"
  echo -e "${RED}Commit or restore that work before running the pipeline hook.${NC}"
  exit 1
fi

EXPECTED_VERSION=$($PYTHON -m scripts.utils.version_calculator)
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/api-specs-enriched.XXXXXX")
TEMP_OUTPUT="$TEMP_ROOT/specifications"
TEMP_REPORT="$TEMP_ROOT/reports"
TEMP_CATALOG="$TEMP_ROOT/api-catalog.json"
# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap below.
cleanup() { rm -rf "$TEMP_ROOT"; }
trap cleanup EXIT

echo -e "${YELLOW}Executing pipeline with temporary output and report directories${NC}"

if ! $PYTHON -m scripts.pipeline \
  --version "$EXPECTED_VERSION" \
  --output-dir "$TEMP_OUTPUT" \
  --report-dir "$TEMP_REPORT"; then
  echo -e "${RED}Pipeline failed! Please fix errors before committing.${NC}"
  exit 1
fi

if ! $PYTHON -m scripts.stamp_release_version "$TEMP_OUTPUT" \
  --check-version "$EXPECTED_VERSION"; then
  echo -e "${RED}ERROR: generated artifacts disagree with the committed build version; nothing was staged.${NC}"
  exit 1
fi

if ! $PYTHON -m scripts.compile_catalog \
  --version "$EXPECTED_VERSION" \
  --input "$TEMP_OUTPUT/openapi.json" \
  --output "$TEMP_CATALOG"; then
  echo -e "${RED}Catalog compilation failed; nothing was staged.${NC}"
  exit 1
fi

if ! jq -e --arg version "$EXPECTED_VERSION" \
  'type == "object" and .version == $version' "$TEMP_CATALOG" >/dev/null; then
  echo -e "${RED}ERROR: generated catalog disagrees with the committed build version; nothing was staged.${NC}"
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
  echo -e "${YELLOW}Install with: npm ci --ignore-scripts --no-audit --no-fund${NC}"
  exit 1
fi

echo -e "${YELLOW}Executing: $PYTHON scripts/lint.py --input-dir <temporary-directory> --fail-on-error --fail-on-warning${NC}"
echo -e "${YELLOW}Note: Validating ALL 25 generated specs (including gitignored files)${NC}"

# Run linting on ALL files in the directory - fail on errors AND warnings to ensure clean specs
if $PYTHON scripts/lint.py --input-dir "$TEMP_OUTPUT" --fail-on-error --fail-on-warning; then
  echo -e "${GREEN}Spectral linting passed (all files validated).${NC}"
else
  LINT_EXIT_CODE=$?
  echo -e "${RED}Spectral linting failed with errors!${NC}"
  echo -e "${RED}Fix linting errors before committing.${NC}"
  exit $LINT_EXIT_CODE
fi

# Source/config commits validate the candidate but do not rewrite the committed
# release artifact. The asynchronous producer regenerates after such a commit
# reaches main and creates a version-stamped release PR. Promoting here would
# smuggle changed content into main under the previous release version, leaving
# the producer with no diff to publish.
if [[ "$CURRENT_BRANCH" != release/v* ]] || \
  [[ ! "$CURRENT_BRANCH" =~ ^release/v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo -e "${GREEN}Candidate output and lint verified; release artifacts were not modified.${NC}"
  exit 0
fi

# A release branch is the sole place where the verified candidate is promoted.
# No failing path above mutates the checked-out generated tree or index.
mkdir -p "$OUTPUT_DIR"
rsync -a --delete "$TEMP_OUTPUT/" "$OUTPUT_DIR/"
mkdir -p "$(dirname "$CATALOG_FILE")"
rsync -a "$TEMP_CATALOG" "$CATALOG_FILE"

# Stage only after generation, release-version verification, and lint all
# succeed. Note: openapi.json is ignored because it is too large for GitHub.
ENRICHED_CHANGES=$(git diff --name-only -- "$OUTPUT_DIR" "$CATALOG_FILE" 2>/dev/null | wc -l | tr -d ' ')

if [ "$ENRICHED_CHANGES" -gt 0 ]; then
  echo -e "${YELLOW}Staging $ENRICHED_CHANGES updated enriched spec files...${NC}"
  git add -u -- "$OUTPUT_DIR"
  for spec_file in "$OUTPUT_DIR"/*.json; do
    [ "$(basename "$spec_file")" = "openapi.json" ] && continue
    git add -f -- "$spec_file"
  done
  git add -f -- "$CATALOG_FILE"
  echo -e "${GREEN}Enriched specs updated and staged.${NC}"
else
  echo -e "${GREEN}No enriched spec changes detected.${NC}"
fi

echo -e "${GREEN}Pre-commit pipeline complete.${NC}"
exit 0
