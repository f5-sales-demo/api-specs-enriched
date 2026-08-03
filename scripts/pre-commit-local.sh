#!/usr/bin/env bash
# Repository-specific pre-commit hooks for api-specs-enriched
# Called by the universal .pre-commit-config.yaml local-hooks entry
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM)

PYTHON="${REPO_ROOT}/.venv/bin/python3"
RUFF="${REPO_ROOT}/.venv/bin/ruff"
MYPY="${REPO_ROOT}/.venv/bin/mypy"

# --- F5 XC API Enrichment Pipeline ---
if [ ! -x scripts/hooks/pre-commit-pipeline.sh ]; then
  echo "[local] required enrichment pipeline hook is missing or not executable" >&2
  exit 1
fi
echo "[local] Running F5 XC API enrichment pipeline..."
scripts/hooks/pre-commit-pipeline.sh

# --- Config interdependency validation ---
CONFIG_FILES=$(echo "$STAGED_FILES" | grep '^config/.*\.yaml$' || true)
if [ -n "$CONFIG_FILES" ]; then
  if [ ! -x "$PYTHON" ]; then
    echo "[local] project Python environment is required; run: make install" >&2
    exit 1
  fi
  echo "[local] Validating config interdependencies..."
  if ! $PYTHON -c "import scripts.validate_configs" 2>/dev/null; then
    echo "[local] scripts.validate_configs is not importable" >&2
    exit 1
  elif ! $PYTHON -m scripts.validate_configs; then
    echo "[local] config validation FAILED — see the errors above" >&2
    exit 1
  fi
fi

# --- Python linting (ruff) ---
PY_FILES=$(echo "$STAGED_FILES" | grep '\.py$' || true)
if [ -n "$PY_FILES" ]; then
  if [ ! -x "$RUFF" ]; then
    echo "[local] project ruff is required; run: make install" >&2
    exit 1
  fi
  echo "[local] Linting Python files with ruff..."
  echo "$PY_FILES" | xargs "$RUFF" check
  echo "$PY_FILES" | xargs "$RUFF" format --check
fi

# --- Python type checking (mypy) ---
PY_FILES_NO_TESTS=$(echo "$STAGED_FILES" | grep '\.py$' | grep -v '^tests/' | grep -v '^docs/' || true)
if [ -n "$PY_FILES_NO_TESTS" ]; then
  if [ ! -x "$MYPY" ]; then
    echo "[local] project mypy is required; run: make install" >&2
    exit 1
  fi
  echo "[local] Running mypy type checking..."
  echo "$PY_FILES_NO_TESTS" | xargs "$MYPY" --ignore-missing-imports --no-error-summary
fi

echo "[local] All repo-specific checks passed."
