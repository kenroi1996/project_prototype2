"""
models/geo_cache.py
───────────────────
SQLAlchemy ORM model for public.geo_cache — a municipality-level coordinate
lookup table used to derive Distance_KM / Distance_Bucket features without
calling an external geocoding API on every training/prediction run.

Schema (managed by geo_cache_setup.sql):
    municipality_id  serial PK
    municipality     varchar(100) UNIQUE NOT NULL
    province         varchar(100)
    latitude         numeric(10,6) NOT NULL
    longitude        numeric(10,6) NOT NULL
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class GeoCache(Base):
    __tablename__  = "geo_cache"
    __table_args__ = {"schema": "public", "extend_existing": True}

    # ── Primary key ───────────────────────────────────────────────────────────
    municipality_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # ── Municipality name (natural lookup key) ────────────────────────────────
    municipality: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )

    # ── Optional province for display / filtering ─────────────────────────────
    province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Coordinates ───────────────────────────────────────────────────────────
    latitude:  Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<GeoCache municipality={self.municipality!r} "
            f"lat={self.latitude} lng={self.longitude}>"
        )