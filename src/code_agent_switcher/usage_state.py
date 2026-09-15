"""Provider-independent usage state helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageWindow:
    label: str
    percent: float
    resets_in: str | None = None
    # The raw timestamp as well as the formatted string. Auto-switch needs to
    # know that a window resets in nine minutes, and re-parsing "3h 14m" back
    # into a duration would be inventing precision the string already lost.
    resets_at: str | None = None


# How much room is left, in thresholds. The panel paints these; they live here
# so there is one table rather than one per drawing surface.
BAND_LIMITS = (50.0, 75.0, 90.0)


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

    @property
    def binding(self) -> "UsageWindow | None":
        """The window closest to its limit - the one that actually stops you."""
        return max(self.windows, key=lambda w: w.percent, default=None)

    def is_exhausted(self, threshold: float = 100.0) -> bool:
        """Return whether any known usage window has reached the threshold."""
        max_percent = self.max_percent
        return max_percent is not None and max_percent >= threshold
