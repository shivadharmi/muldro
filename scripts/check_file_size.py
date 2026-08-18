#!/usr/bin/env python3
"""Pre-commit hook: enforce file size caps from docs/engineering-standards.md.

Caps: Python 800 lines, React/TS components 400, Zustand stores 200.
Files in GRANDFATHERED existed before the standard; they may not grow past
their recorded size and should shrink over time. Remove entries as files are split.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PY_CAP = 800
TSX_CAP = 400
STORE_CAP = 200

# path -> line count at adoption (2026-06-12). Files may not exceed this.
GRANDFATHERED: dict[str, int] = {
    "backend/src/orchestrator/muldro.py": 3427,
    "backend/src/services/graph_executor.py": 2026,
    "backend/src/services/surface_detail_builders.py": 1610,
    "backend/src/services/scheduler.py": 1215,
    "backend/src/tools/intelligence_server.py": 1214,
    "backend/src/services/memory_service.py": 1142,
    "backend/src/api/routes_auth.py": 1036,
    "backend/src/integrations/session_pool.py": 871,
    "backend/src/integrations/mcp_pool.py": 900,
    "frontend/src/lib/api.ts": 897,
    "frontend/src/components/chat/chat-panel.tsx": 678,
    "frontend/src/components/history/run-detail-modal.tsx": 543,
    "frontend/src/app/settings/page.tsx": 522,
}
GRANDFATHER_SLACK = 40  # small headroom so unrelated edits don't hard-block


def cap_for(path: str) -> int:
    if "stores/" in path and path.endswith(".ts"):
        return STORE_CAP
    if path.endswith((".tsx", ".ts")):
        return TSX_CAP
    if path.endswith(".py"):
        return PY_CAP
    return 0  # no cap for other file types


def main(argv: list[str]) -> int:
    files = argv or (
        subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    failures: list[str] = []
    for f in files:
        path = Path(f)
        cap = cap_for(f)
        if not cap or not path.exists():
            continue
        lines = sum(1 for _ in path.open("rb"))
        limit = GRANDFATHERED.get(f)
        if limit is not None:
            if lines > limit + GRANDFATHER_SLACK:
                failures.append(
                    f"{f}: {lines} lines — grandfathered at {limit}, must shrink, not grow"
                )
        elif lines > cap:
            failures.append(f"{f}: {lines} lines exceeds the {cap}-line cap — split by responsibility")
    if failures:
        print("File size standard violations (docs/engineering-standards.md §1):")
        for msg in failures:
            print(f"  {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
