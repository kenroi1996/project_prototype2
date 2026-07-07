"""
workers/settings_workers.py
==============================
Background QThread workers for the Settings page.

  - _UserLoader           : loads all user accounts
  - _UserDeleter          : permanently removes a user account
  - _ConfigLoader         : loads system_config key/value rows
  - _AuditLoader          : loads the current user's login audit trail
  - _AllActivityLoader    : loads filtered activity_log rows (admin view)
  - _ActivityLogCleaner   : deletes activity_log rows (by id list or filter)

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from services.data_store import DataStore


class _UserLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE public.users
                    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP
                """)
                conn.commit()
                cur.execute("""
                    SELECT user_id, username, full_name, email,
                           role, office, is_active, created_at, last_login
                    FROM   public.users
                    ORDER  BY created_at ASC
                """)
                rows = [dict(zip(
                    ["user_id","username","full_name","email",
                     "role","office","is_active","created_at","last_login"],
                    r
                )) for r in cur.fetchall()]
            self.finished.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _UserDeleter(QThread):
    """
    Permanently removes a user account and all their activity log entries.
    Never call this on the currently-logged-in admin.
    """
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, user_id: int):
        super().__init__()
        self._user_id = user_id

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                # Resolve username first (needed for activity_log lookup)
                cur.execute(
                    "SELECT username FROM public.users WHERE user_id = %s",
                    (self._user_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(
                        "User not found — may have already been deleted.")
                username = row[0]

                # ── Null out FK references so the DELETE can proceed ──
                # interventions.counselor_id → users.user_id
                cur.execute(
                    """
                    UPDATE public.interventions
                    SET    counselor_id = NULL
                    WHERE  counselor_id = %s
                    """,
                    (self._user_id,),
                )

                # Add further FK nullifications here if other tables
                # reference users.user_id in the future, e.g.:
                #   UPDATE public.some_table SET user_id = NULL
                #   WHERE user_id = %s

                # ── Remove activity log rows by username ──────────────
                cur.execute(
                    "DELETE FROM public.activity_log WHERE user_name = %s",
                    (username,),
                )

                # ── Delete the user row ───────────────────────────────
                cur.execute(
                    "DELETE FROM public.users WHERE user_id = %s",
                    (self._user_id,),
                )
                if cur.rowcount == 0:
                    raise ValueError(
                        "User not found — may have already been deleted.")

            conn.commit()
            self.finished.emit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(exc))


class _ConfigLoader(QThread):
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.system_config (
                        key        VARCHAR(100) PRIMARY KEY,
                        value      TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_by VARCHAR(100)
                    )
                """)
                conn.commit()
                cur.execute("SELECT key, value FROM public.system_config")
                cfg = {r[0]: r[1] for r in cur.fetchall()}
            self.finished.emit(cfg)
        except Exception as e:
            self.error.emit(str(e))


class _AuditLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, username: str):
        super().__init__()
        self._username = username

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT log_timestamp, action, description, status
                    FROM   public.activity_log
                    WHERE  user_name = %s
                      AND  action IN ('LOGIN', 'LOGIN_FAILED', 'LOGOUT')
                    ORDER  BY log_timestamp DESC
                    LIMIT  20
                """, (self._username,))
                rows = cur.fetchall()
            self.finished.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _AllActivityLoader(QThread):
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, filters: dict):
        super().__init__()
        self._filters = filters

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            f       = self._filters
            clauses = []
            params  = []
            if f.get("username"):
                clauses.append("al.user_name ILIKE %s")
                params.append(f"%{f['username']}%")
            if f.get("action"):
                clauses.append("al.action = %s")
                params.append(f["action"])
            if f.get("status"):
                clauses.append("al.status = %s")
                params.append(f["status"])
            if f.get("date_from"):
                clauses.append("al.log_timestamp >= %s")
                params.append(f["date_from"])
            if f.get("date_to"):
                clauses.append("al.log_timestamp <= %s")
                params.append(f["date_to"] + " 23:59:59")
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = f"""
                SELECT log_id, log_timestamp, user_name, action,
                       description, status
                FROM   public.activity_log al
                {where}
                ORDER  BY al.log_timestamp DESC
                LIMIT  500
            """
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                self.finished.emit([dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as exc:
            self.error.emit(str(exc))


class _ActivityLogCleaner(QThread):
    finished = pyqtSignal(int)
    error    = pyqtSignal(str)

    def __init__(self, ids: list[int] | None = None,
                 filters: dict | None = None):
        super().__init__()
        self._ids     = ids
        self._filters = filters or {}

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                if self._ids is not None:
                    cur.execute(
                        "DELETE FROM public.activity_log "
                        "WHERE log_id = ANY(%s) RETURNING log_id",
                        (self._ids,)
                    )
                else:
                    f       = self._filters
                    clauses = []
                    params  = []
                    if f.get("username"):
                        clauses.append("user_name ILIKE %s")
                        params.append(f"%{f['username']}%")
                    if f.get("action"):
                        clauses.append("action = %s")
                        params.append(f["action"])
                    if f.get("status"):
                        clauses.append("status = %s")
                        params.append(f["status"])
                    if f.get("date_from"):
                        clauses.append("log_timestamp >= %s")
                        params.append(f["date_from"])
                    if f.get("date_to"):
                        clauses.append("log_timestamp <= %s")
                        params.append(f["date_to"] + " 23:59:59")
                    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                    cur.execute(
                        f"DELETE FROM public.activity_log {where} RETURNING log_id",
                        params
                    )
                deleted = len(cur.fetchall())
            conn.commit()
            self.finished.emit(deleted)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(exc))