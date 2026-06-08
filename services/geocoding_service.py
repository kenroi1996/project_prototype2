"""
services/geocoding_service.py
──────────────────────────────
Wraps geopy Nominatim with a PostgreSQL cache (geo_cache table).

Rules
─────
• Single geolocator instance created at module level (fix #4).
• Always check cache before hitting Nominatim (fix #6 resume support).
• Enforce 1.1 s delay AFTER every request attempt, success or failure (fix #1).
• Retry up to 3 times with exponential backoff on rate-limit / connection
  errors: 2 s → 5 s → 10 s (fix #2).
• Unique user_agent "student_risk_app_v1" (fix #3).
• No Qt imports — pure logic only.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from geopy.exc import GeocoderRateLimited, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from db import get_session
from models.geo_cache import GeoCache


# ── Fix #3 & #4 — unique user_agent, single module-level instance ────────────
_geolocator = Nominatim(
    user_agent="student_risk_app_v1",
    timeout=10,
)

# Fix #1 — minimum gap between every Nominatim call (seconds)
_RATE_LIMIT_SECONDS: float = 1.1

# Fix #2 — retry delays in seconds (attempt 1, 2, 3)
_RETRY_DELAYS: tuple[int, ...] = (2, 5, 10)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise(address: str) -> str:
    """Lowercase, collapse whitespace — produces the cache key."""
    return re.sub(r"\s+", " ", address.lower().strip())


def _call_nominatim(address: str):
    """
    Call Nominatim once, then sleep 1.1 s regardless of outcome.

    Returns the Location object or None.
    Raises GeocoderRateLimited / GeocoderUnavailable / GeocoderTimedOut
    so the caller can decide whether to retry.
    """
    try:
        return _geolocator.geocode(address)
    finally:
        # Fix #1 — sleep AFTER every attempt, including failures
        time.sleep(_RATE_LIMIT_SECONDS)


def _call_with_backoff(address: str):
    """
    Call Nominatim with up to 3 retries and exponential backoff (fix #2).

    Returns (location_or_None, succeeded: bool).
    On total failure returns (None, False) — never raises.
    """
    last_exc: Optional[Exception] = None

    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            location = _call_nominatim(address)
            return location, True
        except (GeocoderRateLimited, GeocoderUnavailable, GeocoderTimedOut) as exc:
            last_exc = exc
            print(
                f"[GeocodingService] Attempt {attempt}/3 failed for "
                f"{address!r}: {exc}. "
                f"Waiting {delay} s before retry…"
            )
            time.sleep(delay)
        except Exception as exc:
            # Unexpected error — don't retry, fail fast
            print(f"[GeocodingService] Unexpected error for {address!r}: {exc}")
            return None, False

    print(
        f"[GeocodingService] All retries exhausted for {address!r}. "
        f"Last error: {last_exc}"
    )
    return None, False


# ── Public API ────────────────────────────────────────────────────────────────

class GeocodingService:
    """Geocodes student home addresses with a persistent PostgreSQL cache."""

    @staticmethod
    def geocode(raw_address: str) -> dict:
        """
        Resolve *raw_address* to (latitude, longitude).

        Checks the cache first.  On a cache miss calls Nominatim with
        rate-limiting and exponential-backoff retry.

        Returns
        -------
        dict with keys:
            address_key, latitude, longitude, display_name, success, from_cache
        """
        key = _normalise(raw_address)

        # ── Fix #6 — cache hit: return immediately, no Nominatim call ─────────
        with get_session() as session:
            cached: Optional[GeoCache] = session.get(GeoCache, key)
            if cached is not None:
                return {
                    "address_key":  cached.address_key,
                    "latitude":     cached.latitude,
                    "longitude":    cached.longitude,
                    "display_name": cached.display_name,
                    "success":      cached.success,
                    "from_cache":   True,
                }

        # ── Cache miss — call Nominatim with backoff ──────────────────────────
        location, api_succeeded = _call_with_backoff(raw_address)

        lat = lng = display = None
        success = False

        if api_succeeded and location:
            lat     = location.latitude
            lng     = location.longitude
            display = location.address
            success = True

        # Persist result (including failures) so we never retry known-bad addresses
        entry = GeoCache(
            address_key  = key,
            raw_address  = raw_address,
            latitude     = lat,
            longitude    = lng,
            display_name = display,
            success      = success,
        )
        with get_session() as session:
            session.merge(entry)   # upsert — safe against race conditions

        return {
            "address_key":  key,
            "latitude":     lat,
            "longitude":    lng,
            "display_name": display,
            "success":      success,
            "from_cache":   False,
        }

    @staticmethod
    def geocode_batch(
        addresses: list[str],
        progress_cb=None,
    ) -> list[dict]:
        """
        Geocode a list of addresses sequentially.

        Fix #6 — addresses with a successful cache entry are skipped
        (not passed to Nominatim) so interrupted jobs resume cleanly.

        Parameters
        ----------
        addresses   : raw address strings
        progress_cb : optional callable(current: int, total: int)

        Returns
        -------
        list of result dicts (same structure as geocode())
        """
        results: list[dict] = []
        total = len(addresses)

        for i, addr in enumerate(addresses):
            key = _normalise(addr)

            # Fix #6 — check cache before every call in the batch
            with get_session() as session:
                cached: Optional[GeoCache] = session.get(GeoCache, key)

            if cached is not None and cached.success:
                # Already successfully geocoded — skip Nominatim entirely
                results.append({
                    "address_key":  cached.address_key,
                    "latitude":     cached.latitude,
                    "longitude":    cached.longitude,
                    "display_name": cached.display_name,
                    "success":      True,
                    "from_cache":   True,
                })
            else:
                results.append(GeocodingService.geocode(addr))

            if progress_cb:
                progress_cb(i + 1, total)

        return results

    @staticmethod
    def get_cached(address_key: str) -> Optional[GeoCache]:
        """Return a GeoCache row or None."""
        with get_session() as session:
            return session.get(GeoCache, address_key)

    @staticmethod
    def clear_cache() -> int:
        """Delete all cache entries. Returns rows deleted."""
        with get_session() as session:
            count = session.query(GeoCache).delete()
        return count
