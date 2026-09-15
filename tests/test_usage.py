"""Tests for the usage module."""

import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from claude_switcher.usage import (
    _extract_token,
    _format_reset_delta,
    format_usage,
    fetch_usage_for_account,
    claude_usage_state,
)


class TestExtractToken:
    def test_extracts_oauth_token(self):
        creds = json.dumps({"claudeAiOauth": {"accessToken": "tok_123"}})
        assert _extract_token(creds) == "tok_123"

    def test_returns_none_for_missing_oauth(self):
        creds = json.dumps({"other": "data"})
        assert _extract_token(creds) is None

    def test_returns_none_for_invalid_json(self):
        assert _extract_token("not json") is None


class TestFormatResetDelta:
    def test_days_and_hours(self):
        future = datetime.now(timezone.utc) + timedelta(days=5, hours=13)
        result = _format_reset_delta(future.isoformat())
        assert result.startswith("5d 1")  # 5d 13h or 5d 12h depending on timing

    def test_hours_and_minutes(self):
        future = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)
        result = _format_reset_delta(future.isoformat())
        assert result.startswith("2h ")

    def test_minutes_only(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=45)
        result = _format_reset_delta(future.isoformat())
        assert result.endswith("m")
        assert "h" not in result

    def test_past_returns_now(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _format_reset_delta(past.isoformat()) == "now"

    def test_z_suffix(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        ts = future.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _format_reset_delta(ts)
        assert "h" in result or "m" in result


class TestFormatUsage:
    @patch("claude_switcher.usage.datetime")
    def test_formats_both_periods(self, mock_dt):
        now = datetime(2026, 3, 19, 10, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        usage = {
            "five_hour": {"utilization": 42.7, "resets_at": "2026-03-19T12:00:00+00:00"},
            "seven_day": {"utilization": 18.3, "resets_at": "2026-03-25T00:00:00+00:00"},
        }
        result = format_usage(usage)
        assert "5h 43% (2h 0m)" in result
        assert "7d 18% (5d 14h)" in result

    def test_returns_unavailable_for_none(self):
        assert format_usage(None) == "Usage unavailable"

    def test_returns_unavailable_for_empty(self):
        assert format_usage({}) == "Usage unavailable"

    def test_usage_state_marks_exhausted_at_100(self):
        usage = {
            "five_hour": {"utilization": 100.0},
            "seven_day": {"utilization": 12.0},
        }
        state = claude_usage_state(usage)
        assert state.available is True
        assert state.is_exhausted() is True
        assert state.max_percent == 100.0

    def test_usage_state_does_not_mark_99_9_exhausted(self):
        state = claude_usage_state({"five_hour": {"utilization": 99.9}})
        assert state.is_exhausted() is False

    def test_usage_state_unavailable_without_windows(self):
        state = claude_usage_state({"five_hour": {}, "seven_day": {}})
        assert state.available is False


class TestFetchUsageForAccount:
    @patch("claude_switcher.usage.urllib.request.urlopen")
    @patch("claude_switcher.usage.keychain.read_credentials")
    def test_fetches_and_parses(self, mock_read, mock_urlopen):
        mock_read.return_value = json.dumps({"claudeAiOauth": {"accessToken": "tok"}})
        response_data = json.dumps({
            "five_hour": {"utilization": 50.0, "resets_at": "2026-03-19T12:00:00Z"},
            "seven_day": {"utilization": 20.0, "resets_at": "2026-03-25T00:00:00Z"},
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_usage_for_account("test@test.com")
        assert result["five_hour"]["utilization"] == 50.0

    @patch("claude_switcher.usage.keychain.read_credentials")
    def test_returns_none_when_no_creds(self, mock_read):
        mock_read.return_value = None
        assert fetch_usage_for_account("test@test.com") is None


def _blob(token="tok", expires_in_hours=8.0, refresh="ref"):
    ms = int((datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).timestamp() * 1000)
    return json.dumps({"claudeAiOauth": {
        "accessToken": token, "refreshToken": refresh, "expiresAt": ms}})


class TestIsExpired:
    def test_future_expiry_is_live(self):
        from claude_switcher.usage import _is_expired
        assert _is_expired(_blob(expires_in_hours=1)) is False

    def test_past_expiry_is_expired(self):
        from claude_switcher.usage import _is_expired
        assert _is_expired(_blob(expires_in_hours=-1)) is True

    def test_missing_expiry_is_not_expired(self):
        from claude_switcher.usage import _is_expired
        assert _is_expired(json.dumps({"claudeAiOauth": {"accessToken": "t"}})) is False


class TestFetchUsageRefresh:
    """A stored token dies after eight hours and nothing else renews it."""

    def _patches(self, stored, request_results, refreshed="REFRESHED"):
        calls = {"requests": [], "written": []}

        def _request(token):
            calls["requests"].append(token)
            return request_results[len(calls["requests"]) - 1]

        def _write(service, account, password):
            calls["written"].append((service, password))

        return calls, _request, _write

    def test_401_refreshes_and_retries(self, monkeypatch):
        import claude_switcher.usage as usage
        import claude_switcher.core as core
        stored = _blob(token="OLD")
        new = _blob(token="NEW")
        calls, _request, _write = self._patches(stored, [(401, None), (200, {"five_hour": {}})])
        monkeypatch.setattr(usage.keychain, "read_credentials",
                            lambda s: stored if s != usage.keychain.CLAUDE_SERVICE else None)
        monkeypatch.setattr(usage.keychain, "read_account_attribute", lambda s: "acct")
        monkeypatch.setattr(usage.keychain, "write_credentials", _write)
        monkeypatch.setattr(core, "refresh_claude_credentials", lambda c: new)
        monkeypatch.setattr(usage, "_request_usage", _request)

        assert usage.fetch_usage("claude-switcher:a@b.c") == {"five_hour": {}}
        assert calls["requests"] == ["OLD", "NEW"]
        assert calls["written"] == [("claude-switcher:a@b.c", new)]

    def test_network_failure_does_not_rotate_the_token(self, monkeypatch):
        """A blip must not burn a refresh token that still works."""
        import claude_switcher.usage as usage
        import claude_switcher.core as core
        stored = _blob(token="OLD")
        calls, _request, _write = self._patches(stored, [(0, None)])
        refreshed = []
        monkeypatch.setattr(usage.keychain, "read_credentials", lambda s: stored)
        monkeypatch.setattr(usage.keychain, "write_credentials", _write)
        monkeypatch.setattr(core, "refresh_claude_credentials",
                            lambda c: refreshed.append(c) or _blob(token="NEW"))
        monkeypatch.setattr(usage, "_request_usage", _request)

        assert usage.fetch_usage("claude-switcher:a@b.c") is None
        assert refreshed == []
        assert calls["written"] == []

    def test_expired_blob_refreshes_before_asking(self, monkeypatch):
        import claude_switcher.usage as usage
        import claude_switcher.core as core
        stored = _blob(token="OLD", expires_in_hours=-1)
        new = _blob(token="NEW")
        calls, _request, _write = self._patches(stored, [(200, {"seven_day": {}})])
        monkeypatch.setattr(usage.keychain, "read_credentials",
                            lambda s: stored if s != usage.keychain.CLAUDE_SERVICE else None)
        monkeypatch.setattr(usage.keychain, "read_account_attribute", lambda s: "acct")
        monkeypatch.setattr(usage.keychain, "write_credentials", _write)
        monkeypatch.setattr(core, "refresh_claude_credentials", lambda c: new)
        monkeypatch.setattr(usage, "_request_usage", _request)

        assert usage.fetch_usage("claude-switcher:a@b.c") == {"seven_day": {}}
        assert calls["requests"] == ["NEW"]

    def test_rotating_the_live_pair_moves_the_live_entry_too(self, monkeypatch):
        """Refreshing a snapshot that IS the running session must not revoke it."""
        import claude_switcher.usage as usage
        import claude_switcher.core as core
        stored = _blob(token="OLD", expires_in_hours=-1)
        new = _blob(token="NEW")
        calls, _request, _write = self._patches(stored, [(200, {"five_hour": {}})])
        monkeypatch.setattr(usage.keychain, "read_credentials", lambda s: stored)
        monkeypatch.setattr(usage.keychain, "read_account_attribute", lambda s: "acct")
        monkeypatch.setattr(usage.keychain, "write_credentials", _write)
        monkeypatch.setattr(core, "refresh_claude_credentials", lambda c: new)
        monkeypatch.setattr(usage, "_request_usage", _request)

        usage.fetch_usage("claude-switcher:a@b.c")
        assert calls["written"] == [
            ("claude-switcher:a@b.c", new),
            (usage.keychain.CLAUDE_SERVICE, new),
        ]

    def test_unrelated_snapshot_leaves_the_live_entry_alone(self, monkeypatch):
        import claude_switcher.usage as usage
        import claude_switcher.core as core
        stored = _blob(token="OLD", expires_in_hours=-1)
        live = _blob(token="SOMEONE_ELSE")
        new = _blob(token="NEW")
        calls, _request, _write = self._patches(stored, [(200, {"five_hour": {}})])
        monkeypatch.setattr(usage.keychain, "read_credentials",
                            lambda s: live if s == usage.keychain.CLAUDE_SERVICE else stored)
        monkeypatch.setattr(usage.keychain, "read_account_attribute", lambda s: "acct")
        monkeypatch.setattr(usage.keychain, "write_credentials", _write)
        monkeypatch.setattr(core, "refresh_claude_credentials", lambda c: new)
        monkeypatch.setattr(usage, "_request_usage", _request)

        usage.fetch_usage("claude-switcher:a@b.c")
        assert calls["written"] == [("claude-switcher:a@b.c", new)]
