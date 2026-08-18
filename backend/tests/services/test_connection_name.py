"""Pure tests for mint_connection_name (no DB — this logic is DB-free)."""

import re

from ulid import ULID

from src.services.connection_service import mint_connection_name

# OC v1.3.5 rule: charset [A-Za-z0-9_-], start with letter/digit, <= 64 chars.
_OC_VALID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def test_mint_is_oc_valid_and_20_hex():
    name = mint_connection_name("ws1", "usrA", "gmail", "work")
    assert _OC_VALID.fullmatch(name), name
    assert len(name) == 20
    assert re.fullmatch(r"[0-9a-f]{20}", name)  # blake2b hexdigest, digest_size=10


def test_mint_is_deterministic():
    a = mint_connection_name("ws1", "usrA", "gmail", "work")
    b = mint_connection_name("ws1", "usrA", "gmail", "work")
    assert a == b


def test_mint_distinguishes_every_tuple_field():
    base = mint_connection_name("ws1", "usrA", "gmail", "work")
    assert mint_connection_name("ws2", "usrA", "gmail", "work") != base
    assert mint_connection_name("ws1", "usrB", "gmail", "work") != base
    assert mint_connection_name("ws1", "usrA", "googlecalendar", "work") != base
    assert mint_connection_name("ws1", "usrA", "gmail", "home") != base


def test_mint_fits_real_ulids_under_64():
    name = mint_connection_name(f"ws_{ULID()}", f"usr_{ULID()}", "gmail", "work")
    assert len(name) <= 64
