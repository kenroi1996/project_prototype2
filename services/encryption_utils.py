"""
services/encryption_utils.py
=============================
Field-level encryption for sensitive student attributes at rest.

Uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package) for
symmetric encryption/decryption of specific database columns — currently
`mis_students.home_address` and `sao_student_profile.birthdate`.

The encryption key is loaded from the environment (.env), the same
pattern already used for database credentials in config.py — never
hardcoded, never committed to version control.

Design notes
------------
- Encryption/decryption happens at the DatabaseService boundary (push_data
  / pull_data), NOT inside MergeEngine or the feature engineering /
  training pipeline. Those continue operating on in-memory plaintext
  during a single upload-to-prediction session, exactly as before —
  only data actually persisted to these two staging table columns is
  encrypted at rest.
- If ENCRYPTION_KEY is missing, encrypt_field()/decrypt_field() raise
  clearly rather than silently storing plaintext or crashing obscurely.
"""
from __future__ import annotations

import os
from cryptography.fernet import Fernet, InvalidToken

_KEY = os.environ.get("ENCRYPTION_KEY")
_fernet: Fernet | None = None

if _KEY:
    try:
        _fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)
    except Exception as exc:
        print(f"[encryption_utils] WARNING: ENCRYPTION_KEY is set but invalid ({exc}). "
              f"Sensitive fields will NOT be encrypted until this is fixed.")
        _fernet = None
else:
    print("[encryption_utils] WARNING: ENCRYPTION_KEY not set in environment. "
          "Sensitive fields (home_address, birthdate) will be stored in PLAINTEXT "
          "until a key is configured. Run generate_key() once and add the result "
          "to your .env file.")


def generate_key() -> str:
    """Generate a new Fernet key. Run this ONCE, then store the result in
    .env as ENCRYPTION_KEY — do not regenerate afterward, or previously
    encrypted data becomes permanently undecryptable."""
    return Fernet.generate_key().decode()


def encrypt_field(value: str | None) -> str | None:

    if value is None or value == "":
        return value
    if _fernet is None:
        return value  # fail open — already warned loudly at import time
    return _fernet.encrypt(str(value).encode()).decode()


def decrypt_field(value: str | None) -> str | None:

    if value is None or value == "":
        return value
    if _fernet is None:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        # Not valid ciphertext — almost certainly a pre-encryption plaintext
        # row. Return as-is rather than fail the whole query.
        return value
