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

# path -> line count measured against THIS tree. Files may not exceed this.
#
# GRANDFATHER_SLACK is added on top of every number below, so a file recorded at
# its current size may still grow by that much before this reports. A record is
# a ceiling with headroom, not a freeze.
#
# Re-measured when the view-layer and model-settings branches were merged. Both
# had regenerated this list independently, against trees that no longer exist —
# so neither set of numbers described the file that now exists, and taking
# either side wholesale would have recorded fiction. Every entry below was
# measured against the merged tree. Where a file had SHRUNK the smaller number
# is recorded, because the record is a debt to pay down, not a budget to spend.
#
# `settings-modal.tsx` left this list entirely: the model-settings redesign
# split it from 538 lines to 369, under the 400 cap, so it needs no exemption.
# That is what an entry leaving looks like, and it is the point of the list.
GRANDFATHERED: dict[str, int] = {
    "backend/src/orchestrator/agent_invoker.py": 1620,
    "backend/tests/deep_runtime/test_permission_gate.py": 1161,
    "frontend/src/lib/api.ts": 1170,
    "backend/tests/test_scheduler.py": 1104,
    "backend/src/services/graph_executor.py": 1014,
    "backend/src/services/dag_runner.py": 961,
    "backend/tests/test_push_receiver.py": 924,
    "backend/tests/test_knowledge_service.py": 909,
    "backend/tests/test_autonomous_deep_e2e.py": 907,
    "backend/tests/deep_runtime/test_trust_gate.py": 894,
    "backend/tests/test_perception_policy.py": 889,
    "backend/tests/test_foundation_hardening.py": 885,
    "backend/src/orchestrator/perception_runner.py": 858,
    "backend/src/api/routes_approvals.py": 843,
    "backend/src/orchestrator/muldro.py": 828,
    "backend/tests/test_perception.py": 815,
    "backend/tests/test_chat_single_lead.py": 809,
    "frontend/src/components/muldro/chat-panel.tsx": 724,
    "frontend/src/lib/types.ts": 623,
    "frontend/src/components/history/run-detail-modal.tsx": 536,
    "frontend/tests/e2e/diagnostic.spec.ts": 501,
    "frontend/src/app/integrations/page.tsx": 460,
    "backend/src/integrations/mcp_pool.py": 445,
    "frontend/tests/e2e/screenshot-all-pages.spec.ts": 421,
    "frontend/tests/e2e/pages.spec.ts": 413,
    "frontend/src/components/knowledge/stats-view.tsx": 406,
    "frontend/src/hooks/useConnectAccount.test.ts": 401,
    "frontend/src/app/settings/page.tsx": 23,
    "backend/src/api/routes_auth.py": 16,
    # The one that did NOT move. Recorded 871, measured 1143 — already violating
    # even with the slack. Left at 871 deliberately: re-recording it at 1143
    # would launder a real, pre-existing violation into a permission, and this
    # file belongs to work outside this change. It should fail, and it does.
    "backend/src/integrations/session_pool.py": 871,
}
# Added on top of every recorded number above: small headroom so unrelated edits
# don't hard-block. A file recorded at its current size may still grow by this
# much, which is why "recorded at its current size" is a baseline, not a freeze.
GRANDFATHER_SLACK = 40


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
    # A grandfather key that resolves to nothing is not a lenient cap — it is NO
    # cap, silently. `chat-panel.tsx` carried a key naming a directory that never
    # existed and grew unchecked for months because `.get()` simply returned
    # None. Checked here rather than trusted, so a mistyped or stale path reports
    # itself instead of disappearing.
    for key in GRANDFATHERED:
        if not Path(key).exists():
            failures.append(
                f"{key}: grandfathered path no longer exists — remove or re-point it"
            )
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
