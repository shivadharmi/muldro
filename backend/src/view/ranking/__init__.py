"""Derived, typed features for the view-layer ranker.

`docs/view-layer/ranker-interface.md` §0: the ranker reads only values muldro
computed about its own history — never a value an outside party wrote, and
never a value a model inferred *from* what an outside party wrote.

This package holds the extractors that make that possible.
"""
