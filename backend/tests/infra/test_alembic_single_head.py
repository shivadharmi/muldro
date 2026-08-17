"""Alembic must always resolve to exactly one head.

Two migrations that declare the same ``down_revision`` create two heads. That is
invisible to every other test — each branch's suite passes on its own — but
``alembic upgrade head`` then aborts with ``CommandError: Multiple head revisions
are present``, and that is the command ``infra/scripts/deploy.sh`` runs on every
deploy. Nothing else in CI invokes Alembic, so the break surfaces at deploy time,
long after the merge that caused it.

This guard makes the collision fail at merge time instead. It is a pure structural
check over the migration files — no database, no network.

When it fails, the fix is to re-parent the newer migration onto the other branch's
head (edit its ``down_revision``), or to add an explicit merge revision via
``alembic merge -m "..." <rev1> <rev2>``.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]


def _script_directory() -> ScriptDirectory:
    """Load the migration graph from an absolute path, independent of cwd."""
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_exactly_one_alembic_head():
    script = _script_directory()
    heads = script.get_heads()

    if len(heads) != 1:
        detail = "\n".join(
            f"  {rev}  (down_revision={script.get_revision(rev).down_revision!r})"
            f"  {Path(script.get_revision(rev).path).name}"
            for rev in sorted(heads)
        )
        raise AssertionError(
            f"Alembic has {len(heads)} heads; `alembic upgrade head` will abort on deploy.\n"
            f"Heads:\n{detail}\n"
            "Re-parent the newer migration's down_revision onto the other head, "
            "or create an explicit merge revision."
        )
