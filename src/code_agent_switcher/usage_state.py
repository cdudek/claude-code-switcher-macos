"""Provider-independent usage state helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageWindow:
    label: str
    percent: float
    resets_in: str | None = None


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

    def is_exhausted(self, threshold: float = 100.0) -> bool:
        """Return whether any known usage window has reached the threshold."""
        max_percent = self.max_percent
        return max_percent is not None and max_percent >= threshold
