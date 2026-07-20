"""
tests/test_auth_service.py
=============================
Login throttling regression tests.

After MAX_FAILED_ATTEMPTS failed logins for the same username within
LOCKOUT_WINDOW_MINUTES, AuthService.login() must reject further attempts
before ever querying public.users — verified below by asserting the
users-table SELECT never runs once the threshold is hit.
"""
from __future__ import annotations

import bcrypt

from services.auth_service import AuthService, MAX_FAILED_ATTEMPTS


def _queried_users_table(mock_conn) -> bool:
    cur = mock_conn.cursor.return_value.__enter__.return_value
    return any(
        "FROM   public.users" in str(call.args[0])
        for call in cur.execute.call_args_list
    )


def test_login_rejected_with_no_credentials(mock_conn):
    ok, msg = AuthService.login(mock_conn, "", "")
    assert not ok
    assert "required" in msg.lower()


def test_login_proceeds_to_lookup_when_under_threshold(mock_conn):
    cur = mock_conn.cursor.return_value.__enter__.return_value
    # First call = recent-failures COUNT, second = user lookup (not found)
    cur.fetchone.side_effect = [(MAX_FAILED_ATTEMPTS - 1,), None]

    ok, msg = AuthService.login(mock_conn, "jdoe", "wrongpass")

    assert not ok
    assert _queried_users_table(mock_conn)


def test_login_blocked_at_threshold_without_touching_users_table(mock_conn):
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [(MAX_FAILED_ATTEMPTS,)]

    ok, msg = AuthService.login(mock_conn, "jdoe", "anypass")

    assert not ok
    assert "too many" in msg.lower()
    assert not _queried_users_table(mock_conn)


def test_login_blocked_applies_equally_to_nonexistent_usernames(mock_conn):
    """
    The throttle check runs before the account-existence lookup, and on
    purpose: if a locked-out real account behaved differently from a
    locked-out fake one, that difference would itself leak which
    usernames are real. Both must be rejected identically.
    """
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [(MAX_FAILED_ATTEMPTS,)]

    ok, msg = AuthService.login(mock_conn, "no_such_user_at_all", "anypass")

    assert not ok
    assert "too many" in msg.lower()
    assert not _queried_users_table(mock_conn)


def test_failed_attempt_count_query_error_fails_open(mock_conn):
    """
    If the COUNT query itself errors (DB hiccup, table not yet created),
    the throttle must fail open — proceed to normal login — rather than
    locking every single user out because of an unrelated problem.
    """
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = [Exception("relation does not exist"), None]
    cur.fetchone.side_effect = [None]  # user lookup after fail-open: not found

    ok, msg = AuthService.login(mock_conn, "jdoe", "pass")

    assert not ok
    assert "invalid username or password" in msg.lower()


def test_correct_password_for_active_user_logs_in(mock_conn):
    cur = mock_conn.cursor.return_value.__enter__.return_value
    pw_hash = bcrypt.hashpw(b"correct-horse", bcrypt.gensalt()).decode()

    cur.fetchone.side_effect = [
        (0,),  # under threshold
        ("uid-1", "jdoe", pw_hash, "Jane Doe", "j@x.com", "counselor", "SAO", True),
    ]

    ok, msg = AuthService.login(mock_conn, "jdoe", "correct-horse")

    assert ok
    assert msg == ""