"""Provider-independent usage state helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageWindow:
    label: str
    percent: float
    resets_in: str | None = None


FULL, EMPTY = "\u2588", "\u2591"


def meter(percent: float, cells: int = 10) -> str:
    """A fixed-width bar. Both glyphs are the same width, so rows line up.

    Any non-zero reading gets at least one cell, so 1% does not look like 0%.
    A full bar is reserved for 100 and over, so a nearly-spent window is still
    visibly short of the end - that difference is the one worth seeing.
    """
    if percent <= 0:
        filled = 0
    elif percent >= 100:
        filled = cells
    else:
        filled = max(1, int(percent * cells // 100))
    return FULL * filled + EMPTY * (cells - filled)


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
            f"{w.label:<3}{meter(w.percent, width)}  {w.percent:>3.0f}%"
            + (f"   resets in {w.resets_in}" if w.resets_in else "")
            for w in state.windows[:2]
        ]
        while len(rows) < 2:
            rows.append("")
        return rows[0], rows[1]
    return "no usage reading", state.reason or "reason unknown"
