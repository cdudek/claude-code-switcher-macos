"""AppKit views for the menu panel.

A menu item is plain text, which is why the usage rows spent three versions as
ASCII blocks and emoji. An NSMenuItem can carry a view instead, and then the
same row can be drawn properly: a real progress bar, a plan pill, colour that
means something.

Everything here is laid out with explicit frames rather than constraints. The
panel is a fixed width and every row is a fixed height, so autolayout would add
a solver and a class of bugs for no benefit.

Colours come from the system's semantic colours wherever one fits, so the panel
follows the menu's own appearance in both themes without asking which is in use.
"""

from __future__ import annotations

import AppKit
import objc
from Foundation import NSMakeRect, NSMakeSize

from code_agent_switcher.usage_state import BAND_LIMITS

PANEL_WIDTH = 360.0
PAD = 12.0

# Bands, matching usage_state: how much room is left, not decoration.
GREEN = (0.20, 0.78, 0.35)
YELLOW = (1.00, 0.84, 0.04)
ORANGE = (1.00, 0.62, 0.04)
RED = (1.00, 0.27, 0.23)
BANDS = tuple(zip(BAND_LIMITS, (GREEN, YELLOW, ORANGE)))


def band_colour(percent: float) -> AppKit.NSColor:
    for ceiling, rgb in BANDS:
        if percent < ceiling:
            break
    else:
        rgb = RED
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, 1.0)


def _label(text: str, size: float, colour=None, weight=None, align=None):
    field = AppKit.NSTextField.alloc().init()
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    if weight is None:
        field.setFont_(AppKit.NSFont.systemFontOfSize_(size))
    else:
        field.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(colour or AppKit.NSColor.labelColor())
    if align is not None:
        field.setAlignment_(align)
    return field


class BarView(AppKit.NSView):
    """A rounded track with a rounded fill. Length is the figure, colour the urgency."""

    def initWithFrame_percent_(self, frame, percent):
        self = objc.super(BarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._percent = max(0.0, min(100.0, float(percent)))
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        track = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        )
        AppKit.NSColor.quaternaryLabelColor().setFill()
        track.fill()
        if self._percent <= 0:
            return
        # A visible stub for anything non-zero: a 1% window that draws as nothing
        # reads as an unused one.
        width = max(bounds.size.height, bounds.size.width * self._percent / 100.0)
        fill = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, width, bounds.size.height), radius, radius
        )
        band_colour(self._percent).setFill()
        fill.fill()


class CardView(AppKit.NSView):
    """A rounded card. The active one is lifted, the rest sit flat."""

    def initWithFrame_active_(self, frame, active):
        self = objc.super(CardView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._active = bool(active)
        return self

    def drawRect_(self, rect):
        bounds = AppKit.NSInsetRect(self.bounds(), 0.5, 0.5)
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        if self._active:
            AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.08).setFill()
        else:
            AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.04).setFill()
        path.fill()
        AppKit.NSColor.separatorColor().setStroke()
        path.setLineWidth_(1.0)
        path.stroke()


class PillView(AppKit.NSView):
    """The plan badge: team, max, pro."""

    def initWithFrame_text_(self, frame, text):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._text = text
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 5, 5)
        AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.10).setFill()
        path.fill()
        font = AppKit.NSFont.systemFontOfSize_(10.0)
        attrs = {
            AppKit.NSFontAttributeName: font,
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.secondaryLabelColor(),
        }
        size = AppKit.NSString.stringWithString_(self._text).sizeWithAttributes_(attrs)
        AppKit.NSString.stringWithString_(self._text).drawAtPoint_withAttributes_(
            (
                (bounds.size.width - size.width) / 2.0,
                (bounds.size.height - size.height) / 2.0,
            ),
            attrs,
        )


class DotView(AppKit.NSView):
    """The active marker."""

    def drawRect_(self, rect):
        AppKit.NSColor.controlAccentColor().setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(self.bounds()).fill()


def pill_width(text: str) -> float:
    attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(10.0)}
    return AppKit.NSString.stringWithString_(text).sizeWithAttributes_(attrs).width + 14.0


CARD_TOP = 34.0
ROW_HEIGHT = 22.0
CARD_BOTTOM = 8.0


