"""
models/student.py
─────────────────
Raw enrollment data uploaded from the four portals (MIS, SAO, Guidance,
Registrar).  This mirrors the staging tables already in PostgreSQL but gives
the application a typed ORM layer for queries.

Columns map directly to UNIFIED_FEATURE_MAP keys produced by MergeEngine.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class Student(Base):
    __tablename__ = "students"
    __table_args__ = {"schema": "public", "extend_existing": True}

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Natural / business key ────────────────────────────────────────────────
    student_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    # ── Academic (MIS) ────────────────────────────────────────────────────────
    program:       Mapped[Optional[str]] = mapped_column(String(100))
    college:       Mapped[Optional[str]] = mapped_column(String(100))
    sec_code:      Mapped[Optional[str]] = mapped_column(String(50))
    year_level:    Mapped[Optional[int]] = mapped_column(Integer)
    final_avg_grd: Mapped[Optional[float]] = mapped_column(Float)
    sex_code:      Mapped[Optional[str]] = mapped_column(String(10))
    home_address:  Mapped[Optional[str]] = mapped_column(String(500))
    civil_status:  Mapped[Optional[str]] = mapped_column(String(20))
    religion:      Mapped[Optional[str]] = mapped_column(String(100))

    # ── Guidance ──────────────────────────────────────────────────────────────
    entrance_exam_score:       Mapped[Optional[float]] = mapped_column(Float)
    family_income:             Mapped[Optional[str]]   = mapped_column(String(100))
    parent_highest_education:  Mapped[Optional[str]]   = mapped_column(String(150))
    applicant_age:             Mapped[Optional[int]]   = mapped_column(Integer)

    # ── Registrar ─────────────────────────────────────────────────────────────
    lastname:          Mapped[Optional[str]] = mapped_column(String(100))
    firstname:         Mapped[Optional[str]] = mapped_column(String(100))
    hs_gpa:            Mapped[Optional[float]] = mapped_column(Float)
    year_graduated:    Mapped[Optional[int]]   = mapped_column(Integer)
    shs_strand:        Mapped[Optional[str]]   = mapped_column(String(100))
    hs_type:           Mapped[Optional[str]]   = mapped_column(String(100))
    graduation_honors: Mapped[Optional[str]]   = mapped_column(String(100))
    hs_school:         Mapped[Optional[str]]   = mapped_column(String(255))
    municipality:      Mapped[Optional[str]]   = mapped_column(String(100))
    year_enrolled:     Mapped[Optional[int]]   = mapped_column(Integer)

    # ── SAO ───────────────────────────────────────────────────────────────────
    scholarship_applicant: Mapped[Optional[bool]] = mapped_column(Boolean)
    scholarship_type:      Mapped[Optional[str]]  = mapped_column(String(100))
    birthdate:             Mapped[Optional[date]]  = mapped_column(Date)

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Student id={self.student_id} program={self.program}>"
