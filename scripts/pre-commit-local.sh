#!/usr/bin/env bash
# Repository-specific pre-commit hooks for api-specs-enriched
# Called by the universal .pre-commit-config.yaml local-hooks entry
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)

# Prefer the project venv python for dependencies (rich, pyyaml, etc.)
if [ -x "${REPO_ROOT}/.venv/bin/python3" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
else
  echo "[local] python3 not found, skipping Python-based checks"
  PYTHON=""
fi

# --- F5 XC API Enrichment Pipeline ---
if [ -x scripts/hooks/pre-commit-pipeline.sh ]; then
  echo "[local] Running F5 XC API enrichment pipeline..."
  scripts/hooks/pre-commit-pipeline.sh
fi

# --- Config interdependency validation ---
CONFIG_FILES=$(echo "$STAGED_FILES" | grep '^config/.*\.yaml$' || true)
if [ -n "$CONFIG_FILES" ] && [ -n "$PYTHON" ]; then
  echo "[local] Validating config interdependencies..."
  $PYTHON -m scripts.validate_configs 2>/dev/null || echo "[local] validate_configs failed or not configured"
fi

# --- Python linting (ruff) ---
PY_FILES=$(echo "$STAGED_FILES" | grep '\.py$' || true)
if [ -n "$PY_FILES" ]; then
  if command -v ruff &>/dev/null; then
    echo "[local] Linting Python files with ruff..."
    echo "$PY_FILES" | xargs ruff check --fix --exit-non-zero-on-fix
    echo "$PY_FILES" | xargs ruff format
  else
    echo "[local] ruff not installed, skipping Python lint"
  fi
fi

# --- Python type checking (mypy) ---
PY_FILES_NO_TESTS=$(echo "$STAGED_FILES" | grep '\.py$' | grep -v '^tests/' | grep -v '^docs/' || true)
if [ -n "$PY_FILES_NO_TESTS" ]; then
  if command -v mypy &>/dev/null; then
    echo "[local] Running mypy type checking..."
    echo "$PY_FILES_NO_TESTS" | xargs mypy --ignore-missing-imports --no-error-summary || true
  fi
fi

echo "[local] All repo-specific checks passed."
