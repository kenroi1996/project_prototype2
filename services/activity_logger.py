"""
Activity Logger
===============
Writes structured audit entries to public.activity_log using the
current authenticated session from AuthService.

Usage
-----
    from services.activity_logger import ActivityLogger

    ActivityLogger.log(
        conn,
        action      = "TRAIN",
        entity_type = "MODEL",
        entity_id   = "rf",
        description = "Random Forest trained — Recall 75.8%  F1 0.397",
        new_values  = {"recall": 0.758, "f1": 0.397},
        status      = "SUCCESS",
    )

Standard vocabulary
-------------------
Actions     : LOGIN, LOGOUT, LOGIN_FAILED, UPLOAD, MERGE, TRAIN,
              PREDICT, VIEW, EXPORT
Entity types: SESSION, DATASET, MODEL, STUDENT

All fields except action and entity_type are optional.
user_id, user_name, and session_id are filled automatically from
the current AuthService session — callers do not need to pass them.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class ActivityLogger:

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    @classmethod
    def log(
        cls,
        conn,
        *,
        action:        str,
        entity_type:   str,
        entity_id:     Optional[str]  = None,
        description:   Optional[str]  = None,
        old_values:    Optional[dict] = None,
        new_values:    Optional[dict] = None,
        status:        str            = "SUCCESS",
        error_message: Optional[str]  = None,
        metadata:      Optional[dict] = None,
    ) -> bool:
        """
        Insert one row into public.activity_log.

        Returns True on success, False on failure (never raises).
        The caller is responsible for calling conn.commit() after
        one or more log() calls — this keeps log writes transactional
        with the business operation they describe.
        """
        try:
            from services.auth_service import AuthService

            user    = AuthService.current_user()
            user_id     = user["user_id"]     if user else None
            user_name   = user["full_name"]   if user else None
            session_id  = user["session_id"]  if user else None

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.activity_log
                        (user_id, user_name, action, entity_type, entity_id,
                         description, old_values, new_values,
                         session_id, status, error_message, metadata)
                    VALUES
                        (%s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        user_name,
                        action.upper(),
                        entity_type.upper(),
                        str(entity_id) if entity_id is not None else None,
                        description,
                        json.dumps(old_values) if old_values else None,
                        json.dumps(new_values) if new_values else None,
                        session_id,
                        status.upper(),
                        error_message,
                        json.dumps(metadata)  if metadata  else None,
                    ),
                )
            return True

        except Exception as exc:
            print(f"[ActivityLogger] Failed to write log entry: {exc}")
            return False

    # ------------------------------------------------------------------
    # Convenience wrappers — one per standard action
    # ------------------------------------------------------------------

    @classmethod
    def log_upload(cls, conn, portal: str, row_count: int,
                   filename: str = "") -> bool:
        return cls.log(
            conn,
            action      = "UPLOAD",
            entity_type = "DATASET",
            entity_id   = portal.upper(),
            description = (
                f"{portal.upper()} dataset uploaded — "
                f"{row_count:,} rows"
                + (f" ({filename})" if filename else "")
            ),
            new_values  = {"portal": portal, "row_count": row_count,
                           "filename": filename},
        )

    @classmethod
    def log_merge(cls, conn, total_merged: int, coverage_pct: float,
                  dataset_name: str = "") -> bool:
        return cls.log(
            conn,
            action      = "MERGE",
            entity_type = "DATASET",
            entity_id   = dataset_name or "unified",
            description = (
                f"Portal datasets merged — {total_merged:,} students, "
                f"coverage {coverage_pct:.0f}%"
                + (f" — \"{dataset_name}\"" if dataset_name else "")
            ),
            new_values  = {"total_merged": total_merged,
                           "coverage_pct": coverage_pct},
        )

    @classmethod
    def log_train(cls, conn, model_id: str, recall: float,
                  f1: float, pr_auc: float, threshold: float,
                  train_size: int) -> bool:
        return cls.log(
            conn,
            action      = "TRAIN",
            entity_type = "MODEL",
            entity_id   = model_id,
            description = (
                f"Model trained — Recall {recall:.1f}%  "
                f"F1 {f1:.3f}  PR-AUC {pr_auc:.3f}  "
                f"Threshold {threshold:.2f}  "
                f"Train size {train_size:,}"
            ),
            new_values  = {
                "model_id":   model_id,
                "recall":     recall,
                "f1":         f1,
                "pr_auc":     pr_auc,
                "threshold":  threshold,
                "train_size": train_size,
            },
        )

    @classmethod
    def log_predict(cls, conn, dataset_name: str, school_year: str,
                    total: int, high_risk: int,
                    moderate_risk: int) -> bool:
        return cls.log(
            conn,
            action      = "PREDICT",
            entity_type = "DATASET",
            entity_id   = dataset_name,
            description = (
                f"Prediction completed — \"{dataset_name}\" ({school_year})  "
                f"{total:,} students scored  "
                f"{high_risk:,} high-risk  {moderate_risk:,} moderate-risk"
            ),
            new_values  = {
                "dataset_name": dataset_name,
                "school_year":  school_year,
                "total":        total,
                "high_risk":    high_risk,
                "moderate_risk":moderate_risk,
            },
        )

    @classmethod
    def log_view_student(cls, conn, student_id: str,
                         student_name: str = "") -> bool:
        return cls.log(
            conn,
            action      = "VIEW",
            entity_type = "STUDENT",
            entity_id   = student_id,
            description = (
                f"Student profile viewed"
                + (f" — {student_name}" if student_name else "")
            ),
        )

    @classmethod
    def log_export(cls, conn, entity_type: str = "DATASET",
                   entity_id: str = "",
                   row_count: int = 0) -> bool:
        return cls.log(
            conn,
            action      = "EXPORT",
            entity_type = entity_type,
            entity_id   = entity_id or "csv",
            description = (
                f"Dataset exported to CSV — {row_count:,} rows"
            ),
            new_values  = {"row_count": row_count},
        )

    # ------------------------------------------------------------------
    # Recent log reader (for the activity panel in the UI)
    # ------------------------------------------------------------------

    @classmethod
    def get_recent(cls, conn, limit: int = 50) -> list[dict]:
        """
        Return the most recent `limit` activity log entries as dicts,
        ordered newest first.  Returns [] on error.
        """
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT log_id, log_timestamp, user_name, action,
                           entity_type, entity_id, description, status
                    FROM   public.activity_log
                    ORDER  BY log_timestamp DESC
                    LIMIT  %s
                    """,
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as exc:
            print(f"[ActivityLogger] get_recent error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    @classmethod
    def ensure_schema(cls, conn) -> None:
        """Create activity_log table if it doesn't exist. Safe to call on startup."""
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.activity_log (
                        log_id        SERIAL PRIMARY KEY,
                        log_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        user_id       VARCHAR(50),
                        user_name     VARCHAR(100),
                        action        VARCHAR(50)  NOT NULL,
                        entity_type   VARCHAR(50)  NOT NULL,
                        entity_id     VARCHAR(50),
                        description   TEXT,
                        old_values    JSONB,
                        new_values    JSONB,
                        ip_address    INET,
                        session_id    VARCHAR(100),
                        status        VARCHAR(20)  DEFAULT 'SUCCESS',
                        error_message TEXT,
                        metadata      JSONB
                    )
                """)
            conn.commit()
            print("[ActivityLogger] activity_log table ready.")
        except Exception as exc:
            print(f"[ActivityLogger] Schema bootstrap error: {exc}")
            try:
                conn.rollback()
            except Exception:
                pass