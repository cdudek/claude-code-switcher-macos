"""Tests for the pure decision helpers behind the app's error dialogs.

The rumps UI itself is not exercised here — these cover the branch logic that
decides WHICH dialog a failure gets and what each button does, which is where
the dead-end "Error" alert came from.
"""

import pytest

from claude_switcher.app import (
    ALERT_CANCEL,
    ALERT_OK,
    ALERT_OTHER,
    expired_session_action,
    is_expired_session,
)
from claude_switcher.codex_core import CodexCredentialsExpiredError
from claude_switcher.core import ClaudeCredentialsExpiredError


class TestIsExpiredSession:
    def test_claude_revoked_session(self):
        assert is_expired_session(ClaudeCredentialsExpiredError("revoked")) is True

    def test_codex_revoked_session(self):
        assert is_expired_session(CodexCredentialsExpiredError("revoked")) is True

    @pytest.mark.parametrize("exc", [
        RuntimeError("Credentials not found in Keychain for a@b.com"),
        RuntimeError("Saved credentials for a@b.com are incomplete"),
        OSError("keychain unavailable"),
        ValueError("nope"),
    ])
    def test_other_failures_keep_the_generic_alert(self, exc):
        """Only a revoked sign-in gets the actionable dialog — the rest are errors."""
        assert is_expired_session(exc) is False


class TestExpiredSessionAction:
    def test_ok_button_signs_in_again(self):
        assert expired_session_action(ALERT_OK) == "signin"

    def test_other_button_removes_the_account(self):
        assert expired_session_action(ALERT_OTHER) == "remove"

    def test_cancel_does_nothing(self):
        assert expired_session_action(ALERT_CANCEL) == "cancel"

    def test_unknown_code_is_treated_as_cancel(self):
        """A dismissed dialog must never remove an account by accident."""
        for code in (2, -2, 99):
            assert expired_session_action(code) == "cancel"

    def test_the_three_codes_are_distinct(self):
        assert len({ALERT_OK, ALERT_OTHER, ALERT_CANCEL}) == 3
