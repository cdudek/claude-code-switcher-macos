"""Tests for the pure decision helpers behind the app's error dialogs.

The rumps UI itself is not exercised here — these cover the branch logic that
decides WHICH dialog a failure gets and what each button does, which is where
the dead-end "Error" alert came from.
"""

import pytest

from code_agent_switcher.app import (
    ALERT_CANCEL,
    ALERT_OK,
    ALERT_OTHER,
    EXPIRED_SESSION_MESSAGE,
    EXPIRED_SESSION_TITLE,
    expired_session_action,
    is_expired_session,
)
from code_agent_switcher.codex_core import CodexCredentialsExpiredError
from code_agent_switcher.core import ClaudeCredentialsExpiredError


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
    """Just enough of the app to call _live_active_ref unbound."""
    config_path = "/nowhere/accounts.json"


class TestLiveActiveEmail:
    """The selected-account dot follows the live sign-in, not our own record."""

    def _run(self, monkeypatch, *, live, recorded, saved, live_org="", orgs=None):
        import code_agent_switcher.app as app
        written = []
        orgs = orgs or {}
        monkeypatch.setattr(app, "live_claude_email", lambda: live)
        monkeypatch.setattr(app, "live_claude_org", lambda: live_org or None)
        monkeypatch.setattr(app, "get_active_account",
                            lambda path, provider: _Account(recorded) if recorded else None)
        monkeypatch.setattr(app, "load_accounts",
                            lambda path: [_Account(e, org_uuid=orgs.get(i, ""),
                                                   slot=orgs.get(i, ""))
                                          for i, e in enumerate(saved)])
        monkeypatch.setattr(app, "set_active_account",
                            lambda email, path, provider: written.append(email))
        result = app.ClaudeSwitcherApp._live_active_ref(_Stub(), "claude")
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

    def test_a_live_account_we_do_not_hold_matches_no_row(self, monkeypatch):
        """Nothing to mark and nothing to record: a ref must name a saved
        account or it means nothing downstream."""
        result, written = self._run(monkeypatch, live="stranger@x.com", recorded="a@x.com",
                                    saved=["a@x.com"])
        assert result is None
        assert written == []

    def test_unreadable_live_state_falls_back_to_the_record(self, monkeypatch):
        result, written = self._run(monkeypatch, live=None, recorded="a@x.com",
                                    saved=["a@x.com"])
        assert result is None
        assert written == []


class _Account:
    def __init__(self, email, provider="claude", slot="", org_uuid=""):
        self.email = email
        self.provider = provider
        self.slot = slot
        self.org_uuid = org_uuid

    @property
    def ref(self):
        return f"{self.email}#{self.slot}" if self.slot else self.email


class TestIsRowActive:
    """Mutation note: this decision was inline and untested - dropping the live
    half passed the whole suite."""

    def _call(self, email, live, recorded):
        from code_agent_switcher.app import ClaudeSwitcherApp
        return ClaudeSwitcherApp._is_row_active(email, live, recorded)

    def test_live_account_is_marked(self):
        assert self._call("b@x.com", "b@x.com", False) is True

    def test_stale_record_is_not_marked(self):
        assert self._call("a@x.com", "b@x.com", True) is False

    def test_without_a_live_reading_the_record_decides(self):
        assert self._call("a@x.com", None, True) is True
        assert self._call("a@x.com", None, False) is False


