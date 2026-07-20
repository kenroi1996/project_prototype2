"""
tests/test_encryption.py
==========================
Field-level encryption regression tests (home_address, birthdate).

Uses the `encryption_key` fixture from conftest.py, which sets a real
Fernet key and reimports the module — encryption_utils.py builds its
Fernet instance once at import time from os.environ, so tests can't
just monkeypatch the env var after the fact and expect it to take effect.
"""
from __future__ import annotations


def test_encrypt_then_decrypt_round_trips_to_original_value(encryption_key):
    from services.encryption_utils import encrypt_field, decrypt_field

    original = "Purok 3, Poblacion, Daanbantayan, Cebu"
    ciphertext = encrypt_field(original)

    assert ciphertext != original
    assert decrypt_field(ciphertext) == original


def test_encrypted_value_is_not_readable_plaintext(encryption_key):
    from services.encryption_utils import encrypt_field

    ciphertext = encrypt_field("1998-04-12")
    assert "1998" not in ciphertext
    assert "04" not in ciphertext or "12" not in ciphertext  # not a literal date substring


def test_none_and_empty_string_pass_through_unchanged(encryption_key):
    from services.encryption_utils import encrypt_field, decrypt_field

    assert encrypt_field(None) is None
    assert encrypt_field("") == ""
    assert decrypt_field(None) is None
    assert decrypt_field("") == ""


def test_decrypting_pre_encryption_plaintext_returns_it_unchanged(encryption_key):
    """
    Rows written before encryption was turned on (or before a key was
    configured) store plaintext, not Fernet ciphertext. decrypt_field
    must return that plaintext as-is rather than raising and breaking
    the whole query — see the InvalidToken handling in decrypt_field.
    """
    from services.encryption_utils import decrypt_field

    legacy_plaintext = "Cebu City"
    assert decrypt_field(legacy_plaintext) == legacy_plaintext


def test_missing_key_fails_open_to_plaintext_rather_than_crashing(monkeypatch):
    """
    If ENCRYPTION_KEY is unset, encrypt_field/decrypt_field must not
    raise — they fail open (store/return plaintext) so a missing key
    degrades gracefully rather than taking down uploads entirely. The
    loud warning is what protects against this going unnoticed, not a
    hard failure.
    """
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    import importlib
    import services.encryption_utils as eu
    importlib.reload(eu)

    assert eu.encrypt_field("Cebu City") == "Cebu City"
    assert eu.decrypt_field("Cebu City") == "Cebu City"

    # restore for any tests that run after this one in the same session
    importlib.reload(eu)