def account_card(email: str, plan: str, active: bool, rows, reason: str | None = None) -> AppKit.NSView:
    """One account: identity on top, a bar per limit window under it.

    `rows` is a sequence of (label, percent, resets_in or None). An account with
    no reading passes an empty sequence and gets one explanatory line instead,
    because a card with no rows at all looks like a rendering failure.
    """
    body = list(rows)
    height = CARD_TOP + max(1, len(body)) * ROW_HEIGHT + CARD_BOTTOM
    card = CardView.alloc().initWithFrame_active_(
        NSMakeRect(PAD, 0, PANEL_WIDTH - 2 * PAD, height), active
    )
    card.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    inner = PAD

    top = height - 26.0
    left = inner
    if active:
        dot = DotView.alloc().initWithFrame_(NSMakeRect(inner, top + 5, 8, 8))
        card.addSubview_(dot)
        left = inner + 16

    name = _label(email, 13.0, weight=AppKit.NSFontWeightMedium)
    name.sizeToFit()
    frame = name.frame()
    name.setFrame_(NSMakeRect(left, top, frame.size.width, frame.size.height))
    card.addSubview_(name)

    if plan:
        width = pill_width(plan)
        pill = PillView.alloc().initWithFrame_text_(
            NSMakeRect(left + frame.size.width + 8, top + 1, width, 16), plan
        )
        card.addSubview_(pill)

    y = height - CARD_TOP - ROW_HEIGHT + 4
    if not body:
        # The reason matters: "signed out, sign in again" needs the user to act,
        # "no answer from the API" does not, and one shared line hid which.
        note = _label(reason or "no reading yet", 11.0,
                      AppKit.NSColor.secondaryLabelColor())
        note.setFrame_(NSMakeRect(inner, y, PANEL_WIDTH - 2 * PAD - 2 * inner, 16))
        card.addSubview_(note)
        return card

    for label, percent, resets in body:
        tag = _label(label, 11.0, AppKit.NSColor.secondaryLabelColor())
        tag.setFrame_(NSMakeRect(inner, y, 24, 16))
        card.addSubview_(tag)

        bar = BarView.alloc().initWithFrame_percent_(
            NSMakeRect(inner + 26, y + 5, 130, 7), percent
        )
        card.addSubview_(bar)

        figure = _label(f"{percent:.0f}%", 12.0, align=AppKit.NSTextAlignmentRight)
        figure.setFrame_(NSMakeRect(inner + 160, y, 34, 16))
        card.addSubview_(figure)

        if resets:
            reset = _label(
                f"·  resets in {resets}", 11.0, AppKit.NSColor.secondaryLabelColor()
            )
            # Ends 12pt short of the card edge; the longest string here is
            # "resets in 12d 23h" and it must not be clipped.
            reset.setFrame_(NSMakeRect(inner + 200, y, PANEL_WIDTH - 2 * PAD - inner * 2 - 200, 16))
            card.addSubview_(reset)

        y -= ROW_HEIGHT
    return card


def section_header(text: str) -> AppKit.NSView:
    view = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, 24))
    label = _label(
        text.upper(), 11.0, AppKit.NSColor.secondaryLabelColor(),
        weight=AppKit.NSFontWeightSemibold,
    )
    label.setFrame_(NSMakeRect(PAD + 2, 4, PANEL_WIDTH - 2 * PAD, 16))
    view.addSubview_(label)
    return view


def panel_title(text: str) -> AppKit.NSView:
    view = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, 32))
    label = _label(text, 16.0, weight=AppKit.NSFontWeightBold)
    label.setFrame_(NSMakeRect(PAD + 2, 8, PANEL_WIDTH - 2 * PAD, 22))
    view.addSubview_(label)
    return view


def spacer(height: float = 6.0) -> AppKit.NSView:
    return AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_WIDTH, height))


def menu_item_with_view(view: AppKit.NSView) -> AppKit.NSMenuItem:
    item = AppKit.NSMenuItem.alloc().init()
    item.setEnabled_(False)
    item.setView_(view)
    return item


def rows_for(state) -> list[tuple[str, float, str | None]]:
    """Turn a UsageState into the card's bar rows."""
    if not getattr(state, "available", False):
        return []
    return [(w.label, float(w.percent), w.resets_in) for w in state.windows]


def symbol(name: str, size: float = 15.0) -> AppKit.NSImage | None:
    """An SF Symbol for a menu item.

    Glyph prefixes in the title do not line up: a gear, a clock and a power
    symbol are three different widths, so every row started at a different x.
    A menu item's image column is one width by construction.
    """
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size, AppKit.NSFontWeightRegular
    )
    return image.imageWithSymbolConfiguration_(config) or image


def set_symbol(item, name: str) -> None:
    """Put an SF Symbol in a rumps MenuItem's image column."""
    image = symbol(name)
    if image is None:
        return
    try:
        item._menuitem.setImage_(image)
    except AttributeError:
        pass
