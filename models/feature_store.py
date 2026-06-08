"""
models/feature_store.py
───────────────────────
Stores the engineered features produced by feature_engineering.py for each
student, so the ML model can be retrained without re-running the full
engineering pipeline every time.

One row per student per pipeline run (identified by run_id).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class FeatureStore(Base):
    __tablename__ = "feature_store"
    __table_args__ = {"schema": "public", "extend_existing": True}

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    student_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    run_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
        comment="FK to merge_log.run_id — identifies the pipeline run",
    )

    # ── Engineered features (mirrors FINAL_FEATURES in feature_engineering.py)
    gpa_tier:             Mapped[Optional[int]]   = mapped_column(SmallInteger)
    has_college_grade:    Mapped[Optional[int]]   = mapped_column(SmallInteger)
    year_level:           Mapped[Optional[int]]   = mapped_column(SmallInteger)
    entrance_exam_tier:   Mapped[Optional[int]]   = mapped_column(SmallInteger)
    hs_performance_tier:  Mapped[Optional[int]]   = mapped_column(SmallInteger)
    strand_program_match: Mapped[Optional[float]] = mapped_column(Float)
    financial_stress:     Mapped[Optional[int]]   = mapped_column(SmallInteger)
    first_gen_student:    Mapped[Optional[int]]   = mapped_column(SmallInteger)
    has_scholarship:      Mapped[Optional[int]]   = mapped_column(SmallInteger)
    gap_years:            Mapped[Optional[int]]   = mapped_column(SmallInteger)
    private_hs:           Mapped[Optional[int]]   = mapped_column(SmallInteger)
    has_hs_honors:        Mapped[Optional[int]]   = mapped_column(SmallInteger)
    age_at_enrollment:    Mapped[Optional[float]] = mapped_column(Float)
    program_encoded:      Mapped[Optional[int]]   = mapped_column(Integer)
    sex_code_encoded:     Mapped[Optional[int]]   = mapped_column(SmallInteger)

    # ── Target variable ───────────────────────────────────────────────────────
    risk_label: Mapped[Optional[str]] = mapped_column(
        String(20),
        comment="low_risk | moderate_risk | high_risk",
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FeatureStore student={self.student_id} "
            f"run={self.run_id} gpa_tier={self.gpa_tier}>"
        )
