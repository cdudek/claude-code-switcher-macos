"""Tests for the usage sample series.

The series exists because the transcripts carry no account attribution and the
API publishes only the reading you are looking at. Two things must hold: a
failed reading is never written as zero, and a pair of readings across a reset
is never treated as a rise.
"""

from datetime import datetime, timedelta, timezone

from code_agent_switcher import usage_log
from code_agent_switcher.usage_state import UsageState, UsageWindow

T0 = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)


def _state(*pairs):
    return UsageState(True, "x", tuple(UsageWindow(lbl, pct, "1h") for lbl, pct in pairs))


def _write(path, at, account, pct, label="5h", plan="max", active=True):
    usage_log.record("claude", account, plan, active, _state((label, pct)), path=path, now=at)


class TestRecording:
    def test_a_reading_round_trips(self, tmp_path):
        p = tmp_path / "s.jsonl"
        assert usage_log.record("claude", "a@b.c", "max", True, _state(("5h", 12.5)), path=p, now=T0)
        [s] = usage_log.load(p)
        assert s.account == "a@b.c"
        assert s.windows == {"5h": 12.5}
        assert s.plan == "max"
        assert s.active is True

    def test_a_failed_reading_is_not_written_as_zero(self, tmp_path):
        """A gap is honest. A row of zeros looks like an idle account."""
        p = tmp_path / "s.jsonl"
        assert usage_log.record("claude", "a@b.c", "max", True,
                                UsageState(False, "Usage unavailable"), path=p) is False
        assert usage_log.load(p) == []

    def test_a_reading_with_no_windows_is_not_written(self, tmp_path):
        p = tmp_path / "s.jsonl"
        assert usage_log.record("claude", "a@b.c", "max", True,
                                UsageState(True, "x", ()), path=p) is False
        assert usage_log.load(p) == []

    def test_a_damaged_line_does_not_lose_the_file(self, tmp_path):
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 1.0)
        with p.open("a") as h:
            h.write("{not json\n")
        _write(p, T0 + timedelta(minutes=5), "a@b.c", 2.0)
        assert len(usage_log.load(p)) == 2

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert usage_log.load(tmp_path / "nothing.jsonl") == []

    def test_prune_keeps_the_newest(self, tmp_path):
        p = tmp_path / "s.jsonl"
        for i in range(10):
            _write(p, T0 + timedelta(minutes=i), "a@b.c", float(i))
        usage_log.prune(p, keep=4)
        loaded = usage_log.load(p)
        assert len(loaded) == 4
        assert [s.windows["5h"] for s in loaded] == [6.0, 7.0, 8.0, 9.0]


class TestSteps:
    def test_a_rise_between_two_readings_is_a_step(self, tmp_path):
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 10.0)
        _write(p, T0 + timedelta(minutes=5), "a@b.c", 13.0)
        [step] = usage_log.steps(usage_log.load(p))
        assert step.delta_percent == 3.0
        assert step.minutes == 5

    def test_a_drop_is_a_reset_and_not_a_step(self, tmp_path):
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 90.0)
        _write(p, T0 + timedelta(minutes=5), "a@b.c", 2.0)
        assert usage_log.steps(usage_log.load(p)) == []

    def test_a_long_gap_is_not_a_measurement(self, tmp_path):
        """The app is not always running; tokens cannot be attributed across
        twelve hours of nothing."""
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 10.0)
        _write(p, T0 + timedelta(hours=12), "a@b.c", 40.0)
        assert usage_log.steps(usage_log.load(p)) == []

    def test_accounts_do_not_bleed_into_each_other(self, tmp_path):
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 10.0)
        _write(p, T0 + timedelta(minutes=1), "other@b.c", 80.0)
        _write(p, T0 + timedelta(minutes=5), "a@b.c", 12.0)
        steps = usage_log.steps(usage_log.load(p))
        assert [s.account for s in steps] == ["a@b.c"]
        assert steps[0].delta_percent == 2.0

    def test_windows_do_not_bleed_into_each_other(self, tmp_path):
        p = tmp_path / "s.jsonl"
        usage_log.record("claude", "a@b.c", "max", True,
                         _state(("5h", 10.0), ("7d", 80.0)), path=p, now=T0)
        usage_log.record("claude", "a@b.c", "max", True,
                         _state(("5h", 12.0), ("7d", 81.0)), path=p,
                         now=T0 + timedelta(minutes=5))
        rises = {s.label: s.delta_percent for s in usage_log.steps(usage_log.load(p))}
        assert rises == {"5h": 2.0, "7d": 1.0}


class TestResetsSeen:
    def test_a_drop_counts_as_a_reset(self, tmp_path):
        p = tmp_path / "s.jsonl"
        for pct in (10.0, 40.0, 3.0, 20.0, 1.0):
            _write(p, T0 + timedelta(minutes=len(usage_log.load(p)) * 5), "a@b.c", pct)
        assert usage_log.resets_seen(usage_log.load(p))[("a@b.c", "5h")] == 2

    def test_noise_below_a_point_is_not_a_reset(self, tmp_path):
        p = tmp_path / "s.jsonl"
        _write(p, T0, "a@b.c", 40.0)
        _write(p, T0 + timedelta(minutes=5), "a@b.c", 39.5)
        assert usage_log.resets_seen(usage_log.load(p))[("a@b.c", "5h")] == 0
