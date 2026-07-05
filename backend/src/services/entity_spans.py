"""Candidate entity-mention span extraction for the world-model resolver.

Deterministic and dependency-free — this is NOT a NER model. It only needs decent
RECALL of candidate mentions; the resolver's exact/FTS/vector signals do the
precise resolution. Extracts: the whole text when it is short/name-like (<=3
tokens, e.g. a clean name or an actor email passed by non-chat callers), email
addresses, @handles, quoted phrases, and capitalized token runs (proper-noun-ish),
filtering common sentence-starter words out of single-word spans.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HANDLE = re.compile(r"(?<!\w)@\w{2,}")
_QUOTED = re.compile(r"[\"']([^\"']{2,64})[\"']")
# Two-or-more capitalized words in a row (strong proper-noun signal).
_CAP_RUN = re.compile(r"\b[A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)+\b")
# A single capitalized token (weaker — filtered by _STOP below).
_CAP_WORD = re.compile(r"\b[A-Z][\w&'-]+\b")

_STOP = {
    "the",
    "a",
    "an",
    "i",
    "my",
    "our",
    "your",
    "this",
    "that",
    "it",
    "we",
    "you",
    "he",
    "she",
    "they",
    "please",
    "can",
    "could",
    "would",
    "should",
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank",
}


def extract_spans(text: str, *, max_spans: int = 12) -> list[str]:
    """Return de-duplicated candidate mention spans (case-insensitive dedup,
    order-preserving, capped at max_spans)."""
    if not text or not text.strip():
        return []
    text = text.strip()
    spans: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        s = raw.strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            spans.append(s)

    # Whole text when it is short / name-like (clean-name and email callers).
    if len(text.split()) <= 3:
        _add(text)
    for m in _EMAIL.findall(text):
        _add(m)
    for m in _HANDLE.findall(text):
        _add(m)
    for m in _QUOTED.findall(text):
        _add(m)
    for m in _CAP_RUN.findall(text):
        _add(m)
    for m in _CAP_WORD.findall(text):
        if m.lower() not in _STOP:
            _add(m)

    return spans[:max_spans]
