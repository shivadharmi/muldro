#!/bin/bash
# Pre-commit hook for Jarvis project
# Mirrors CI checks: ruff lint + format, TypeScript types, secrets scan

set -e

cd "$(git rev-parse --show-toplevel)"

ERRORS=0

# Check Python lint + format (matches CI: ruff check + ruff format --check)
if git diff --cached --name-only | grep -q '\.py$'; then
  echo "Running ruff on staged Python files..."
  cd backend
  if [ -d ".venv" ]; then
    source .venv/bin/activate
    if ! ruff check src/ tests/ 2>/dev/null; then
      echo "ERROR: Python lint failed. Run 'ruff check --fix src/ tests/' to fix."
      ERRORS=1
    fi
    if ! ruff format --check src/ tests/ 2>/dev/null; then
      echo "ERROR: Python format check failed. Run 'ruff format src/ tests/' to fix."
      ERRORS=1
    fi
  fi
  cd ..
fi

# Check TypeScript
if git diff --cached --name-only | grep -qE '\.tsx?$'; then
  echo "Checking TypeScript types..."
  if [ -d "frontend/node_modules" ]; then
    cd frontend
    if ! npx tsc --noEmit 2>/dev/null; then
      echo "ERROR: TypeScript type check failed."
      ERRORS=1
    fi
    cd ..
  fi
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