class TestConnectorFooter:
    """The line under the bars. A claude.ai connector is authorised per account
    on Anthropic's side, so a switch takes every one of them away and this app
    has nothing to copy - saying so before the switch is the whole point."""

    def _app(self, cache, live_ref="live@x.com"):
        from code_agent_switcher.app import ClaudeSwitcherApp
        app = ClaudeSwitcherApp.__new__(ClaudeSwitcherApp)
        app._connectors_cache = cache
        app._connectors_at = {}
        app._spent_elsewhere = set()
        app._live_active_ref = lambda provider: live_ref
        return app

    @staticmethod
    def _rows(*pairs):
        from code_agent_switcher.connectors import Connector
        return tuple(Connector(n, r) for n, r in pairs)

    LIVE = ("claude", "live@x.com")
    OTHER = ("claude", "other@x.com")

    def test_the_active_account_is_told_how_many_it_has(self):
        app = self._app({self.LIVE: self._rows(("Linear", "connected"),
                                               ("Gmail", "never_connected_no_auto_connect"))})
        assert app._connector_footer(self.LIVE, True) == ("1 claude.ai connector", False)

    def test_the_count_is_pluralised(self):
        app = self._app({self.LIVE: self._rows(("Linear", "connected"),
                                               ("Slack", "connected"))})
        assert app._connector_footer(self.LIVE, True) == ("2 claude.ai connectors", False)

    def test_a_switch_that_costs_something_says_what(self):
        app = self._app({
            self.LIVE: self._rows(("Linear", "connected"), ("Slack", "connected")),
            self.OTHER: self._rows(("Linear", "never_connected_no_auto_connect"),
                                   ("Slack", "connected")),
        })
        assert app._connector_footer(self.OTHER, False) == ("Switching drops Linear", True)

    def test_more_than_two_losses_are_counted_not_listed(self):
        """Four names do not fit the card, and a clipped line reads as a bug."""
        app = self._app({
            self.LIVE: self._rows(("Linear", "connected"), ("Slack", "connected"),
                                  ("Gmail", "connected"), ("Figma", "connected")),
            self.OTHER: (),
        })
        assert app._connector_footer(self.OTHER, False) == (
            "Switching drops Linear, Slack +2", True)

    def test_a_switch_that_costs_nothing_does_not_warn(self):
        app = self._app({
            self.LIVE: self._rows(("Linear", "connected")),
            self.OTHER: self._rows(("Linear", "connected")),
        })
        assert app._connector_footer(self.OTHER, False) == ("1 claude.ai connectors", False)

    def test_an_unread_account_shows_no_line_at_all(self):
        """Not "0 connectors" - claiming an account has none because the API
        did not answer is worse than saying nothing."""
        app = self._app({})
        assert app._connector_footer(self.OTHER, False) == ("", False)

    def test_an_unread_live_account_does_not_produce_a_warning(self):
        app = self._app({self.OTHER: self._rows(("Linear", "connected"))})
        assert app._connector_footer(self.OTHER, False) == ("1 claude.ai connectors", False)

    @staticmethod
    def _account(provider="claude", email="other@x.com"):
        from code_agent_switcher.config import AccountInfo
        return AccountInfo(email, "", "", False, "acct", provider=provider)

    def test_codex_is_never_asked(self, monkeypatch):
        """Codex has no claude.ai connectors, so the call would be a wasted
        round trip on every poll for every Codex account."""
        from code_agent_switcher import app as app_mod
        calls = []
        monkeypatch.setattr(app_mod.connectors_api, "fetch_connectors_for_account",
                            lambda ref: calls.append(ref) or ((), None))
        app = self._app({})
        assert app._connectors_for(self._account(provider="codex")) is None
        assert calls == []

    def test_a_failed_read_keeps_the_last_good_answer(self, monkeypatch):
        """Otherwise one timeout makes the footer vanish and come back, which
        reads as the connectors themselves flickering."""
        from code_agent_switcher import app as app_mod
        good = self._rows(("Linear", "connected"))
        app = self._app({self.OTHER: good})
        app._connectors_at[self.OTHER] = 0.0  # force a re-read
        monkeypatch.setattr(app_mod.connectors_api, "fetch_connectors_for_account",
                            lambda ref: (None, "no answer from the API"))
        assert app._connectors_for(self._account()) == good
        assert app._connectors_cache[self.OTHER] == good


