"""Deterministic deadline extraction. Text in, date out, no model in the loop.

Why this is a parser and not a prompt
-------------------------------------
A deadline is admitted into the ranker's feature set on one argument:
*an attacker can lie about **when**; they cannot inject an **instruction**.*
Extraction is still a boundary — a deadline lifted from a body is
attacker-influenced — but it enters as a **typed date, which is bounded and
checkable**.

That argument holds only for a genuinely parsed date. What the codebase holds
today is `NormalizedEvent.importance_signals["contains_deadline"]`: a boolean
an LLM asserted while reading the attacker's subject and body. That is the
instruction channel wearing a typed name, and this module replaces it.

So there is no model here and no prompt to inject — only a parser to feed. And
there is no clock: `now` is a required keyword argument, because a relative
expression needs a reference point and a function that reads the wall clock
cannot be tested deterministically.

What the checkable half buys
----------------------------
A typed date can be sanity-checked, so this module checks it:

* **The past is not a deadline.** A candidate before `now`'s date is dropped.
  Same-day is kept — a message that says "today" arrived today.
* **The far future is not a deadline either.** `HORIZON_DAYS` bounds it, so
  `by 3025` cannot produce a rank-affecting value.
* **Nothing raises.** This runs over attacker-controlled text on a live
  perception path. Every failure — bad type, garbage, impossible calendar date,
  adversarial padding — returns `None`. A raise here would drop the founder's
  card entirely, which is the worse outcome (the same reasoning as
  `frame.py`'s neutralize-don't-refuse rule).

Ambiguity: the earliest plausible date wins
-------------------------------------------
When the text names several dates, this returns the **earliest** one still in
the plausible window. That is the safest reading for the founder — a draft due
Friday and a final due the 20th is a Friday problem — and it is also the
reading an attacker would target, by writing `by tomorrow` into a body that has
nothing due at all.

**That influence is expected and accepted, not an oversight.** A deadline is
one bounded feature among several on `RankFeatures`, and `rank_with_model`'s
`max_displacement` clamps how far any single item may move from its
deterministic position. A maximally
successful lie about *when* moves an item a few places; it cannot move it to
the top, cannot add an item, and cannot say anything to the ranker at all.
The blast radius is bounded by construction rather than by detection.

Regex discipline
----------------
Every alternative in every pattern below is a **literal** or a single bounded
class, and every quantifier is bounded (`\\s{1,3}`, `\\d{1,3}`). There is no
nested repetition, so there is no catastrophic backtracking to trigger with a
long adversarial string. `MAX_SCAN_CHARS` bounds the total work on top of that,
and is the *only* such bound: a per-pattern match cap was tried and removed,
because it bought ~0.03s on the worst case a 20k window can hold while
introducing a correctness cliff — an earliest date appearing after the cap
would have been silently discarded.

Division of labour with `python-dateutil`
-----------------------------------------
`dateutil` converts month **names**; plain `date()` validates all-numeric
fields (where the only open question is field order, which `dayfirst` cannot
answer better than the explicit rule below); `relativedelta` does the month
arithmetic for `next month` / `in N months`, which is the part worth not
hand-rolling.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Iterator

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

__all__ = ["HORIZON_DAYS", "MAX_SCAN_CHARS", "extract_deadline"]


# A deadline in the feed is operational: a reply, a filing, a renewal, a board
# packet. One year covers the longest of those (annual filings and renewals)
# with room to spare. Past it, a date-shaped token is far more likely to be a
# misparse — a version string, a reference number, a copyright year in a
# footer — than a commitment the founder holds. Beyond the horizon the value is
# discarded outright rather than clamped, because a clamped date would assert a
# deadline the text never named.
HORIZON_DAYS = 365

# Bound the work regardless of how much text a sender sends. A deadline that
# appears only after 20k characters is not one a human reader would find either.
MAX_SCAN_CHARS = 20_000


def _alternation(words: Iterable[str]) -> str:
    """Longest-first alternation of literals — no prefix shadows a longer name."""
    return "|".join(sorted(set(words), key=len, reverse=True))


_MONTH_WORDS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
)

# Full weekday names only. The three-letter abbreviations (`mon`, `wed`, `sat`,
# `sun`) collide with ordinary prose and proper nouns, and a false positive here
# invents a deadline inside the next seven days — the most rank-affecting range
# there is. Declining to parse `Wed` costs nothing by comparison.
_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTH_ALT = _alternation(_MONTH_WORDS)
_WEEKDAY_ALT = _alternation(_WEEKDAY_INDEX)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

# The four-digit year is REQUIRED, which costs the real `due 3/15` phrasing and
# is still the right trade. A bare `N/M` is one of the commonest shapes in
# ordinary mail that is not a date at all — `Rated 4/5 stars`, `at 3/4
# capacity`, `open 24/7` (which even survives the day-first swap as July 24) —
# and marketing mail is exactly the volume this feed carries. Each of those
# would mint a plausible near-future deadline out of nothing.
#
# A two-digit year is not accepted either: resolving `26` to a century requires
# a reference year, and dateutil resolves it against the *system* clock, which
# would make this function impure.
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_MONTH_FIRST = re.compile(
    rf"\b({_MONTH_ALT})\.?\s{{1,3}}(\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s{{1,3}}(\d{{4}}))?\b"
)
_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s{{1,3}}(?:of\s{{1,3}})?({_MONTH_ALT})\.?"
    rf"(?:,?\s{{1,3}}(\d{{4}}))?\b"
)

_TODAY = re.compile(r"\btoday\b")
_TOMORROW = re.compile(r"\btomorrow\b")

# `EOD` and `COB` name a time of day, not a day. They mean *today* only when
# nothing else in the text names a day: `by EOD` is today, but `EOD Thursday`
# and `Wednesday EOD` both mean that weekday. Treating them as a **fallback**
# consulted only after every other candidate has been collected and filtered
# handles both word orders, which a lookahead cannot — Python has no
# variable-width lookbehind, so the trailing form would have stayed broken.
# Since the earliest candidate wins, a stray `today` from a bare `EOD` reading
# would otherwise beat the weekday every single time.
_END_OF_DAY = re.compile(r"\b(?:eod|cob|end\s{1,3}of\s{1,3}day|close\s{1,3}of\s{1,3}business)\b")

_WEEKDAY = re.compile(rf"\b(?:(next)\s{{1,3}})?({_WEEKDAY_ALT})\b")
_IN_N = re.compile(r"\bin\s{1,3}(\d{1,3})\s{1,3}(days|day|weeks|week|months|month)\b")
_NEXT_PERIOD = re.compile(r"\bnext\s{1,3}(week|month)\b")


def _from_month_name(month: str, day: str, year: str | None, today: date) -> date | None:
    """dateutil converts the name; the fields arrive already isolated by regex.

    Reassembling a canonical `"<month> <day> <year>"` token keeps dateutil's
    input free of the ordinal suffixes and `of` that the surrounding prose
    carries, so no fuzzy parsing is ever needed.
    """
    if year is not None:
        parsed = dateutil_parser.parse(f"{month} {day} {year}", default=datetime(int(year), 1, 1))
        return parsed.date()

    for candidate_year in (today.year, today.year + 1):
        try:
            parsed = dateutil_parser.parse(
                f"{month} {day} {candidate_year}", default=datetime(candidate_year, 1, 1)
            ).date()
        except (ValueError, OverflowError):
            continue
        if parsed >= today:
            return parsed
    return None


def _iso(match: re.Match[str], today: date) -> date:
    year, month, day = (int(group) for group in match.groups())
    return date(year, month, day)


def _slash(match: re.Match[str], today: date) -> date:
    first, second, year = (int(group) for group in match.groups())
    # Month-first (US convention) unless the first field cannot be a month, in
    # which case day-first is the only reading available — a deduction, not a
    # guess. A genuinely ambiguous `4/3/2026` stays month-first.
    month, day = (second, first) if first > 12 >= second else (first, second)
    return date(year, month, day)


def _month_first(match: re.Match[str], today: date) -> date | None:
    month, day, year = match.groups()
    return _from_month_name(month, day, year, today)


def _day_first(match: re.Match[str], today: date) -> date | None:
    day, month, year = match.groups()
    return _from_month_name(month, day, year, today)


def _weekday(match: re.Match[str], today: date) -> date:
    days_ahead = (_WEEKDAY_INDEX[match.group(2)] - today.weekday()) % 7
    # A bare weekday names its soonest occurrence, today included: "by Sunday"
    # sent on a Sunday means today. "next Sunday" skips the occurrence that
    # would otherwise be today.
    if match.group(1) is not None and days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _in_n(match: re.Match[str], today: date) -> date:
    amount, unit = int(match.group(1)), match.group(2)
    if unit.startswith("day"):
        return today + timedelta(days=amount)
    if unit.startswith("week"):
        return today + timedelta(weeks=amount)
    return today + relativedelta(months=amount)


def _next_period(match: re.Match[str], today: date) -> date:
    if match.group(1) == "week":
        return today + timedelta(days=7)
    return today + relativedelta(months=1)


# (pattern, converter). Order is irrelevant: the earliest surviving candidate
# wins regardless of which pattern produced it. Every converter takes `today`
# so the table can stay uniform, even where an absolute date ignores it.
_Converter = Callable[[re.Match[str], date], date | None]

_MATCHERS: tuple[tuple[re.Pattern[str], _Converter], ...] = (
    (_ISO_DATE, _iso),
    (_SLASH_DATE, _slash),
    (_MONTH_FIRST, _month_first),
    (_DAY_FIRST, _day_first),
    (_WEEKDAY, _weekday),
    (_IN_N, _in_n),
    (_NEXT_PERIOD, _next_period),
)


def _candidates(text: str, today: date) -> Iterator[date]:
    """Every date the text names, unfiltered.

    Each conversion runs under its **own** guard. One unparseable token —
    `Feb 30`, `13/45/2026`, or anything unanticipated — must skip that match
    only. Letting it abort the sweep would let a single hostile fragment
    suppress a real deadline elsewhere in the same message, which is a quieter
    failure than raising and a more useful one to an attacker.
    """
    if _TODAY.search(text):
        yield today
    if _TOMORROW.search(text):
        yield today + timedelta(days=1)

    for pattern, convert in _MATCHERS:
        for match in pattern.finditer(text):
            try:
                resolved = convert(match, today)
            except Exception:  # noqa: BLE001 - see docstring: skip the match, not the sweep
                continue
            if resolved is not None:
                yield resolved


def _extract(text: str, now: datetime) -> date | None:
    if not isinstance(text, str) or not text:
        return None
    today = now.date()
    window = text[:MAX_SCAN_CHARS].lower()
    horizon = today + timedelta(days=HORIZON_DAYS)

    earliest: date | None = None
    for candidate in _candidates(window, today):
        if candidate < today or candidate > horizon:
            continue
        if earliest is None or candidate < earliest:
            earliest = candidate

    # `EOD`/`COB` is a time of day, so it only names a *day* when nothing else
    # did. Consulting it here — after filtering, not before — means a text whose
    # only other date is in the past ("you promised 2026-02-01; do it by EOD")
    # still resolves to today.
    if earliest is None and _END_OF_DAY.search(window):
        return today
    return earliest


def extract_deadline(text: Any, *, now: datetime) -> date | None:
    """Return the deadline the text names, or None. Never raises.

    `now` is required: relative expressions ("by Friday", "in 3 days") resolve
    against it, and reading the wall clock instead would make this untestable.

    Returns the earliest date in `[now.date(), now.date() + HORIZON_DAYS]` that
    the text names. Anything in the past, anything past the horizon, anything
    that is not a real calendar date, and anything at all that goes wrong
    yields `None`.
    """
    try:
        return _extract(text, now)
    except Exception:  # noqa: BLE001 - totality is the contract; see module docstring
        return None
