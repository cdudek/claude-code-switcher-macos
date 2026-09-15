"""A running record of what the usage API said, so the budget can be measured.

The transcripts say how many tokens were spent. They do not say which account
spent them, and they cannot: `ownerAccountUuid` appears on a handful of
bridge-session lines and nowhere else, so the history on disk carries no account
attribution and no amount of parsing will invent it.

What the app does know, every five minutes, is which account is signed in and
what percentage of each limit window it has used. Nothing kept that. This module
appends one line per account per poll, which over a few days answers the
question the API cannot: how much room a percentage point actually buys, whether
that differs between accounts and plans, and whether it differs by time of day.

Append-only JSONL, one small object per line, so a truncated write costs one
sample and never the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAMPLES_PATH = (
    Path.home() / "Library" / "Application Support" / "Code Agent Switcher" / "usage-samples.jsonl"
)

# Roughly a year of five-minute polls for four accounts. The file is tiny per
# line; this exists so it cannot grow without bound on a machine left running.
MAX_SAMPLES = 500_000


@dataclass(frozen=True)
class Sample:
    at: datetime
    provider: str
    account: str
    plan: str
    active: bool
    windows: dict[str, float]
    resets: dict[str, str | None]


def record(
    provider: str,
    account: str,
    plan: str,
    active: bool,
    state,
    path: Path = SAMPLES_PATH,
    now: datetime | None = None,
) -> bool:
    """Append one reading. Returns False when there was nothing to record.

    A failed reading is not written: a gap in the series is honest, a row of
    zeros would look like an account that used nothing.
    """
    if not getattr(state, "available", False) or not getattr(state, "windows", ()):
        return False
    line = {
        "at": (now or datetime.now(timezone.utc)).isoformat(),
        "provider": provider,
        "account": account,
        "plan": plan,
        "active": bool(active),
        "windows": {w.label: round(float(w.percent), 2) for w in state.windows},
        "resets": {w.label: w.resets_in for w in state.windows},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        return False
    return True


def load(path: Path = SAMPLES_PATH, since: datetime | None = None) -> list[Sample]:
    """Every sample, oldest first. A damaged line is skipped, not fatal."""
    if not path.is_file():
        return []
    out: list[Sample] = []
    try:
        handle = path.open(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    with handle:
        for line in handle:
            try:
                d = json.loads(line)
                at = datetime.fromisoformat(d["at"])
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                continue
            if since is not None and at < since:
                continue
            windows = d.get("windows")
            if not isinstance(windows, dict):
                continue
            out.append(
                Sample(
                    at=at,
                    provider=d.get("provider", "claude"),
                    account=d.get("account", "unknown"),
                    plan=d.get("plan", ""),
                    active=bool(d.get("active")),
                    windows={k: float(v) for k, v in windows.items()
                             if isinstance(v, (int, float))},
                    resets=d.get("resets") or {},
                )
            )
    out.sort(key=lambda s: s.at)
    return out


def prune(path: Path = SAMPLES_PATH, keep: int = MAX_SAMPLES) -> None:
    """Drop the oldest lines once the file is longer than `keep`."""
    try:
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) <= keep:
            return
        path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
    except OSError:
        pass


@dataclass
class Step:
    """One rise in a limit window, between two consecutive samples."""

    account: str
    plan: str
    label: str
    at: datetime
    minutes: float
    delta_percent: float


def steps(samples: list[Sample], max_gap: timedelta = timedelta(minutes=30)) -> list[Step]:
    """Consecutive rises per account and window.

    Pairs are dropped when the percentage FELL, which means the window reset in
    between and the difference measures nothing, and when the samples are too
    far apart to attribute tokens to the interval - the app is not always
    running, and a twelve-hour gap is not a measurement.
    """
    by_key: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        for label in s.windows:
            by_key.setdefault((s.account, label), []).append(s)

    out: list[Step] = []
    for (account, label), series in by_key.items():
        series.sort(key=lambda s: s.at)
        for before, after in zip(series, series[1:]):
            gap = after.at - before.at
            if gap <= timedelta(0) or gap > max_gap:
                continue
            rise = after.windows[label] - before.windows[label]
            if rise <= 0:
                continue
            out.append(
                Step(
                    account=account,
                    plan=after.plan,
                    label=label,
                    at=after.at,
                    minutes=gap.total_seconds() / 60,
                    delta_percent=rise,
                )
            )
    out.sort(key=lambda s: s.at)
    return out


def resets_seen(samples: list[Sample]) -> dict[tuple[str, str], int]:
    """How often each window actually reset, counted from a drop in the figure.

    This is the measured count, as opposed to the one the report reconstructs
    from message timestamps. When the two disagree the reconstruction is wrong.
    """
    counts: dict[tuple[str, str], int] = {}
    by_key: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        for label in s.windows:
            by_key.setdefault((s.account, label), []).append(s)
    for key, series in by_key.items():
        series.sort(key=lambda s: s.at)
        counts[key] = sum(
            1
            for before, after in zip(series, series[1:])
            if after.windows[key[1]] < before.windows[key[1]] - 1.0
        )
    return counts
