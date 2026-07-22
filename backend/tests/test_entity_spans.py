"""Deterministic, dependency-free span extraction for entity resolution (NOT a
NER model). Pure — no DB, no network."""

from src.services.entity_spans import extract_spans


def test_empty_or_blank_returns_no_spans():
    assert extract_spans("") == []
    assert extract_spans("   ") == []


def test_extracts_capitalized_name_from_a_chatty_message():
    spans = extract_spans("please email Bob about the Q3 sync")
    assert "Bob" in spans
    assert "Q3" in spans
    # lowercase filler words are not spans
    assert "please" not in spans and "about" not in spans


def test_short_cleanish_text_is_kept_verbatim():
    # non-chat callers pass clean names / actor emails; single-name lookups must resolve
    assert extract_spans("bob@acme.com") == ["bob@acme.com"]
    assert "Acme Corp" in extract_spans("Acme Corp")


def test_multiword_proper_noun_run_and_its_tokens():
    spans = extract_spans('meet with "Project Phoenix" team next week')
    assert "Project Phoenix" in spans  # quoted phrase
    assert "Project" in spans and "Phoenix" in spans


def test_handles_and_emails():
    spans = extract_spans("ping @alice and mail carol@x.io")
    assert "@alice" in spans
    assert "carol@x.io" in spans


def test_dedup_is_case_insensitive_and_order_preserving():
    spans = extract_spans("Acme acme ACME")  # 3 tokens -> whole text kept once
    lowered = [s.lower() for s in spans]
    assert lowered.count("acme") == 1


def test_capped_at_max_spans():
    text = " ".join(f"Name{i}" for i in range(30))
    assert len(extract_spans(text, max_spans=12)) <= 12


def test_common_sentence_starters_are_not_single_word_spans():
    spans = extract_spans("The report is late")  # 4 tokens -> no whole-text span
    assert "The" not in spans
    assert "Report" not in spans  # "report" is lowercase in the text


def test_non_ascii_capitalized_name_is_extracted_midsentence():
    # recall-first: a non-ASCII capitalized first name embedded in a sentence must
    # not be silently dropped (regression for the ASCII-only [A-Z] initial).
    spans = extract_spans("please email Émile about the draft")
    assert "Émile" in spans
    # regression guard: lowercase filler still excluded
    assert "please" not in spans and "about" not in spans
