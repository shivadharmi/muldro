from src.services.provenance import SourceRef, merge_source_refs


def test_sourceref_requires_source_omits_none():
    ref = SourceRef(source="gmail", event_id="evt_1")
    assert ref.to_dict() == {"source": "gmail", "event_id": "evt_1"}  # run_id omitted (None)


def test_sourceref_dedup_key_prefers_event_then_run_then_source():
    assert SourceRef(source="gmail", event_id="evt_1").dedup_key() == "evt_1"
    assert SourceRef(source="outcome", run_id="run_1").dedup_key() == "run_1"
    assert SourceRef(source="interaction").dedup_key() == "interaction"


def test_merge_appends_and_dedups_by_key():
    existing = [{"source": "gmail", "event_id": "evt_1"}]
    out = merge_source_refs(existing, SourceRef(source="gmail", event_id="evt_2"))
    assert out == [
        {"source": "gmail", "event_id": "evt_1"},
        {"source": "gmail", "event_id": "evt_2"},
    ]


def test_merge_replaces_same_key_moving_to_end():
    existing = [
        {"source": "gmail", "event_id": "evt_1"},
        {"source": "gmail", "event_id": "evt_2"},
    ]
    out = merge_source_refs(existing, SourceRef(source="gmail", event_id="evt_1"))
    assert out == [
        {"source": "gmail", "event_id": "evt_2"},
        {"source": "gmail", "event_id": "evt_1"},
    ]  # evt_1 deduped, re-appended most-recent


def test_merge_caps_to_most_recent():
    existing = [{"source": "s", "event_id": f"evt_{i}"} for i in range(20)]
    out = merge_source_refs(existing, SourceRef(source="s", event_id="evt_new"), cap=20)
    assert len(out) == 20
    assert out[-1] == {"source": "s", "event_id": "evt_new"}
    assert {"source": "s", "event_id": "evt_0"} not in out  # oldest dropped


def test_merge_handles_none_existing():
    out = merge_source_refs(None, SourceRef(source="slack", event_id="evt_x"))
    assert out == [{"source": "slack", "event_id": "evt_x"}]
