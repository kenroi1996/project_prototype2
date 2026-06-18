"""
Auth Service
============
Manages user authentication and the current session for EarlyAlert.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

_SESSION: Optional[dict] = None


class AuthService:

    @staticmethod
    def login(conn, username: str, password: str) -> tuple[bool, str]:
        global _SESSION

        if not username or not password:
            return False, "Username and password are required."

        try:
            import bcrypt

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, username, password_hash,
                           full_name, email, role, office, is_active
                    FROM   public.users
                    WHERE  username = %s
                    """,
                    (username.strip(),),
                )
                row = cur.fetchone()

            if row is None:
                _log_failed_attempt(conn, username, "User not found")
                return False, "Invalid username or password."

            (user_id, db_username, pw_hash,
             full_name, email, role, office, is_active) = row

            # Disabled accounts show generic message — no hint that the
            # account exists, consistent with security best practice.
            if not is_active:
                _log_failed_attempt(conn, username, "Account disabled")
                return False, "Invalid username or password."

            if not bcrypt.checkpw(password.encode(), pw_hash.encode()):
                _log_failed_attempt(conn, username, "Wrong password")
                return False, "Invalid username or password."

            # ── Update last_login timestamp ───────────────────────────
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        ALTER TABLE public.users
                        ADD COLUMN IF NOT EXISTS last_login TIMESTAMP
                        """,
                    )
                    cur.execute(
                        "UPDATE public.users SET last_login = NOW() WHERE user_id = %s",
                        (user_id,),
                    )
            except Exception as _le:
                print(f"[AuthService] last_login update failed (non-fatal): {_le}")

            # ── Build session ─────────────────────────────────────────
            session_id = str(uuid.uuid4())
            _SESSION = {
                "user_id":    str(user_id),
                "username":   db_username,
                "full_name":  full_name,
                "email":      email or "",
                "role":       role,
                "office":     office or "",
                "session_id": session_id,
                "login_at":   datetime.now().isoformat(),
            }

            from services.activity_logger import ActivityLogger
            ActivityLogger.log(
                conn,
                action      = "LOGIN",
                entity_type = "SESSION",
                entity_id   = session_id,
                description = f"{full_name} logged in successfully.",
                status      = "SUCCESS",
            )
            conn.commit()

            print(f"[AuthService] Login: {full_name} ({role}) — session {session_id[:8]}…")
            return True, ""

        except Exception as exc:
            print(f"[AuthService] Login error: {exc}")
            return False, f"Database error during login: {exc}"

    @staticmethod
    def logout(conn=None) -> None:
        global _SESSION
        if _SESSION and conn:
            try:
                from services.activity_logger import ActivityLogger
                ActivityLogger.log(
                    conn,
                    action      = "LOGOUT",
                    entity_type = "SESSION",
                    entity_id   = _SESSION.get("session_id", ""),
                    description = f"{_SESSION['full_name']} logged out.",
                    status      = "SUCCESS",
                )
                conn.commit()
            except Exception as exc:
                print(f"[AuthService] Logout log error: {exc}")

        print(f"[AuthService] Session ended for "
              f"{(_SESSION or {}).get('username', '?')}")
        _SESSION = None

    @staticmethod
    def current_user() -> Optional[dict]:
        return _SESSION

    @staticmethod
    def is_logged_in() -> bool:
        return _SESSION is not None

    @staticmethod
    def current_user_id() -> Optional[str]:
        return _SESSION["user_id"] if _SESSION else None

    @staticmethod
    def current_username() -> Optional[str]:
        return _SESSION["username"] if _SESSION else None

    @staticmethod
    def current_full_name() -> str:
        return _SESSION["full_name"] if _SESSION else "Unknown"

    @staticmethod
    def current_session_id() -> Optional[str]:
        return _SESSION["session_id"] if _SESSION else None

    @staticmethod
    def current_role() -> Optional[str]:
        return _SESSION["role"] if _SESSION else None

    @staticmethod
    def is_admin() -> bool:
        return (_SESSION or {}).get("role") == "admin"

    # ── Password recovery ─────────────────────────────────────────────

    SECURITY_QUESTIONS = [
        "What was the name of your first pet?",
        "What is your mother's maiden name?",
        "What was the name of your primary school?",
        "What was your childhood nickname?",
        "What city were you born in?",
        "What is the name of your favorite teacher?",
    ]

    @staticmethod
    def needs_security_setup(conn) -> bool:
        """Return True if the current user has no security question set."""
        if not _SESSION:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT security_question FROM public.users "
                    "WHERE user_id = %s",
                    (_SESSION["user_id"],)
                )
                row = cur.fetchone()
                return row is None or row[0] is None
        except Exception:
            return False

    @staticmethod
    def needs_password_change(conn) -> bool:
        """Return True if the current user must change their password."""
        if not _SESSION:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT force_password_change FROM public.users "
                    "WHERE user_id = %s",
                    (_SESSION["user_id"],)
                )
                row = cur.fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    @staticmethod
    def set_security_question(conn, question: str, answer: str) -> tuple[bool, str]:
        """Save security question and hashed answer for the current user."""
        if not _SESSION:
            return False, "Not logged in."
        if not question or not answer:
            return False, "Question and answer are required."
        try:
            import bcrypt
            answer_hash = bcrypt.hashpw(
                answer.strip().lower().encode(), bcrypt.gensalt()
            ).decode()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.users
                    SET    security_question    = %s,
                           security_answer_hash = %s
                    WHERE  user_id = %s
                    """,
                    (question, answer_hash, _SESSION["user_id"])
                )
            conn.commit()
            return True, ""
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_security_question(conn, username: str) -> tuple[bool, str]:
        """
        Return (found, question) for the given username.
        Returns (False, error_msg) if user not found or no question set.
        """
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT security_question FROM public.users "
                    "WHERE username = %s AND is_active = TRUE",
                    (username.strip(),)
                )
                row = cur.fetchone()
            if row is None:
                return False, "Username not found."
            if not row[0]:
                return False, (
                    "No security question set for this account. "
                    "Please contact your administrator."
                )
            return True, row[0]
        except Exception as e:
            return False, str(e)

    @staticmethod
    def verify_security_answer(
        conn, username: str, answer: str
    ) -> tuple[bool, str]:
        """
        Verify the security answer for a given username.
        Returns (True, "") on success, (False, error_msg) on failure.
        """
        try:
            import bcrypt
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT security_answer_hash FROM public.users "
                    "WHERE username = %s AND is_active = TRUE",
                    (username.strip(),)
                )
                row = cur.fetchone()
            if row is None or not row[0]:
                return False, "Username not found or no security question set."
            if not bcrypt.checkpw(
                answer.strip().lower().encode(), row[0].encode()
            ):
                return False, "Incorrect answer."
            return True, ""
        except Exception as e:
            return False, str(e)

    @staticmethod
    def reset_password_with_answer(
        conn, username: str, new_password: str
    ) -> tuple[bool, str]:
        """
        Reset password after security answer has been verified.
        Call only after verify_security_answer returns True.
        """
        if len(new_password) < 8:
            return False, "Password must be at least 8 characters."
        try:
            import bcrypt
            pw_hash = bcrypt.hashpw(
                new_password.encode(), bcrypt.gensalt()
            ).decode()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.users
                    SET    password_hash         = %s,
                           force_password_change = FALSE
                    WHERE  username = %s
                    """,
                    (pw_hash, username.strip())
                )
            conn.commit()
            return True, ""
        except Exception as e:
            return False, str(e)

    @staticmethod
    def admin_reset_password(
        conn, user_id: int, temp_password: str
    ) -> tuple[bool, str]:
        """
        Admin resets another user's password and sets force_password_change.
        The user will be forced to set a new password on next login.
        """
        if len(temp_password) < 8:
            return False, "Temporary password must be at least 8 characters."
        try:
            import bcrypt
            pw_hash = bcrypt.hashpw(
                temp_password.encode(), bcrypt.gensalt()
            ).decode()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.users
                    SET    password_hash         = %s,
                           force_password_change = TRUE
                    WHERE  user_id = %s
                    """,
                    (pw_hash, user_id)
                )
                if cur.rowcount == 0:
                    return False, "User not found."
            conn.commit()
            return True, ""
        except Exception as e:
            return False, str(e)

    @staticmethod
    def change_password(
        conn, user_id: int, new_password: str
    ) -> tuple[bool, str]:
        """
        Change password for any user (used by forced-change flow).
        Clears force_password_change flag.
        """
        if len(new_password) < 8:
            return False, "Password must be at least 8 characters."
        try:
            import bcrypt
            pw_hash = bcrypt.hashpw(
                new_password.encode(), bcrypt.gensalt()
            ).decode()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.users
                    SET    password_hash         = %s,
                           force_password_change = FALSE
                    WHERE  user_id = %s
                    """,
                    (pw_hash, user_id)
                )
            conn.commit()
            return True, ""
        except Exception as e:
            return False, str(e)

    def ensure_schema(conn) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.users (
                        user_id       SERIAL       PRIMARY KEY,
                        username      VARCHAR(50)  NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        full_name     VARCHAR(100) NOT NULL,
                        email         VARCHAR(150),
                        role          VARCHAR(30)  NOT NULL DEFAULT 'admin',
                        office        VARCHAR(100),
                        is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
                        created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login    TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
                        ON public.users (username)
                """)
                # Migration: add last_login to existing tables
                cur.execute("""
                    ALTER TABLE public.users
                    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP
                """)
            conn.commit()
            print("[AuthService] Users table ready.")
        except Exception as exc:
            print(f"[AuthService] Schema bootstrap error: {exc}")
            try:
                conn.rollback()
            except Exception:
                pass


def _log_failed_attempt(conn, username: str, reason: str) -> None:
    try:
        from services.activity_logger import ActivityLogger
        ActivityLogger.log(
            conn,
            action      = "LOGIN_FAILED",
            entity_type = "SESSION",
            description = f"Failed login for '{username}': {reason}.",
            status      = "FAILURE",
        )
        conn.commit()
    except Exception as exc:
        print(f"[AuthService] Could not log failed attempt: {exc}")