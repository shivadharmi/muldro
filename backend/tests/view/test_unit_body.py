"""The stored body is one string, and the row holds no second projection of it.

There is no `claim` column, and no `lede`/`summary`/`preview` either: the lede
IS paragraph 1 of `body`. A stored lede is a second projection of one string,
free to drift from the body it summarises — the "same sentence at two lengths"
defect this rebuild removed, reintroduced as a schema column.

Nothing expires. A body is superseded when a new event lands under the same
key and the prose stops describing the thing — never by a clock — so there is
no `expires_at` column.
"""

import pytest
from sqlalchemy import ARRAY, Text

from src.models.ids import ID_PREFIXES
from src.models.unit_body import UnitBody


def test_the_table_is_unit_bodies():
    assert UnitBody.__tablename__ == "unit_bodies"


@pytest.mark.parametrize("forbidden", ["claim", "lede", "summary", "preview", "expires_at"])
def test_the_row_carries_no_second_projection_of_the_body(forbidden):
    assert forbidden not in UnitBody.__table__.columns


def test_the_row_carries_the_five_fields_a_body_needs():
    columns = set(UnitBody.__table__.columns.keys())
    assert {"workspace_id", "frame_key", "body", "event_ids", "as_of"} <= columns


def test_identity_is_one_body_per_thing_per_workspace():
    uniques = [
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in UnitBody.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("frame_key", "workspace_id") in uniques


def test_event_ids_is_a_list_of_text_not_a_json_blob():
    """It is queried and compared as a set; ARRAY is the repo's list[str] type."""
    assert isinstance(UnitBody.__table__.columns["event_ids"].type, ARRAY)


def test_frame_key_is_text_because_it_embeds_an_unbounded_external_entity_id():
    assert isinstance(UnitBody.__table__.columns["frame_key"].type, Text)


def test_the_id_prefix_is_registered():
    assert ID_PREFIXES["ubody"] == "unit_body"
