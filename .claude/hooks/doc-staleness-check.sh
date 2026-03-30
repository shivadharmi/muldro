#!/bin/bash
# Stop hook: advisory doc staleness check
# Reads stdin (required by protocol), checks git diff for doc-sensitive files,
# writes advisory to stderr (shown as warning in UI). Never blocks.

INPUT=$(cat)

# Get changed files (unstaged + staged vs HEAD)
CHANGED=$(git diff --name-only HEAD 2>/dev/null; git diff --name-only --cached 2>/dev/null)
CHANGED=$(echo "$CHANGED" | sort -u)
[ -z "$CHANGED" ] && exit 0

DOCS=""
echo "$CHANGED" | grep -q 'backend/src/models/' && DOCS="${DOCS}  - CLAUDE.md + data-model.md (table count)\n"
echo "$CHANGED" | grep -q 'backend/alembic/versions/' && DOCS="${DOCS}  - CLAUDE.md + data-model.md + README.md (migration count)\n"
echo "$CHANGED" | grep -q 'frontend/src/app/.*/page.tsx' && DOCS="${DOCS}  - CLAUDE.md (page count)\n"
echo "$CHANGED" | grep -q 'backend/src/orchestrator/' && DOCS="${DOCS}  - CLAUDE.md + overview.md (orchestrator)\n"
echo "$CHANGED" | grep -q 'backend/src/services/' && DOCS="${DOCS}  - docs/architecture/services.md\n"
echo "$CHANGED" | grep -q 'backend/src/api/routes_' && DOCS="${DOCS}  - CLAUDE.md (router count)\n"
echo "$CHANGED" | grep -q 'docker-compose.yml' && DOCS="${DOCS}  - startup.md + README.md (infra)\n"
echo "$CHANGED" | grep -q 'backend/src/tools/' && DOCS="${DOCS}  - docs/architecture/tools-mcp.md\n"

[ -z "$DOCS" ] && exit 0

# Write advisory to stderr (appears as warning in Claude Code UI)
printf "[doc-staleness] These docs may need updating:\n%b" "$DOCS" >&2
exit 0
