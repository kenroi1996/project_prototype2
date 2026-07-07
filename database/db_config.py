"""
database/db_config.py — DEPRECATED
===================================
This module previously held a hardcoded, plaintext database configuration
(including a real password) that was committed directly to version control.

It is no longer imported anywhere in the application. database/connection.py
now pulls credentials from config.py, which loads them from a local .env
file (excluded from version control via .gitignore) instead.

This file is kept only so any external script still importing DB_CONFIG
fails loudly with a clear message, rather than silently connecting with
stale hardcoded credentials.
"""

raise ImportError(
    "database/db_config.py is deprecated and must not be used. "
    "Import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD from config.py "
    "instead, which loads them from your local .env file."
)
