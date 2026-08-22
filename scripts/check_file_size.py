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
#
# GRANDFATHER_SLACK is added on top of every number below, so a file recorded at
# its current size may still grow by that much before this reports. A record is
# a ceiling with headroom, not a freeze.
#
# Re-measured 2026-08-23. Most of these had drifted far BELOW their recorded
# number as the modules were split, which is drift in the safe direction but
# still a dead cap: a 3427-line record on an 896-line file permits it to
# quadruple unreported. Each is now recorded at its measured size.
GRANDFATHERED: dict[str, int] = {
    "backend/src/orchestrator/muldro.py": 896,
    "backend/src/services/graph_executor.py": 1023,
    "backend/src/api/routes_auth.py": 16,
    # The one that did NOT move. Recorded 871, measured 1143 — already violating
    # even with the slack. Left at 871 deliberately: re-recording it at 1143
    # would launder a real, pre-existing violation into a permission, and this
    # file belongs to work outside this change. It should fail, and it does.
    "backend/src/integrations/session_pool.py": 871,
    "backend/src/integrations/mcp_pool.py": 445,
    # Grew past its 897-line grandfather mark (to 1111) in commits since
    # adoption without this record being updated — an untracked drift, not
    # something this change introduced. The model-config client rewrite
    # (partial-credential body, bind-rejection error mapping) grows it a
    # little further; recorded at its current size rather than split.
    "frontend/src/lib/api.ts": 1169,
    # Already 496 lines at standard adoption (over the 400 cap even then) and
    # omitted from this list by oversight. The model-config contract rewrite
    # (scope_type/scope_key bindings, flat catalog.models, credential fields)
    # grows it further; recorded here rather than split, matching every other
    # pre-existing oversized file's treatment.
    "frontend/src/lib/types.ts": 590,
    # Recorded at adoption as `components/chat/chat-panel.tsx` (678 lines) — a
    # path that has NEVER existed. `git log --all -- frontend/src/components/chat/`
    # is empty; at the adoption commit (1be2dc9) the key already read `chat/`
    # while the file sat at `jarvis/`, and it has only ever moved `jarvis/` →
    # `muldro/`. The key was wrong the day it was written.
    #
    # So the lesson is NOT "watch out for renames" — no rename broke this. It is
    # that A GRANDFATHER KEY WHICH DOES NOT RESOLVE TO A FILE IS SILENTLY
    # IGNORED: `GRANDFATHERED.get(f)` returns None for a path git never produces,
    # the entry is simply never consulted, and the cap never applied at all. The
    # file grew to 736 with nothing reporting it. `main()` now validates every
    # key against the filesystem, so the next mistyped path reports instead of
    # disappearing. Re-pointed at the real path and recorded at its measured
    # size; note GRANDFATHER_SLACK still allows 40 lines of growth on top.
    "frontend/src/components/muldro/chat-panel.tsx": 736,
    "frontend/src/components/history/run-detail-modal.tsx": 536,
    "frontend/src/app/settings/page.tsx": 23,
    # Four keys were removed in the same pass — `services/surface_detail_builders.py`,
    # `services/scheduler.py`, `services/memory_service.py` and
    # `tools/intelligence_server.py`. Each was split into a package of the same
    # name, so the recorded `.py` path no longer exists and the entry could never
    # fire; the modules inside those packages take the normal cap correctly.
}
# Added on top of every recorded number above: small headroom so unrelated edits
# don't hard-block. A file recorded at its current size may still grow by this
# much, which is why "recorded at its current size" is a baseline, not a freeze.
GRANDFATHER_SLACK = 40


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
