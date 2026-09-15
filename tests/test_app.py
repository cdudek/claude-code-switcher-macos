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
    EXPIRED_SESSION_MESSAGE,
    EXPIRED_SESSION_TITLE,
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


class TestExpiredSessionCopy:
    """The dialog is the whole recovery path, so its copy is worth pinning."""

    def test_title_names_the_account(self):
        t = EXPIRED_SESSION_TITLE.format(email="name@example.com")
        assert t == "Signed out of name@example.com"

    def test_message_is_two_short_sentences(self):
        assert EXPIRED_SESSION_MESSAGE.count(".") == 2
        assert len(EXPIRED_SESSION_MESSAGE.split()) <= 25

    def test_message_does_not_restate_the_buttons(self):
        """The buttons say what the actions are; repeating them was the bloat."""
        low = EXPIRED_SESSION_MESSAGE.lower()
        assert "remove" not in low
        assert "sign in again" not in low

    def test_message_has_no_jargon(self):
        low = EXPIRED_SESSION_MESSAGE.lower()
        for word in ("token", "oauth", "keychain", "revoke", "snapshot", "credential"):
            assert word not in low, f"{word!r} is developer language, not user language"


class _Stub:
    """Just enough of the app to call _live_active_email unbound."""
    config_path = "/nowhere/accounts.json"


class TestLiveActiveEmail:
    """The selected-account dot follows the live sign-in, not our own record."""

    def _run(self, monkeypatch, *, live, recorded, saved):
        import claude_switcher.app as app
        written = []
        monkeypatch.setattr(app, "live_claude_email", lambda: live)
        monkeypatch.setattr(app, "get_active_account",
                            lambda path, provider: _Account(recorded) if recorded else None)
        monkeypatch.setattr(app, "load_accounts",
                            lambda path: [_Account(e) for e in saved])
        monkeypatch.setattr(app, "set_active_account",
                            lambda email, path, provider: written.append(email))
        result = app.ClaudeSwitcherApp._live_active_email(_Stub(), "claude")
        return result, written

    def test_live_sign_in_wins_over_the_record(self, monkeypatch):
        result, _ = self._run(monkeypatch, live="b@x.com", recorded="a@x.com",
                              saved=["a@x.com", "b@x.com"])
        assert result == "b@x.com"

    def test_drift_repairs_the_record(self, monkeypatch):
        _, written = self._run(monkeypatch, live="b@x.com", recorded="a@x.com",
                               saved=["a@x.com", "b@x.com"])
        assert written == ["b@x.com"]

    def test_agreeing_record_is_not_rewritten(self, monkeypatch):
        _, written = self._run(monkeypatch, live="a@x.com", recorded="a@x.com",
                               saved=["a@x.com"])
        assert written == []

    def test_unsaved_live_account_is_not_written_to_the_record(self, monkeypatch):
        result, written = self._run(monkeypatch, live="stranger@x.com", recorded="a@x.com",
                                    saved=["a@x.com"])
        assert result == "stranger@x.com"
        assert written == []

    def test_unreadable_live_state_falls_back_to_the_record(self, monkeypatch):
        result, written = self._run(monkeypatch, live=None, recorded="a@x.com",
                                    saved=["a@x.com"])
        assert result is None
        assert written == []


class _Account:
    def __init__(self, email, provider="claude"):
        self.email = email
        self.provider = provider


class TestIsRowActive:
    """Mutation note: this decision was inline and untested - dropping the live
    half passed the whole suite."""

    def _call(self, email, live, recorded):
        from claude_switcher.app import ClaudeSwitcherApp
        return ClaudeSwitcherApp._is_row_active(email, live, recorded)

    def test_live_account_is_marked(self):
        assert self._call("b@x.com", "b@x.com", False) is True

    def test_stale_record_is_not_marked(self):
        assert self._call("a@x.com", "b@x.com", True) is False

    def test_without_a_live_reading_the_record_decides(self):
        assert self._call("a@x.com", None, True) is True
        assert self._call("a@x.com", None, False) is False
