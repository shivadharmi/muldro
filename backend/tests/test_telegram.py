from src.interface.telegram import _split_message


class TestSplitMessage:
    def test_short_message_returns_single_chunk(self):
        assert _split_message("hello") == ["hello"]

    def test_splits_on_paragraph_boundary(self):
        text = "A" * 3000 + "\n\n" + "B" * 3000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2
        assert chunks[0] == "A" * 3000
        assert chunks[1] == "B" * 3000

    def test_splits_on_newline_when_no_paragraph(self):
        text = "A" * 3000 + "\n" + "B" * 3000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2

    def test_hard_split_when_no_newlines(self):
        text = "A" * 8000
        chunks = _split_message(text, limit=4000)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4000
        assert len(chunks[1]) == 4000
