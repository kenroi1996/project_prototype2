"""
services/password_utils.py
=============================
Password validation and hashing helpers used by the Settings page
(user creation, password changes) and available for reuse elsewhere
(e.g. login/auth flows).

Extracted verbatim from ui/pages/settings_page.py — no logic changes.
"""
from __future__ import annotations
import re

_PW_MIN_LEN   = 8
_PW_RULES_TXT = (
    "At least 8 characters · 1 uppercase · 1 number · 1 special character"
)
# (both are part of this module's public surface for tabs that display
#  the rules text or need the minimum length)


def _validate_password(pw: str) -> str | None:
    if len(pw) < _PW_MIN_LEN:
        return f"Password must be at least {_PW_MIN_LEN} characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"\d", pw):
        return "Password must contain at least one number."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;':\",./<>?]", pw):
        return "Password must contain at least one special character."
    return None


def _hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _check_password(plain: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False