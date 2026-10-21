"""Event attribute redaction: secrets stay masked, trading words containing "session" are kept (2026-10-21)."""
import pytest

from observability.events import _redacted_key


@pytest.mark.parametrize("key", ["session", "session_id", "sessionId", "SESSION_KEY", "sessid", "jsessionid",
                                 "phpsessid", "user_session_id", "api_key", "access_token", "Authorization",
                                 "cookie", "password", "client_secret", "account_no", "private_key",
                                 "credential", "refresh_token", "session_token", "session_cookie"])
def test_secret_keys_stay_redacted(key):
    assert _redacted_key(key), key


@pytest.mark.parametrize("key", ["session_date", "earliest_session", "stall_sessions", "sessions_observed",
                                 "decision_day", "max_gain_pct", "market_elapsed_fraction"])
def test_trading_keys_with_session_in_the_name_are_kept(key):
    assert not _redacted_key(key), key
