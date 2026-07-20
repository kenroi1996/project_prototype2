"""
tests/conftest.py
==================
Shared fixtures for the EarlyAlert test suite.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# Make sure the project root is importable even if pytest is invoked
# from a different working directory (pytest.ini's pythonpath handles
# the normal case; this is a defensive backstop).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def reset_system_config():
    """
    SystemConfig caches settings in a module-level dict. Without this,
    a threshold change made by one test (e.g. test_prediction_engine's
    admin-override case) would silently leak into every test that runs
    after it, in any file, since pytest imports modules once per session.
    """
    import services.system_config as sc
    original = dict(sc._cache)
    yield
    sc._cache = original


@pytest.fixture
def mock_conn():
    """
    A MagicMock standing in for a psycopg2 connection, with cursor()
    usable as a context manager — matches the `with conn.cursor() as cur:`
    pattern used everywhere in this codebase.
    """
    conn = MagicMock()
    return conn


@pytest.fixture
def encryption_key(monkeypatch):
    """
    encryption_utils.py builds its Fernet instance once at import time
    from os.environ["ENCRYPTION_KEY"]. To test it with a known key,
    set the env var and force a reimport so the module rebuilds its
    Fernet instance against the new key rather than reusing whatever
    (if anything) was cached from the first import in this test session.
    """
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)

    import importlib
    import services.encryption_utils as eu
    importlib.reload(eu)
    yield key
    importlib.reload(eu)  # restore whatever state existed before the test