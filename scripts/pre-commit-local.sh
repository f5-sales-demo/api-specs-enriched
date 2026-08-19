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
  # This used to be `... 2>/dev/null || echo "failed or not configured"`, which threw
  # away the diagnostics AND the exit code, so a real failure was indistinguishable
  # from a missing module and the hook went on to report success. It failed on main
  # for as long as securemesh_site_v2 had been in minimum_configs.yaml without a
  # matching resource_metadata.yaml entry, and nobody saw it.
  #
  # "not importable" and "validation failed" are now different outcomes: the first
  # is skipped with a warning, the second stops the commit.
  if ! $PYTHON -c "import scripts.validate_configs" 2>/dev/null; then
    echo "[local] scripts.validate_configs is not importable — skipping" >&2
  elif ! $PYTHON -m scripts.validate_configs; then
    echo "[local] config validation FAILED — see the errors above" >&2
    exit 1
  fi
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
MYPY=""
if [ -x "${REPO_ROOT}/.venv/bin/mypy" ]; then
  MYPY="${REPO_ROOT}/.venv/bin/mypy"
elif command -v mypy &>/dev/null; then
  MYPY="$(command -v mypy)"
fi

PY_FILES_NO_TESTS=$(echo "$STAGED_FILES" | grep '\.py$' | grep -v '^tests/' | grep -v '^docs/' || true)
if [ -n "$PY_FILES_NO_TESTS" ]; then
  if [ -n "$MYPY" ]; then
    echo "[local] Running mypy type checking..."
    echo "$PY_FILES_NO_TESTS" | xargs "$MYPY" --ignore-missing-imports --no-error-summary
  else
    echo "[local] mypy is not installed — skipping Python type checking" >&2
  fi
fi

echo "[local] All repo-specific checks passed."
