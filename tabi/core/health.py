"""Two-bar health engine (pure functions).

Body (physical) follows the hotel market for the group's plan: prices rising or
rooms disappearing hurt, the reverse heals. Mood (mental) follows the chat:
real planning progress heals, silence and dodging decay it.
"""
from __future__ import annotations

WEIGHT_AVAILABILITY = 200.0   # 5% fewer rooms  → ~10 damage
WEIGHT_PRICE_PCT = 120.0      # 8% price jump   → ~10 damage
DAMAGE_CAP = 25

MOOD_PER_MESSAGE = 1          # any chatter keeps Tabi company
MOOD_PER_PROGRESS = 10        # a plan field locked / someone commits
MOOD_DECAY_PER_HOUR = 1.5     # silence (after a grace period)
MOOD_GRACE_HOURS = 6
BODY_DRIFT_PER_DAY = 3        # without live data, an un-booked trip slowly gets pricier


def market_delta(physical: int, prev: dict, curr: dict) -> int:
    """prev/curr: {price, count}. Returns the new physical value."""
    p0, p1 = float(prev.get("price") or 0), float(curr.get("price") or 0)
    c0, c1 = float(prev.get("count") or 0), float(curr.get("count") or 0)
    d_avail = (c1 - c0) / max(1.0, c0)
    d_price = (p1 - p0) / max(1.0, p0)
    damage = WEIGHT_AVAILABILITY * max(0.0, -d_avail) + WEIGHT_PRICE_PCT * max(0.0, d_price)
    heal = WEIGHT_AVAILABILITY * max(0.0, d_avail) + WEIGHT_PRICE_PCT * max(0.0, -d_price)
    return _clamp(physical - min(damage, DAMAGE_CAP) + min(heal, DAMAGE_CAP))


def mood_after_silence(mental: int, silent_hours: float) -> int:
    over = max(0.0, silent_hours - MOOD_GRACE_HOURS)
    return _clamp(mental - min(DAMAGE_CAP, over * MOOD_DECAY_PER_HOUR))


def mood_after_activity(mental: int, messages: int, progress: int) -> int:
    return _clamp(mental + messages * MOOD_PER_MESSAGE + progress * MOOD_PER_PROGRESS)


def body_drift(physical: int, days: float) -> int:
    return _clamp(physical - min(DAMAGE_CAP, days * BODY_DRIFT_PER_DAY))


def _clamp(v: float) -> int:
    return max(0, min(100, int(round(v))))


# ─── Sprite buckets (same thresholds the original art used) ─────────────────
def body_tier(physical: int) -> str:
    return "full" if physical > 70 else "half" if physical >= 40 else "low"


def mood_tier(mental: int) -> str:
    return "happy" if mental > 70 else "meh" if mental >= 40 else "sad"


def is_rotten(mental: int) -> bool:
    return mental < 50
