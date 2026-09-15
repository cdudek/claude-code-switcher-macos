"""Token accounting read from the agents' own transcripts.

The usage API tells you a percentage and nothing else - every dollar field on a
subscription plan comes back null. The transcripts on disk carry the real
numbers: Claude Code writes one JSONL line per assistant message under
~/.claude/projects, Codex writes token_usage_record lines under ~/.codex/sessions,
and both carry a timestamp, a model and a full token breakdown.

Two things this module is careful about.

Deduplication: the same assistant message appears in more than one transcript
when a session is resumed or forked, so totals are roughly doubled unless
message ids are tracked. Codex records carry a response_id for the same reason.

Session windows: Anthropic's five-hour limit starts at your first message and
runs five hours, then the next message opens a fresh one. That is reconstructed
here from the timestamps - it is not read from Anthropic, who only publish the
window you are in right now. The count of windows IS the count of resets.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

# The limit window Anthropic calls "5-hour". A window opens on the first message
# and closes five hours later; the next message after that opens a new one.
SESSION_WINDOW = timedelta(hours=5)

# Dollars per million tokens. List prices drift and Anthropic has not published
# a rate card for every model here, so this is an estimate and the report says
# so. A model missing from this table is counted in tokens and priced at zero,
# and the report names it rather than quietly under-reporting.
#   in / out / cache_write / cache_read
OPUS_CLASS = (15.0, 75.0, 18.75, 1.50)
SONNET_CLASS = (3.0, 15.0, 3.75, 0.30)
HAIKU_CLASS = (1.0, 5.0, 1.25, 0.10)

RATES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5": OPUS_CLASS,
    "claude-opus-4-7": OPUS_CLASS,
    "claude-sonnet-5": SONNET_CLASS,
    "claude-haiku-4-5-20251001": HAIKU_CLASS,
}


def rate_for(model: str, rates=None) -> tuple[tuple[float, float, float, float] | None, bool]:
    """The rate for a model, and whether it was assumed rather than known.

    A model with no entry used to cost nothing, which understated the total by
    whatever that model actually did - and the models missing from the table are
    the new ones, which is where the volume is. Guessing by family name is wrong
    sometimes; counting a billion tokens as free is wrong always. The report says
    which rows were assumed.
    """
    table = RATES if rates is None else rates
    known = table.get(model)
    if known:
        return known, False
    name = model.lower()
    if "haiku" in name:
        return HAIKU_CLASS, True
    if "sonnet" in name:
        return SONNET_CLASS, True
    if name.startswith("claude-") or "opus" in name:
        return OPUS_CLASS, True
    return None, False


@dataclass(frozen=True)
class Record:
    """One billed exchange."""

    at: datetime
    provider: str
    model: str
    input: int
    output: int
    cache_write: int
    cache_read: int

    @property
    def billable(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read

    def cost(self, rates: dict[str, tuple[float, float, float, float]] | None = None) -> float:
        rate, _assumed = rate_for(self.model, rates)
        if not rate:
            return 0.0
        rin, rout, rcw, rcr = rate
        return (
            self.input * rin
            + self.output * rout
            + self.cache_write * rcw
            + self.cache_read * rcr
        ) / 1_000_000


@dataclass
class Totals:
    messages: int = 0
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    dollars: float = 0.0

    def add(self, r: Record, rates=None) -> None:
        self.messages += 1
        self.input += r.input
        self.output += r.output
        self.cache_write += r.cache_write
        self.cache_read += r.cache_read
        self.dollars += r.cost(rates)

    @property
    def billable(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass
class Window:
    """One five-hour limit window, reconstructed from timestamps."""

    start: datetime
    last: datetime
    totals: Totals = field(default_factory=Totals)

    @property
    def minutes_used(self) -> float:
        return (self.last - self.start).total_seconds() / 60

    @property
    def day(self) -> date:
        return self.start.astimezone().date()


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _recent_files(root: Path, since: date | None) -> Iterator[Path]:
    """Transcripts that could hold a record on or after `since`.

    Filtering on mtime first is what makes this tolerable: the Claude tree here
    is 1.3 GB across a thousand files, and a month's report needs a fraction of
    it. mtime can only be later than the newest record inside, never earlier,
    so this drops files it is safe to drop.
    """
    if not root.is_dir():
        return
    for path in root.rglob("*.jsonl"):
        if since is not None:
            try:
                if date.fromtimestamp(os.path.getmtime(path)) < since:
                    continue
            except OSError:
                continue
        yield path


def claude_records(since: date | None = None, root: Path = CLAUDE_PROJECTS) -> Iterator[Record]:
    """Assistant messages from ~/.claude/projects, deduplicated on message id."""
    seen: set[str] = set()
    for path in _recent_files(root, since):
        try:
            handle = path.open(errors="ignore")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                at = _parse_ts(entry.get("timestamp"))
                if at is None:
                    continue
                if since is not None and at.astimezone().date() < since:
                    continue
                mid = message.get("id")
                if isinstance(mid, str):
                    if mid in seen:
                        continue
                    seen.add(mid)
                yield Record(
                    at=at,
                    provider="claude",
                    model=message.get("model") or "unknown",
                    input=int(usage.get("input_tokens") or 0),
                    output=int(usage.get("output_tokens") or 0),
                    cache_write=int(usage.get("cache_creation_input_tokens") or 0),
                    cache_read=int(usage.get("cache_read_input_tokens") or 0),
                )


def codex_records(since: date | None = None, root: Path = CODEX_SESSIONS) -> Iterator[Record]:
    """token_usage_record lines from ~/.codex/sessions, deduplicated on response id.

    The model is not on the usage record; it is on the session_meta or
    turn_context line earlier in the same file, so each file is read in order and
    the most recent model seen is the one attributed.
    """
    seen: set[str] = set()
    for path in _recent_files(root, since):
        try:
            handle = path.open(errors="ignore")
        except OSError:
            continue
        model = "unknown"
        with handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                kind = entry.get("type")
                if kind in ("session_meta", "turn_context"):
                    found = _find_model(entry.get("payload"))
                    if found:
                        model = found
                    continue
                if kind != "token_usage_record":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict):
                    continue
                usage = payload.get("usage")
                if not isinstance(usage, dict):
                    continue
                at = _parse_ts(entry.get("timestamp"))
                if at is None:
                    continue
                if since is not None and at.astimezone().date() < since:
                    continue
                rid = payload.get("response_id")
                if isinstance(rid, str):
                    if rid in seen:
                        continue
                    seen.add(rid)
                cached = int(usage.get("cached_input_tokens") or 0)
                yield Record(
                    at=at,
                    provider="codex",
                    model=model,
                    # Codex reports input_tokens INCLUSIVE of the cached part.
                    input=max(int(usage.get("input_tokens") or 0) - cached, 0),
                    output=int(usage.get("output_tokens") or 0),
                    cache_write=int(usage.get("cache_write_input_tokens") or 0),
                    cache_read=cached,
                )


def _find_model(payload) -> str | None:
    """Pull a model name out of a nested payload without knowing its shape."""
    if isinstance(payload, dict):
        value = payload.get("model")
        if isinstance(value, str) and value:
            return value
        for nested in payload.values():
            found = _find_model(nested)
            if found:
                return found
    return None


def load_records(since: date | None = None) -> list[Record]:
    """Every record from both providers, oldest first."""
    records = list(claude_records(since)) + list(codex_records(since))
    records.sort(key=lambda r: r.at)
    return records


def session_windows(
    records: Iterable[Record], span: timedelta = SESSION_WINDOW
) -> list[Window]:
    """Reconstruct the limit windows. One window per reset.

    Records must be in time order. A window opens on a record and stays open for
    `span`; the first record past that opens the next one.
    """
    windows: list[Window] = []
    for r in records:
        if not windows or r.at >= windows[-1].start + span:
            windows.append(Window(start=r.at, last=r.at))
        window = windows[-1]
        window.last = max(window.last, r.at)
        window.totals.add(r)
    return windows


def by_day(records: Iterable[Record]) -> dict[date, Totals]:
    out: dict[date, Totals] = {}
    for r in records:
        out.setdefault(r.at.astimezone().date(), Totals()).add(r)
    return dict(sorted(out.items()))


def by_model(records: Iterable[Record]) -> dict[str, Totals]:
    out: dict[str, Totals] = {}
    for r in records:
        out.setdefault(r.model, Totals()).add(r)
    return dict(sorted(out.items(), key=lambda kv: -kv[1].billable))


def by_provider(records: Iterable[Record]) -> dict[str, Totals]:
    out: dict[str, Totals] = {}
    for r in records:
        out.setdefault(r.provider, Totals()).add(r)
    return dict(sorted(out.items()))


def windows_per_day(windows: Iterable[Window]) -> dict[date, list[Window]]:
    out: dict[date, list[Window]] = {}
    for w in windows:
        out.setdefault(w.day, []).append(w)
    return dict(sorted(out.items()))


def assumed_models(records: Iterable[Record]) -> list[str]:
    """Models priced by family guess, so the report can name them."""
    return sorted({r.model for r in records if r.billable and rate_for(r.model)[1]})


def unpriced_models(records: Iterable[Record]) -> list[str]:
    """Models with real tokens and no rate at all."""
    return sorted({
        r.model for r in records if r.billable and rate_for(r.model)[0] is None
    })


def since_days(days: int) -> date:
    return (datetime.now(timezone.utc).astimezone() - timedelta(days=days - 1)).date()
