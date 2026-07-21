import asyncio
import datetime
from unittest.mock import AsyncMock, patch

from src.services.provenance import SourceRef


def test_record_attribute_facts_passes_source_ref():
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch(
        "src.services.entity_facts.store.EntityFactStore.record_fact",
        new=AsyncMock(return_value=("fact_1", False)),
    ) as rec:
        asyncio.run(
            wm._record_attribute_facts(
                "ent_1",
                "user_1",
                "ws_1",
                {"role": "investor"},
                "perception",
                datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc),
                source_ref=SourceRef(source="gmail", event_id="evt_9"),
            )
        )
    assert rec.await_count == 1
    assert rec.call_args.kwargs["source_ref"] == {"source": "gmail", "event_id": "evt_9"}


def test_record_attribute_facts_none_source_ref_passes_none():
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch(
        "src.services.entity_facts.store.EntityFactStore.record_fact",
        new=AsyncMock(return_value=("fact_1", False)),
    ) as rec:
        asyncio.run(
            wm._record_attribute_facts(
                "ent_1",
                "user_1",
                "ws_1",
                {"role": "investor"},
                "perception",
                datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc),
            )
        )
    assert rec.call_args.kwargs["source_ref"] is None
