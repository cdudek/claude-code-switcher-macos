"""Provider-independent usage state helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageWindow:
    label: str
    percent: float
    resets_in: str | None = None


# Colour by how much room is left, not by decoration. A menu item's title is
# plain text - rumps offers no attributed string - so the only colour available
# is in the glyphs themselves, and emoji squares are the only coloured glyphs
# that render at menu size on every macOS version.
CALM = "\U0001F7E9"      # green, plenty of room
WATCH = "\U0001F7E8"     # yellow, past half
CLOSE = "\U0001F7E7"     # orange, running out
SPENT = "\U0001F7E5"     # red, nearly gone or gone

BANDS = ((50.0, CALM), (75.0, WATCH), (90.0, CLOSE))


def band(percent: float) -> str:
    """The colour for a utilisation figure."""
    for ceiling, glyph in BANDS:
        if percent < ceiling:
            return glyph
    return SPENT


def meter(percent: float, cells: int = 10) -> str:
    """A bar of coloured squares. Length is the figure, colour is the urgency.

    No empty track. An unfilled cell would have to be a white or black square
    and one of those disappears into the menu background - the menu follows the
    system theme and the app cannot ask which one is in use. Length alone
    carries the reading, so the row puts the percentage first and lets the bar
    run off to the right where a ragged edge costs nothing.

    Any non-zero reading gets at least one square, so 1% does not read as 0%.
    """
    if percent <= 0:
        return ""
    filled = cells if percent >= 100 else max(1, int(percent * cells // 100))
    return band(percent) * filled


@dataclass(frozen=True)
class UsageState:
    available: bool
    display: str
    windows: tuple[UsageWindow, ...] = ()
    reason: str | None = None

    @property
    def max_percent(self) -> float | None:
        """Return the highest known utilization percentage."""
        return max((window.percent for window in self.windows), default=None)

    def is_exhausted(self, threshold: float = 100.0) -> bool:
        """Return whether any known usage window has reached the threshold."""
        max_percent = self.max_percent
        return max_percent is not None and max_percent >= threshold


ROW_INDENT = "      "


def usage_rows(state: "UsageState", width: int = 10) -> tuple[str, str]:
    """The two menu lines under an account.

    One line per limit window, each a label, a bar, the figure and when it
    resets. A percentage on its own does not answer the question people
    actually have, which is "how much room is left" - a bar does, at a glance,
    without reading.

    Always two lines, whatever the state, so the menu can be refreshed in place
    without rebuilding it.
    """
    if state.available and state.windows:
        rows = [
            f"{w.label:<3}{w.percent:>3.0f}%  "
            + (f"resets in {w.resets_in:<8}" if w.resets_in else " " * 19)
            + meter(w.percent, width)
            for w in state.windows[:2]
        ]
        while len(rows) < 2:
            rows.append("")
        return rows[0], rows[1]
    return "no usage reading", state.reason or "reason unknown"
