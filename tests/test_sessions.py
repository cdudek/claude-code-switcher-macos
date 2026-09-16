"""A session already running keeps spending the account you thought you left:
Claude Code reads its credential once, at startup, and never looks again."""

from datetime import datetime, timedelta, timezone

from code_agent_switcher import sessions
from code_agent_switcher.sessions import running_sessions, spent_elsewhere
from code_agent_switcher.usage_log import Sample

T0 = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


def _s(minutes, percent, active=False, account="a@x.com", provider="claude"):
    return Sample(at=T0 + timedelta(minutes=minutes), provider=provider, account=account,
                  plan="max", active=active, windows={"5h": percent}, resets={})


class TestSpentElsewhere:
    def test_a_rise_on_an_idle_account_is_something_else_spending_it(self):
        assert spent_elsewhere([_s(0, 10.0), _s(5, 12.0)]) == {("claude", "a@x.com")}

    def test_a_rise_on_the_account_in_use_is_not_reported(self):
        """That one is supposed to be spending."""
        assert spent_elsewhere([_s(0, 10.0, active=True), _s(5, 12.0, active=True)]) == set()

    def test_a_flat_idle_account_is_not_reported(self):
        assert spent_elsewhere([_s(0, 10.0), _s(5, 10.0)]) == set()

    def test_a_window_reset_is_not_a_rise(self):
        assert spent_elsewhere([_s(0, 40.0), _s(5, 3.0)]) == set()

    def test_a_gap_too_long_to_attribute_is_ignored(self):
        """The app is not always running; twelve hours apart measures nothing."""
        assert spent_elsewhere([_s(0, 10.0), _s(600, 30.0)],
                               window=timedelta(days=2)) == set()

    def test_only_the_recent_window_counts(self):
        """It answers "is something spending this right now", and a rise from
        this morning does not. Keying on history reported three of four
        accounts as haunted."""
        old = [_s(0, 10.0), _s(5, 20.0)]
        now = T0 + timedelta(hours=6)
        assert spent_elsewhere(old, now=now) == set()
        assert spent_elsewhere(old, now=now, window=timedelta(hours=12)) == {("claude", "a@x.com")}

    def test_an_interval_that_starts_active_is_not_counted(self):
        """Mid-switch the account was still in use for part of the interval."""
        assert spent_elsewhere([_s(0, 10.0, active=True), _s(5, 12.0)]) == set()

    def test_providers_are_kept_apart(self):
        rows = [_s(0, 10.0, provider="codex"), _s(5, 12.0, provider="codex")]
        assert spent_elsewhere(rows) == {("codex", "a@x.com")}

    def test_no_samples_is_not_a_crash(self):
        assert spent_elsewhere([]) == set()


class TestRunningSessions:
    def _pgrep(self, monkeypatch, stdout, returncode=0):
        class R:
            pass
        r = R(); r.stdout = stdout; r.returncode = returncode
        monkeypatch.setattr(sessions.subprocess, "run", lambda *a, **k: r)

    def test_it_counts_the_processes(self, monkeypatch):
        self._pgrep(monkeypatch, "2916\n4158\n4357\n")
        assert running_sessions() == 3

    def test_no_processes_is_zero_not_one(self, monkeypatch):
        """pgrep exits non-zero and prints nothing when it matches nothing; a
        naive line count returns 1 and warns about a session that is not there."""
        self._pgrep(monkeypatch, "", returncode=1)
        assert running_sessions() == 0

    def test_output_on_a_failed_call_is_not_counted(self, monkeypatch):
        """A bad invocation can print and still fail. Counting its output would
        warn about sessions that do not exist, and a warning that cries wolf
        gets clicked through."""
        self._pgrep(monkeypatch, "pgrep: illegal option -- z\n", returncode=2)
        assert running_sessions() == 0

    def test_a_trailing_newline_is_not_a_session(self, monkeypatch):
        self._pgrep(monkeypatch, "2916\n")
        assert running_sessions() == 1

    def test_pgrep_missing_is_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(sessions.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no pgrep")))
        assert running_sessions() == 0
