"""Tests for the Manage Accounts window.

Only the layout facts that were asked for and can be read back: every row
starts at the same x, nothing is indented for a marker that is not drawn, and
the window does not offer a second way to switch.
"""

import AppKit

from code_agent_switcher import accounts_window
from code_agent_switcher.accounts_window import AccountsWindowController


class _Account:
    def __init__(self, email, plan, provider, slot="", org_name=""):
        self.email = email
        self.subscription_type = plan
        self.provider = provider
        self.slot = slot
        self.org_name = org_name

    @property
    def ref(self):
        return f"{self.email}#{self.slot}" if self.slot else self.email


def _walk(view, out):
    for sub in view.subviews():
        out.append(sub)
        _walk(sub, out)
    return out


def _built(active_claude="b@x.com"):
    controller = AccountsWindowController(type("App", (), {})())
    controller.show(
        [
            _Account("a@x.com", "team", "claude"),
            _Account("b@x.com", "max", "claude"),
            _Account("c@x.com", "team", "codex"),
        ],
        {"claude": active_claude, "codex": "c@x.com"},
        "9.9.9",
    )
    views = _walk(controller.window.contentView(), [])
    controller.window.close()
    return views


class TestRowLayout:
    def test_no_marker_column(self):
        """Active on the right says which one is in use; a dot on one row of
        four indented the other three for something that was not there."""
        assert [v for v in _built() if type(v).__name__ == "DotView"] == []

    def test_every_email_starts_at_the_same_x(self):
        xs = {
            round(v.frame().origin.x)
            for v in _built()
            if hasattr(v, "stringValue") and "@" in (v.stringValue() or "")
        }
        assert xs == {round(accounts_window.ROW_INSET)}

    def test_the_active_row_is_named(self):
        labels = [
            v.stringValue()
            for v in _built()
            if hasattr(v, "stringValue") and v.stringValue() == "Active"
        ]
        assert len(labels) == 2  # one per provider


class TestNoSecondSwitcher:
    def test_the_window_has_no_radios(self):
        titles = [v.title() for v in _built() if isinstance(v, AppKit.NSButton)]
        assert not any("@" in title for title in titles)

    def test_the_row_menu_does_not_offer_a_switch(self):
        """Choosing an account happens in the panel, next to the numbers."""
        import inspect
        source = inspect.getsource(accounts_window)
        source = source[source.index("def showMenu_"):source.index("def signIn_")]
        assert "Switch to this account" not in source
        assert "Sign in again" in source
        assert "Remove from the list" in source


class TestWindowChrome:
    def test_the_title_names_the_app(self):
        controller = AccountsWindowController(type("App", (), {})())
        controller.show([], {"claude": None, "codex": None}, "9.9.9")
        title = controller.window.title()
        controller.window.close()
        assert title.startswith("Code Agent Switcher")
        assert "Manage Accounts" in title
