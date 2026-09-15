"""Tests for the panel's pure pieces.

The drawing itself needs a screen. What can be checked without one is that the
colour bands land where the thresholds say, that a reading turns into the right
rows, and that a card is built at the height its contents need - a card sized
for two rows that gets three silently clips the third.
"""

import pytest

from code_agent_switcher import ui
from code_agent_switcher.usage_state import BAND_LIMITS, UsageState, UsageWindow


def _rgb(colour):
    return (round(colour.redComponent(), 2),
            round(colour.greenComponent(), 2),
            round(colour.blueComponent(), 2))


class TestBands:
    def test_the_panel_uses_the_shared_thresholds(self):
        """One table, so a threshold cannot be changed in one place only."""
        assert [limit for limit, _ in ui.BANDS] == list(BAND_LIMITS)

    @pytest.mark.parametrize("percent,expected", [
        (0.0, ui.GREEN), (49.9, ui.GREEN),
        (50.0, ui.YELLOW), (74.9, ui.YELLOW),
        (75.0, ui.ORANGE), (89.9, ui.ORANGE),
        (90.0, ui.RED), (100.0, ui.RED), (140.0, ui.RED),
    ])
    def test_each_band_starts_at_its_threshold(self, percent, expected):
        assert _rgb(ui.band_colour(percent)) == tuple(round(c, 2) for c in expected)


class TestRows:
    def test_an_available_reading_becomes_one_row_per_window(self):
        state = UsageState(True, "x", (UsageWindow("5h", 27.0, "3h"),
                                       UsageWindow("7d", 15.0, None)))
        assert ui.rows_for(state) == [("5h", 27.0, "3h"), ("7d", 15.0, None)]

    def test_an_unavailable_reading_has_no_rows(self):
        assert ui.rows_for(UsageState(False, "Usage unavailable", reason="signed out")) == []


class TestCardGeometry:
    def _height(self, rows):
        card = ui.account_card("a@b.c", "team", False, rows)
        return card.frame().size.height

    def test_a_card_grows_with_its_rows(self):
        one = self._height([("5h", 1.0, None)])
        two = self._height([("5h", 1.0, None), ("7d", 2.0, None)])
        assert two - one == ui.ROW_HEIGHT

    def test_a_reading_less_card_still_has_a_line(self):
        """Zero rows must not collapse to a title with nothing under it."""
        assert self._height([]) == self._height([("5h", 1.0, None)])

    def test_the_reason_is_what_the_empty_card_says(self):
        card = ui.account_card("a@b.c", "team", False, [], reason="signed out, sign in again")
        texts = [v.stringValue() for v in card.subviews() if hasattr(v, "stringValue")]
        assert "signed out, sign in again" in texts

    def test_every_card_is_the_panel_width(self):
        card = ui.account_card("a@b.c", "team", True, [("5h", 5.0, "1h")])
        assert card.frame().size.width == ui.PANEL_WIDTH - 2 * ui.PAD

    def test_the_active_card_carries_the_dot(self):
        active = ui.account_card("a@b.c", "team", True, [("5h", 5.0, None)])
        idle = ui.account_card("a@b.c", "team", False, [("5h", 5.0, None)])
        dots = lambda card: sum(1 for v in card.subviews() if isinstance(v, ui.DotView))
        assert (dots(active), dots(idle)) == (1, 0)


class TestMenuSymbols:
    """Glyph prefixes in the title do not line up; the image column does."""

    @pytest.mark.parametrize("name", ["person.2", "chart.bar", "gearshape", "power"])
    def test_every_symbol_the_menu_asks_for_exists(self, name):
        assert ui.symbol(name) is not None

    def test_an_unknown_symbol_is_not_fatal(self):
        assert ui.symbol("not.a.real.symbol.name") is None

    def test_setting_a_symbol_fills_the_image_column(self):
        import rumps
        item = rumps.MenuItem("Manage accounts")
        ui.set_symbol(item, "person.2")
        assert item._menuitem.image() is not None

    def test_setting_an_unknown_symbol_leaves_the_item_alone(self):
        import rumps
        item = rumps.MenuItem("Manage accounts")
        ui.set_symbol(item, "not.a.real.symbol.name")
        assert item._menuitem.image() is None


class TestCardPadding:
    """The menu lays a view-based item out at x=0 across the full width, so a
    card's own inset is discarded. The wrapper is what carries the padding."""

    def _wrapper(self):
        return ui.card_row("a@b.c", "team", False, [("5h", 10.0, None)])

    def test_the_wrapper_fills_the_panel(self):
        assert self._wrapper().frame().size.width == ui.PANEL_WIDTH

    def test_the_card_is_inset_on_both_sides(self):
        card = self._wrapper().subviews()[0]
        assert card.frame().origin.x == ui.PAD
        assert card.frame().size.width == ui.PANEL_WIDTH - 2 * ui.PAD

    def test_there_is_a_gap_below_each_card(self):
        wrapper = self._wrapper()
        card = wrapper.subviews()[0]
        assert card.frame().origin.y == ui.CARD_GAP
        assert wrapper.frame().size.height == card.frame().size.height + ui.CARD_GAP