class TestConstructorOrdering:
    """`__init__` calls `_rebuild_menu()`, which reads caches that `__init__`
    itself sets up. v0.12.0 set one of them six lines too late and crashed on
    every launch; the footer tests never saw it because they build the object
    with `__new__` and assign the attributes by hand. This runs the real
    constructor."""

    def _build(self, monkeypatch, tmp_path, accounts):
        import rumps
        from code_agent_switcher import app as app_mod

        monkeypatch.setattr(rumps.App, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(rumps.App, "menu", _FakeMenu(), raising=False)
        monkeypatch.setattr(rumps, "Timer", lambda *a, **k: _FakeTimer())
        monkeypatch.setattr(app_mod.threading, "Timer", lambda *a, **k: _FakeTimer())
        monkeypatch.setattr(app_mod, "icon_path", lambda: None)
        monkeypatch.setattr(app_mod, "AccountsWindowController", lambda app: object())
        monkeypatch.setattr(app_mod, "load_accounts", lambda path: accounts)
        monkeypatch.setattr(app_mod.ClaudeSwitcherApp, "_first_launch", lambda self: None)
        monkeypatch.setattr(app_mod.ClaudeSwitcherApp, "_fetch_all_usage", lambda self: None)
        monkeypatch.setattr(app_mod.ClaudeSwitcherApp, "_live_active_ref",
                            lambda self, provider: None)
        monkeypatch.setattr(app_mod.ClaudeSwitcherApp, "config_path", tmp_path / "c.json",
                            raising=False)
        return app_mod.ClaudeSwitcherApp()

    def test_the_constructor_builds_a_menu_with_an_account_in_it(self, monkeypatch, tmp_path):
        """One account is what it takes: an empty list never reaches _card_for,
        which is where the missing attribute was read."""
        from code_agent_switcher.config import AccountInfo
        app = self._build(monkeypatch, tmp_path,
                          [AccountInfo("a@x.com", "max", "", True, "acct")])
        assert app._connectors_cache == {}
        assert app._connectors_at == {}


class _FakeTimer:
    def start(self):
        pass

    def stop(self):
        pass


class _FakeMenu:
    def __init__(self):
        self._menu = _FakeNSMenu()

    def clear(self):
        pass

    def add(self, item):
        pass


class _FakeNSMenu:
    def setDelegate_(self, d):
        pass

    def setAutoenablesItems_(self, flag):
        pass

    def addItem_(self, item):
        pass


class TestInUseElsewhereLine:
    """A session started before the switch keeps spending the account it was
    launched with, because Claude Code reads its credential once at startup."""

    def _app(self, spent, connectors=None):
        from code_agent_switcher.app import ClaudeSwitcherApp
        app = ClaudeSwitcherApp.__new__(ClaudeSwitcherApp)
        app._connectors_cache = connectors or {}
        app._connectors_at = {}
        app._spent_elsewhere = spent
        app._live_active_ref = lambda provider: "live@x.com"
        return app

    KEY = ("claude", "other@x.com")

    def test_it_says_so_and_warns(self):
        app = self._app({self.KEY})
        assert app._connector_footer(self.KEY, False) == ("In use by another session", True)

    def test_it_outranks_the_connector_count(self):
        """The connector number is trivia next to a bill nobody can account
        for."""
        from code_agent_switcher.connectors import Connector
        app = self._app({self.KEY}, {self.KEY: (Connector("Linear", "connected"),)})
        assert app._connector_footer(self.KEY, False)[0] == "In use by another session"

    def test_an_untouched_account_falls_through_to_connectors(self):
        from code_agent_switcher.connectors import Connector
        app = self._app(set(), {self.KEY: (Connector("Linear", "connected"),)})
        assert app._connector_footer(self.KEY, False) == ("1 claude.ai connectors", False)


class TestSwitchWarning:
    """Those sessions hold the credential they started with, and one refreshing
    its token can flip the live account back on its own."""

    def _app(self, count):
        from code_agent_switcher import app as app_mod
        from code_agent_switcher.app import ClaudeSwitcherApp
        app = ClaudeSwitcherApp.__new__(ClaudeSwitcherApp)
        return app, app_mod

    def test_no_running_sessions_switches_without_asking(self, monkeypatch):
        app, mod = self._app(0)
        monkeypatch.setattr(mod.sessions_api, "running_sessions", lambda: 0)
        monkeypatch.setattr(mod.rumps, "alert", lambda **k: pytest.fail("should not ask"))
        assert app._confirm_switch_with_running_sessions("claude") is True

    def test_it_asks_when_a_session_is_running(self, monkeypatch):
        app, mod = self._app(3)
        seen = {}
        monkeypatch.setattr(mod.sessions_api, "running_sessions", lambda: 3)
        monkeypatch.setattr(mod.rumps, "alert", lambda **k: seen.update(k) or 1)
        assert app._confirm_switch_with_running_sessions("claude") is True
        assert "3 Claude Code sessions are running" in seen["message"]

    def test_cancelling_stops_the_switch(self, monkeypatch):
        app, mod = self._app(1)
        monkeypatch.setattr(mod.sessions_api, "running_sessions", lambda: 1)
        monkeypatch.setattr(mod.rumps, "alert", lambda **k: 0)
        assert app._confirm_switch_with_running_sessions("claude") is False

    def test_one_session_reads_as_singular(self, monkeypatch):
        app, mod = self._app(1)
        seen = {}
        monkeypatch.setattr(mod.sessions_api, "running_sessions", lambda: 1)
        monkeypatch.setattr(mod.rumps, "alert", lambda **k: seen.update(k) or 1)
        app._confirm_switch_with_running_sessions("claude")
        assert "1 Claude Code session is running" in seen["message"]

    def test_codex_is_not_asked_about(self, monkeypatch):
        """Only Claude Code holds a credential this way."""
        app, mod = self._app(5)
        monkeypatch.setattr(mod.sessions_api, "running_sessions",
                            lambda: pytest.fail("should not be called"))
        assert app._confirm_switch_with_running_sessions("codex") is True


class TestSwitchHonoursTheWarning:
    """Mutation note: deleting the confirm call from _switch_account passed the
    whole suite - the function was tested, the call site was not."""

    def _app(self, monkeypatch, answer):
        from code_agent_switcher import app as app_mod
        from code_agent_switcher.app import ClaudeSwitcherApp
        app = ClaudeSwitcherApp.__new__(ClaudeSwitcherApp)
        app._switch_in_progress = set()
        app._live_active_ref = lambda provider: "someone-else@x.com"
        monkeypatch.setattr(ClaudeSwitcherApp, "_confirm_switch_with_running_sessions",
                            lambda self, provider: answer)
        started = []
        monkeypatch.setattr(app_mod.threading, "Thread",
                            lambda **k: type("T", (), {"start": lambda s: started.append(k)})())
        return app, started

    def test_cancelling_the_warning_does_not_switch(self, monkeypatch):
        app, started = self._app(monkeypatch, False)
        app._switch_account("claude", "target@x.com")
        assert started == []
        assert app._switch_in_progress == set()

    def test_confirming_goes_ahead(self, monkeypatch):
        app, started = self._app(monkeypatch, True)
        app._switch_account("claude", "target@x.com")
        assert len(started) == 1
