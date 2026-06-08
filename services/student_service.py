"""
services/student_service.py
────────────────────────────
Database CRUD for the Student table using SQLAlchemy.

Rules
─────
• Every method opens its own session and closes it in a finally block.
• No Qt imports — pure data access logic.
• Callers may be workers or the main thread — sessions are never shared.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from db import get_session
from models.student import Student


class StudentService:

    @staticmethod
    def get_all() -> list[Student]:
        """Return all students ordered by student_id."""
        with get_session() as session:
            rows = session.scalars(
                select(Student).order_by(Student.student_id)
            ).all()
            # Detach from session so rows are safe to pass to UI thread
            return list(rows)

    @staticmethod
    def get_by_id(student_id: str) -> Optional[Student]:
        """Return a single student or None."""
        with get_session() as session:
            return session.scalars(
                select(Student).where(Student.student_id == student_id)
            ).first()

    @staticmethod
    def upsert(data: dict) -> Student:
        """
        Insert or update a student record.

        Parameters
        ----------
        data : dict mapping column names to values (student_id is required)
        """
        student_id = data.get("student_id")
        if not student_id:
            raise ValueError("student_id is required for upsert.")

        with get_session() as session:
            existing = session.scalars(
                select(Student).where(Student.student_id == student_id)
            ).first()

            if existing:
                for key, value in data.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                student = existing
            else:
                student = Student(**{
                    k: v for k, v in data.items()
                    if hasattr(Student, k)
                })
                session.add(student)

            session.flush()
            session.refresh(student)
            return student

    @staticmethod
    def bulk_upsert(records: list[dict]) -> int:
        """
        Upsert multiple student records.
        Returns the count of rows processed.
        """
        with get_session() as session:
            for data in records:
                student_id = data.get("student_id")
                if not student_id:
                    continue
                existing = session.scalars(
                    select(Student).where(Student.student_id == student_id)
                ).first()
                if existing:
                    for key, value in data.items():
                        if hasattr(existing, key):
                            setattr(existing, key, value)
                else:
                    session.add(Student(**{
                        k: v for k, v in data.items()
                        if hasattr(Student, k)
                    }))
        return len(records)

    @staticmethod
    def count() -> int:
        with get_session() as session:
            return session.query(Student).count()

    @staticmethod
    def get_at_risk(threshold: float) -> list[Student]:
        """Return students whose final_avg_grd >= threshold."""
        with get_session() as session:
            rows = session.scalars(
                select(Student)
                .where(Student.final_avg_grd >= threshold)
                .order_by(Student.final_avg_grd.desc())
            ).all()
            return list(rows)
