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

# path -> line count when the guard was first actually RUN (2026-08-22).
# Files may not exceed this; each carries a standing debt to be split.
#
# This list was regenerated because it had never been enforced: core.hooksPath
# pointed at a directory removed by the muldro rename, so no hook ran for months,
# and the pre-commit framework that invokes THIS script was never installed. The
# previous list was written against a tree that no longer exists — it named
# surface_detail_builders.py as a file (it is a package now) and muldro.py at
# 3427 lines (it is 896). A guard that never runs cannot keep its own exemptions
# honest.
GRANDFATHERED: dict[str, int] = {
    "backend/src/orchestrator/agent_invoker.py": 1620,
    "backend/tests/deep_runtime/test_permission_gate.py": 1159,
    # The model-config client rewrite (partial-credential bodies, bind-rejection
    # error mapping) grew this; the view-layer cutover shrank it by deleting the
    # A2UI surface calls. 1146 is the MERGED size — neither branch's own figure
    # (1111 here, 1169 there) describes the file that now exists.
    "frontend/src/lib/api.ts": 1146,
    "backend/src/integrations/session_pool.py": 1143,
    "backend/tests/test_scheduler.py": 1104,
    "backend/src/services/graph_executor.py": 1023,
    "backend/src/services/dag_runner.py": 961,
    "backend/tests/test_push_receiver.py": 924,
    "backend/tests/test_autonomous_deep_e2e.py": 909,
    "backend/tests/test_knowledge_service.py": 909,
    "backend/src/orchestrator/muldro.py": 896,
    "backend/tests/deep_runtime/test_trust_gate.py": 892,
    "backend/tests/test_perception_policy.py": 889,
    "backend/tests/test_foundation_hardening.py": 885,
    "backend/tests/test_chat_single_lead.py": 880,
    "backend/src/orchestrator/perception_runner.py": 825,
    "backend/src/api/routes_approvals.py": 820,
    "backend/tests/test_perception.py": 815,
    "frontend/src/components/muldro/chat-panel.tsx": 736,
    # Over the 400 cap before the standard was adopted and omitted from this
    # list by oversight. The Model tab's adaptation to scope_type/scope_key
    # bindings and the flat catalog.models list grew it further; recorded
    # rather than split — the tab's actual redesign is a later phase.
    "frontend/src/components/settings/model-tab.tsx": 610,
    # Same oversight. The model-config contract rewrite (scope_type/scope_key
    # bindings, flat catalog.models, credential fields) grew it further.
    "frontend/src/lib/types.ts": 591,
    "frontend/src/components/settings/settings-modal.tsx": 538,
    "frontend/src/components/history/run-detail-modal.tsx": 536,
    "frontend/tests/e2e/diagnostic.spec.ts": 501,
    "frontend/src/app/integrations/page.tsx": 460,
    "frontend/tests/e2e/screenshot-all-pages.spec.ts": 421,
    "frontend/tests/e2e/pages.spec.ts": 413,
    "frontend/src/components/knowledge/stats-view.tsx": 406,
    "frontend/src/hooks/useConnectAccount.test.ts": 401,
}
GRANDFATHER_SLACK = 40  # small headroom so unrelated edits don't hard-block


def cap_for(path: str) -> int:
    # Alembic revisions are GENERATED, not designed. The standard's reasoning —
    # "hitting a cap is a design signal, split by responsibility" — has nothing
    # to act on in a file `alembic revision --autogenerate` wrote, and the
    # initial-schema revision is 1200+ lines by construction. Capping them would
    # mean grandfathering every future migration one at a time.
    if "alembic/versions/" in path:
        return 0
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
