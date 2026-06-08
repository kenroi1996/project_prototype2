"""
models/geo_cache.py
───────────────────
Caches geocoding results so Nominatim is not called repeatedly for the same
address.  Keyed on the normalised address string.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class GeoCache(Base):
    __tablename__ = "geo_cache"
    __table_args__ = {"schema": "public", "extend_existing": True}

    # ── Primary key ───────────────────────────────────────────────────────────
    # Use the normalised address as the natural PK to make cache lookups O(1)
    address_key: Mapped[str] = mapped_column(
        String(500), primary_key=True, nullable=False
    )

    # ── Raw address as submitted ───────────────────────────────────────────────
    raw_address: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Geocoding result ──────────────────────────────────────────────────────
    latitude:    Mapped[Optional[float]] = mapped_column(Float)
    longitude:   Mapped[Optional[float]] = mapped_column(Float)
    display_name: Mapped[Optional[str]]  = mapped_column(Text)

    # ── Whether geocoding succeeded ───────────────────────────────────────────
    success: Mapped[bool] = mapped_column(default=False)

    # ── Audit ─────────────────────────────────────────────────────────────────
    geocoded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<GeoCache address_key={self.address_key!r} "
            f"lat={self.latitude} lng={self.longitude}>"
        )
