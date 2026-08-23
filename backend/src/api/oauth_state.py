"""Single-use CSRF state for the native OAuth connect flow.

The `state` parameter carried the raw `user_id`. It is echoed back verbatim by
the provider and the callback trusted it as the identity to store the token
under — so anyone who could guess or observe a user id could complete an OAuth
dance and have the resulting credential filed against that account. User ids are
ULID-prefixed and appear in logs, URLs and API responses; they were never a
secret, and `state` is the one field in the flow that has to be one.

What replaces it is the ordinary construction: an unguessable random token
minted at authorize time, bound server-side to the user who asked for it, and
CONSUMED on first use. Single-use matters as much as unguessability — a state
that survives its callback can be replayed, and a replayed callback re-files a
credential the founder already saw succeed.

Redis holds the binding rather than a signed self-describing token, because a
signature can prove "Muldro issued this" but cannot prove "this has not been
used before" without storage anyway. Redis is already required infrastructure.

FAIL CLOSED, deliberately: if the binding cannot be written, authorize raises
rather than falling back to an unprotected state. A connect flow that quietly
degrades to the vulnerable shape whenever Redis blips is the same defect with a
narrower window, and the window is not visible to anyone.
"""

from __future__ import annotations

import logging
import secrets

logger = logging.getLogger(__name__)

# Long enough that guessing is hopeless, short enough to sit in a URL.
_STATE_BYTES = 32

# An OAuth round trip is a few browser redirects and a consent screen. Ten
# minutes is generous for that and short enough that an abandoned authorize
# leaves nothing usable behind.
STATE_TTL_SECONDS = 600

_KEY_PREFIX = "oauth_state:"


def _key(state: str) -> str:
    return f"{_KEY_PREFIX}{state}"


async def issue_state(redis, user_id: str) -> str:
    """Mint a state token bound to ``user_id``. Raises if it cannot be stored."""
    if not user_id:
        raise ValueError("cannot issue an OAuth state with no user")
    if redis is None:
        raise RuntimeError("OAuth state requires Redis; refusing to issue an unbound state")

    state = secrets.token_urlsafe(_STATE_BYTES)
    # NX so a token can never overwrite an existing binding. With 32 random
    # bytes a collision is not a real prospect, but silently rebinding one if it
    # happened would hand the second caller the first caller's slot.
    stored = await redis.set(_key(state), user_id, ex=STATE_TTL_SECONDS, nx=True)
    if not stored:
        raise RuntimeError("failed to store OAuth state")
    return state


async def consume_state(redis, state: str) -> str | None:
    """Return the bound user id and invalidate the token. None if unusable.

    None covers every way a state can fail to name a live authorization —
    absent, expired, forged, or already spent — and the caller must not
    distinguish them: telling a caller "that state existed but is spent" is a
    probing oracle.
    """
    if not state or redis is None:
        return None
    try:
        # GETDEL is what makes this single-use, and it is atomic: a read then a
        # separate delete lets two concurrent callbacks both observe the token
        # before either removes it, which is exactly the replay this prevents.
        user_id = await redis.getdel(_key(state))
    except AttributeError:
        # redis-py exposes getdel from 4.x; older clients fall back to a
        # pipeline, which is atomic for the same reason.
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.get(_key(state))
                pipe.delete(_key(state))
                user_id, _ = await pipe.execute()
        except Exception:
            logger.warning("OAuth state lookup failed", exc_info=True)
            return None
    except Exception:
        logger.warning("OAuth state lookup failed", exc_info=True)
        return None

    if not user_id:
        return None
    return user_id.decode() if isinstance(user_id, bytes) else str(user_id)
