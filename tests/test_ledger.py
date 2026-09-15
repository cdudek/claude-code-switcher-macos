"""Tests for the token ledger.

The two things that can silently produce a wrong number are deduplication (a
resumed session repeats every message, doubling the totals) and the session
window reconstruction (the count of windows IS the count of limit resets).
"""

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from code_agent_switcher.ledger import (
    HAIKU_CLASS,
    OPUS_CLASS,
    SONNET_CLASS,
    Record,
    Totals,
    assumed_models,
    by_day,
    by_project,
    tokens_between_for,
    by_model,
    claude_records,
    codex_records,
    rate_for,
    session_windows,
    unpriced_models,
    windows_per_day,
)

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _rec(minutes=0, model="claude-opus-5", out=10, cr=0, cw=0, inp=0, provider="claude"):
    return Record(
        at=T0 + timedelta(minutes=minutes),
        provider=provider,
        model=model,
        input=inp,
        output=out,
        cache_write=cw,
        cache_read=cr,
    )


def _claude_line(mid, ts, model="claude-opus-5", **usage):
    base = {"input_tokens": 1, "output_tokens": 2,
            "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4}
    base.update(usage)
    return json.dumps({
        "timestamp": ts,
        "message": {"id": mid, "model": model, "usage": base},
    }) + "\n"


class TestClaudeRecords:
    def test_reads_tokens_model_and_time(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(_claude_line("m1", "2026-09-01T09:00:00.000Z"))
        [r] = list(claude_records(root=tmp_path))
        assert (r.input, r.output, r.cache_write, r.cache_read) == (1, 2, 3, 4)
        assert r.model == "claude-opus-5"
        assert r.provider == "claude"
        assert r.at == T0

    def test_a_repeated_message_id_is_counted_once(self, tmp_path):
        """A resumed session rewrites earlier messages into the new transcript."""
        line = _claude_line("m1", "2026-09-01T09:00:00.000Z")
        (tmp_path / "a.jsonl").write_text(line)
        (tmp_path / "b.jsonl").write_text(line)
        assert len(list(claude_records(root=tmp_path))) == 1

    def test_different_ids_are_both_counted(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(
            _claude_line("m1", "2026-09-01T09:00:00.000Z")
            + _claude_line("m2", "2026-09-01T09:01:00.000Z")
        )
        assert len(list(claude_records(root=tmp_path))) == 2

    def test_lines_without_usage_are_skipped(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(
            json.dumps({"timestamp": "2026-09-01T09:00:00Z", "message": {"id": "x"}}) + "\n"
            + json.dumps({"type": "user", "timestamp": "2026-09-01T09:00:00Z"}) + "\n"
            + "not json\n"
            + _claude_line("m1", "2026-09-01T09:02:00.000Z")
        )
        assert len(list(claude_records(root=tmp_path))) == 1

    def test_a_missing_tree_yields_nothing(self, tmp_path):
        assert list(claude_records(root=tmp_path / "gone")) == []


class TestCodexRecords:
    def _file(self, tmp_path, model="gpt-5.6-sol", rid="r1", cached=100):
        (tmp_path / "s.jsonl").write_text(
            json.dumps({"type": "turn_context", "timestamp": "2026-09-01T09:00:00.000Z",
                        "payload": {"context": {"model": model}}}) + "\n"
            + json.dumps({"type": "token_usage_record",
                          "timestamp": "2026-09-01T09:00:00.000Z",
                          "payload": {"response_id": rid,
                                      "usage": {"input_tokens": 500,
                                                "cached_input_tokens": cached,
                                                "cache_write_input_tokens": 7,
                                                "output_tokens": 20}}}) + "\n"
        )

    def test_model_comes_from_the_session_header(self, tmp_path):
        self._file(tmp_path)
        [r] = list(codex_records(root=tmp_path))
        assert r.model == "gpt-5.6-sol"
        assert r.provider == "codex"

    def test_cached_tokens_are_not_double_counted_as_input(self, tmp_path):
        """Codex reports input_tokens inclusive of the cached part."""
        self._file(tmp_path, cached=100)
        [r] = list(codex_records(root=tmp_path))
        assert r.input == 400
        assert r.cache_read == 100
        assert r.billable == 400 + 100 + 7 + 20


class TestSessionWindows:
    def test_one_burst_is_one_window(self):
        records = [_rec(0), _rec(30), _rec(120)]
        assert len(session_windows(records)) == 1

    def test_a_record_past_five_hours_opens_the_next_window(self):
        records = [_rec(0), _rec(299), _rec(301)]
        windows = session_windows(records)
        assert len(windows) == 2
        assert windows[0].totals.messages == 2
        assert windows[1].totals.messages == 1

    def test_the_window_runs_from_its_first_record_not_the_last(self):
        """The limit is five hours from the first message, so a long session
        does not push its own reset further away."""
        records = [_rec(0), _rec(290), _rec(295), _rec(305)]
        windows = session_windows(records)
        assert len(windows) == 2
        assert windows[1].start == T0 + timedelta(minutes=305)

    def test_exactly_five_hours_starts_a_new_window(self):
        assert len(session_windows([_rec(0), _rec(300)])) == 2

    def test_active_span_is_first_to_last_not_the_whole_window(self):
        [w] = session_windows([_rec(0), _rec(90)])
        assert w.minutes_used == 90

    def test_no_records_no_windows(self):
        assert session_windows([]) == []

    def test_windows_group_by_the_day_they_opened(self):
        windows = session_windows([_rec(0), _rec(60 * 30)])
        grouped = windows_per_day(windows)
        assert len(grouped) == 2


class TestGrouping:
    def test_by_day_sums_each_day(self):
        days = by_day([_rec(0, out=10), _rec(5, out=15), _rec(60 * 30, out=7)])
        assert [t.output for t in days.values()] == [25, 7]

    def test_by_model_is_ordered_by_volume(self):
        records = [_rec(0, model="small", out=1), _rec(1, model="big", out=100)]
        assert list(by_model(records)) == ["big", "small"]


class TestRates:
    def test_a_known_model_is_not_assumed(self):
        assert rate_for("claude-opus-5") == (OPUS_CLASS, False)

    @pytest.mark.parametrize("model,expected", [
        ("claude-fable-5-1", OPUS_CLASS),
        ("claude-sonnet-9", SONNET_CLASS),
        ("claude-haiku-9", HAIKU_CLASS),
    ])
    def test_an_unknown_claude_model_is_priced_by_family(self, model, expected):
        rate, assumed = rate_for(model)
        assert (rate, assumed) == (expected, True)

    def test_a_non_claude_model_has_no_rate(self):
        assert rate_for("gpt-5.6-sol") == (None, False)

    def test_an_unknown_model_costs_something_rather_than_nothing(self):
        """Pricing a new model at zero understates by exactly the amount that
        matters, because the new model is where the volume is."""
        assert _rec(model="claude-fable-5-1", out=1_000_000).cost() > 0

    def test_the_report_can_name_assumed_and_unpriced_models(self):
        records = [_rec(model="claude-fable-5-1"), _rec(model="gpt-5.6-sol", provider="codex"),
                   _rec(model="claude-opus-5")]
        assert assumed_models(records) == ["claude-fable-5-1"]
        assert unpriced_models(records) == ["gpt-5.6-sol"]

    def test_cost_uses_every_token_class(self):
        rate = {"m": (1.0, 2.0, 4.0, 8.0)}
        r = Record(at=T0, provider="claude", model="m",
                   input=1_000_000, output=1_000_000,
                   cache_write=1_000_000, cache_read=1_000_000)
        assert r.cost(rate) == pytest.approx(15.0)

    def test_totals_accumulate_cost(self):
        t = Totals()
        t.add(_rec(out=1_000_000))
        t.add(_rec(out=1_000_000))
        assert t.dollars == pytest.approx(150.0)


class TestReportRenders:
    """The report is one static file; the ways it breaks are empty input and
    a model name that carries HTML."""

    def test_renders_with_no_records(self):
        from code_agent_switcher.report import render
        page = render([])
        assert "<!doctype html>" in page
        assert "No records in this range." in page

    def test_shows_the_window_count(self):
        from code_agent_switcher.report import render
        page = render([_rec(0), _rec(400)])
        assert "<div class=k>Windows</div><div class=v>2" in page

    def test_escapes_a_model_name(self):
        from code_agent_switcher.report import render
        page = render([_rec(model="<script>x</script>")])
        assert "<script>x</script>" not in page
        assert "&lt;script&gt;" in page

    def test_names_an_unpriced_model_instead_of_hiding_it(self):
        from code_agent_switcher.report import render
        page = render([_rec(model="gpt-5.6-sol", provider="codex", out=5)])
        assert "gpt-5.6-sol" in page
        assert "no rate" in page

    def test_writes_where_it_says_it_wrote(self, tmp_path):
        from code_agent_switcher.report import write_report
        out = write_report([_rec(0)], tmp_path / "deep" / "usage.html")
        assert out.is_file()
        assert out.read_text().startswith("<!doctype html>")


class TestProjectAttribution:
    """Both agents record the working directory, so the spend can be split by
    repository without anyone tagging anything."""

    @staticmethod
    def _repo(root, name):
        repo = root / name
        (repo / ".git").mkdir(parents=True)
        return repo

    def _project_of(self, tmp_path, cwd):
        from code_agent_switcher import ledger
        ledger._repo_name.cache_clear()
        (tmp_path / "t" ).mkdir(exist_ok=True)
        (tmp_path / "t" / "a.jsonl").write_text(json.dumps({
            "timestamp": "2026-09-01T09:00:00.000Z",
            "cwd": str(cwd),
            "message": {"id": "m1", "model": "claude-opus-5",
                        "usage": {"output_tokens": 5}},
        }) + "\n")
        [r] = list(claude_records(root=tmp_path / "t"))
        return r.project

    def test_claude_records_carry_the_repository(self, tmp_path):
        repo = self._repo(tmp_path, "omr-leadhub")
        assert self._project_of(tmp_path, repo) == "omr-leadhub"

    def test_a_subdirectory_reports_the_repository_not_itself(self, tmp_path):
        repo = self._repo(tmp_path, "omr-leadhub")
        (repo / "src" / "web").mkdir(parents=True)
        assert self._project_of(tmp_path, repo / "src" / "web") == "omr-leadhub"

    def test_a_worktree_is_folded_into_its_repository(self, tmp_path):
        """A worktree at <repo>/.worktrees/<branch> is work on that repo. Naming
        it by the branch scattered one repository across a row per branch."""
        repo = self._repo(tmp_path, "omr-leadhub")
        tree = repo / ".worktrees" / "screens-sanity"
        tree.mkdir(parents=True)
        (tree / ".git").write_text("gitdir: ../../.git/worktrees/screens-sanity\n")
        assert self._project_of(tmp_path, tree) == "omr-leadhub"

    def test_a_directory_under_no_repository_is_left_unassigned(self, tmp_path):
        loose = tmp_path / "observer-sessions"
        loose.mkdir()
        assert self._project_of(tmp_path, loose) == ""

    def _under_root(self, monkeypatch, tmp_path, rel):
        from code_agent_switcher import ledger
        root = tmp_path / "projects"
        monkeypatch.setattr(ledger, "PROJECT_ROOTS", (root,))
        return root, root.joinpath(*rel)

    def test_a_deleted_repository_keeps_its_name(self, monkeypatch, tmp_path):
        """It has no .git left to find, but the path still names it, and it was
        the second largest directory in the history."""
        root, cwd = self._under_root(monkeypatch, tmp_path, ("omr", "omr-marketing-engine"))
        (root / "omr").mkdir(parents=True)
        assert self._project_of(tmp_path, cwd) == "omr-marketing-engine"

    def test_a_deleted_repository_directly_under_the_root_keeps_its_name(
        self, monkeypatch, tmp_path
    ):
        root, cwd = self._under_root(monkeypatch, tmp_path, ("elk-herd",))
        root.mkdir(parents=True)
        assert self._project_of(tmp_path, cwd) == "elk-herd"

    def test_an_owner_folder_is_not_a_repository(self, monkeypatch, tmp_path):
        root, cwd = self._under_root(monkeypatch, tmp_path, ("omr",))
        cwd.mkdir(parents=True)
        assert self._project_of(tmp_path, cwd) == ""

    def test_an_agent_sandbox_is_not_a_repository(self, monkeypatch, tmp_path):
        """One is made per agent run and deleted after, so every run that ever
        happened was leaving its own row in among the real repositories."""
        root, cwd = self._under_root(monkeypatch, tmp_path, ("omr", "agent-a54b2a6d1bf892354"))
        (root / "omr").mkdir(parents=True)
        assert self._project_of(tmp_path, cwd) == ""

    def test_a_dot_directory_is_not_a_repository(self, tmp_path):
        """~/.claude is version controlled, so a job sandbox under it was
        landing as a project called ".claude"."""
        sandbox = tmp_path / ".claude" / "jobs" / "a737" / "tmp"
        sandbox.mkdir(parents=True)
        (tmp_path / ".claude" / ".git").mkdir()
        assert self._project_of(tmp_path, sandbox) == ""

    def test_a_subdirectory_of_a_deleted_repository_still_names_the_repository(
        self, monkeypatch, tmp_path
    ):
        """`<root>/elk-herd/src` with elk-herd gone is the repository elk-herd,
        not a repository called src."""
        root, cwd = self._under_root(monkeypatch, tmp_path, ("elk-herd", "src"))
        root.mkdir(parents=True)
        assert self._project_of(tmp_path, cwd) == "elk-herd"

    def test_a_record_with_no_cwd_is_not_fatal(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(_claude_line("m1", "2026-09-01T09:00:00.000Z"))
        [r] = list(claude_records(root=tmp_path))
        assert r.project == ""

    def test_by_project_groups_and_ranks_by_cost(self):
        records = [
            dataclasses.replace(_rec(0, out=1_000_000), project="small"),
            dataclasses.replace(_rec(1, out=9_000_000), project="big"),
            dataclasses.replace(_rec(2, out=1_000_000), project="small"),
        ]
        grouped = by_project(records)
        assert list(grouped) == ["big", "small"]
        assert grouped["small"].messages == 2

    def test_records_with_no_repository_are_named_not_dropped(self):
        assert list(by_project([_rec(0)])) == ["Other"]


class TestProviderScopedTokens:
    def test_a_codex_window_is_not_priced_with_claude_tokens(self):
        records = sorted(
            [_rec(0, out=100), _rec(1, out=900, provider="codex")],
            key=lambda r: r.at,
        )
        start, end = T0 - timedelta(minutes=1), T0 + timedelta(minutes=5)
        assert tokens_between_for(records, start, end, "codex") == 900
        assert tokens_between_for(records, start, end, "claude") == 100


class TestHourlyRateSection:
    """Printing only the hours that have readings made five hours of data look
    like a finished picture of the day."""

    def _page(self, samples):
        from code_agent_switcher.report import _rate_by_hour_section
        return _rate_by_hour_section([_rec(0, out=1_000_000)], samples, ("5h", "7d"))

    def _samples(self, tmp_path, hours):
        from code_agent_switcher import usage_log
        from code_agent_switcher.usage_state import UsageState, UsageWindow
        p = tmp_path / "s.jsonl"
        for hour in hours:
            base = T0.replace(hour=hour, minute=0)
            for i, pct in enumerate((10.0, 12.0)):
                usage_log.record(
                    "claude", "a@b.c", "team", True,
                    UsageState(True, "x", (UsageWindow("5h", pct),)),
                    path=p, now=base + timedelta(minutes=i * 5),
                )
        return usage_log.load(p)

    def test_every_hour_of_the_day_gets_a_row(self, tmp_path):
        page = self._page(self._samples(tmp_path, [9]))
        for hour in range(24):
            assert f"{hour:02d}:00" in page

    def test_the_covered_count_is_stated(self, tmp_path):
        assert "1 of 24 hours" in self._page(self._samples(tmp_path, [9]))
        assert "3 of 24 hours" in self._page(self._samples(tmp_path, [9, 14, 20]))

    def test_an_hour_with_no_reading_is_dimmed_not_hidden(self, tmp_path):
        page = self._page(self._samples(tmp_path, [9]))
        assert "class=quiet" in page

    def test_no_readings_at_all_says_so(self):
        assert "Not enough readings" in self._page([])
