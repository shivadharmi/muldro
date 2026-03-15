#!/bin/bash
# Pre-commit hook for Jarvis project
# Runs lint checks on staged Python files

set -e

cd "$(git rev-parse --show-toplevel)"

ERRORS=0

# Check Python lint
if git diff --cached --name-only | grep -q '\.py$'; then
  echo "Running ruff on staged Python files..."
  cd backend
  if [ -d ".venv" ]; then
    source .venv/bin/activate
    if ! ruff check src/ tests/ 2>/dev/null; then
      echo "ERROR: Python lint failed. Run 'ruff check --fix src/ tests/' to fix."
      ERRORS=1
    fi
  fi
  cd ..
fi

# Check for secrets in staged files (exclude example files and this hook)
SECRET_FILES=$(git diff --cached --diff-filter=A --name-only | grep -v -E '\.(example|sh)$' | xargs grep -l -E '(sk-ant-|AKIA|password\s*=\s*["\x27][^"\x27]+)' 2>/dev/null || true)
if [ -n "$SECRET_FILES" ]; then
  echo "ERROR: Possible secrets detected in staged files:"
  echo "$SECRET_FILES"
  ERRORS=1
fi

if [ $ERRORS -ne 0 ]; then
  echo "Pre-commit checks failed. Fix errors before committing."
  exit 1
fi

echo "Pre-commit checks passed."
