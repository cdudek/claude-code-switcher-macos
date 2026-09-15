from code_agent_switcher.auto_switch import (
    account_key,
    should_auto_switch,
    choose_auto_switch_target,
)
from code_agent_switcher.config import AccountInfo
from code_agent_switcher.usage_state import UsageState, UsageWindow


def _account(email, provider="claude", active=False):
    return AccountInfo(email, "pro", "", active, email, provider=provider)


def _usage(percent):
    return UsageState(True, f"{percent}%", (UsageWindow("test", percent),))


def test_should_auto_switch_requires_enabled_and_exhausted_usage():
    assert should_auto_switch(_usage(100), True, 100) is True
    assert should_auto_switch(_usage(99.9), True, 100) is False
    assert should_auto_switch(_usage(100), False, 100) is False
    assert should_auto_switch(UsageState(False, "Usage unavailable"), True, 100) is False


def test_choose_target_same_provider_only():
    active = _account("active@test.com", "claude", True)
    target = _account("target@test.com", "claude")
    other_provider_same_email = _account("target@test.com", "codex")

    chosen = choose_auto_switch_target(
        "claude",
        [active, other_provider_same_email, target],
        "active@test.com",
        {account_key(target): _usage(12), account_key(other_provider_same_email): _usage(0)},
        lambda account: True,
    )

    assert chosen == target


def test_choose_target_ignores_missing_credentials():
    active = _account("active@test.com", "claude", True)
    no_creds = _account("no-creds@test.com", "claude")
    target = _account("target@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, no_creds, target],
        "active@test.com",
        {account_key(no_creds): _usage(0), account_key(target): _usage(10)},
        lambda account: account.email != "no-creds@test.com",
    )

    assert chosen == target


def test_choose_target_returns_none_when_all_targets_exhausted():
    active = _account("active@test.com", "claude", True)
    exhausted = _account("exhausted@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, exhausted],
        "active@test.com",
        {account_key(exhausted): _usage(100)},
        lambda account: True,
    )

    assert chosen is None


def test_choose_target_uses_unknown_usage_as_fallback():
    active = _account("active@test.com", "claude", True)
    unknown = _account("unknown@test.com", "claude")

    chosen = choose_auto_switch_target(
        "claude",
        [active, unknown],
        "active@test.com",
        {},
        lambda account: True,
    )

    assert chosen == unknown


from datetime import datetime, timedelta, timezone

from code_agent_switcher.auto_switch import (
    RESET_IMMINENT_MINUTES,
    can_auto_switch,
    effective_percent,
    headroom,
    minutes_to_reset,
    tokens_left,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _win(label, percent, in_minutes=None):
    at = (NOW + timedelta(minutes=in_minutes)).isoformat() if in_minutes is not None else None
    return UsageWindow(label=label, percent=percent, resets_in=None, resets_at=at)


def _state(*windows):
    return UsageState(available=True, display="x", windows=tuple(windows))


def _acct(email, provider="claude", plan="team"):
    return AccountInfo(
        email=email, subscription_type=plan, org_name="", active=False,
        keychain_account=email, provider=provider,
    )


class TestResetAwareness:
    def test_minutes_to_reset_reads_the_raw_timestamp(self):
        assert minutes_to_reset(_win("5h", 90.0, 30), NOW) == 30

    def test_a_window_with_no_timestamp_says_so(self):
        assert minutes_to_reset(_win("5h", 90.0), NOW) is None

    def test_a_window_about_to_reset_counts_as_empty(self):
        """Switching away from an account that is seconds from its whole
        allowance back is the wrong move."""
        assert effective_percent(_win("5h", 99.0, RESET_IMMINENT_MINUTES - 1), NOW) == 0.0

    def test_a_window_not_about_to_reset_counts_as_it_reads(self):
        assert effective_percent(_win("5h", 99.0, RESET_IMMINENT_MINUTES + 1), NOW) == 99.0


class TestHeadroom:
    def test_headroom_follows_the_binding_window(self):
        """Two windows, the fuller one is what stops you."""
        assert headroom(_state(_win("5h", 10.0), _win("7d", 80.0)), NOW) == 20.0

    def test_no_reading_no_headroom(self):
        assert headroom(UsageState(False, "Usage unavailable"), NOW) is None

    def test_an_imminent_reset_restores_the_headroom(self):
        state = _state(_win("5h", 99.0, 2), _win("7d", 30.0))
        assert headroom(state, NOW) == 70.0


class TestTokensLeft:
    def test_headroom_is_priced_on_the_binding_window(self):
        state = _state(_win("5h", 20.0), _win("7d", 60.0))
        rates = {("a@x.com", "7d"): 1_000_000.0, ("a@x.com", "5h"): 1.0}
        assert tokens_left(_acct("a@x.com"), state, rates, NOW) == 40_000_000.0

    def test_no_measurement_no_price(self):
        assert tokens_left(_acct("a@x.com"), _state(_win("5h", 20.0)), {}, NOW) is None


class TestTargetChoice:
    def _pick(self, states, rates=None, accounts=None, threshold=100.0):
        accounts = accounts or [_acct("active@x.com"), _acct("a@x.com"), _acct("b@x.com")]
        usage = {("claude", email): state for email, state in states.items()}
        return choose_auto_switch_target(
            provider="claude", accounts=accounts, active_ref="active@x.com",
            usage_by_account=usage, has_credentials=lambda a: True,
            threshold=threshold, per_point=rates, now=NOW,
        )

    def test_picks_the_account_with_the_most_room_not_the_first(self):
        """The old rule took whichever came first in the config file."""
        target = self._pick({
            "a@x.com": _state(_win("7d", 80.0)),
            "b@x.com": _state(_win("7d", 10.0)),
        })
        assert target.email == "b@x.com"

    def test_a_measured_account_is_ranked_by_tokens_not_percent(self):
        """40% of a small allowance loses to 20% of a big one."""
        target = self._pick(
            {"a@x.com": _state(_win("7d", 60.0)), "b@x.com": _state(_win("7d", 80.0))},
            rates={("a@x.com", "7d"): 1.0, ("b@x.com", "7d"): 1_000.0},
        )
        assert target.email == "b@x.com"

    def test_an_account_about_to_reset_becomes_the_best_choice(self):
        target = self._pick({
            "a@x.com": _state(_win("7d", 99.0, 2)),
            "b@x.com": _state(_win("7d", 50.0)),
        })
        assert target.email == "a@x.com"

    def test_an_exhausted_account_is_not_a_candidate(self):
        target = self._pick({
            "a@x.com": _state(_win("7d", 100.0)),
            "b@x.com": _state(_win("7d", 40.0)),
        })
        assert target.email == "b@x.com"

    def test_a_measured_account_beats_an_unmeasured_one(self):
        target = self._pick(
            {"a@x.com": _state(_win("7d", 5.0)), "b@x.com": _state(_win("7d", 50.0))},
            rates={("b@x.com", "7d"): 1_000.0},
        )
        assert target.email == "b@x.com"

    def test_an_account_with_no_reading_is_the_last_resort(self):
        target = self._pick({"a@x.com": UsageState(False, "Usage unavailable"),
                             "b@x.com": _state(_win("7d", 20.0))})
        assert target.email == "b@x.com"

    def test_an_unreadable_account_is_still_better_than_nothing(self):
        target = self._pick({"a@x.com": UsageState(False, "Usage unavailable"),
                             "b@x.com": _state(_win("7d", 100.0))})
        assert target.email == "a@x.com"

    def test_nothing_left_means_no_target(self):
        assert self._pick({"a@x.com": _state(_win("7d", 100.0)),
                           "b@x.com": _state(_win("7d", 100.0))}) is None

    def test_the_active_account_is_never_the_target(self):
        assert self._pick({"active@x.com": _state(_win("7d", 0.0))},
                          accounts=[_acct("active@x.com")]) is None


class TestCanAutoSwitch:
    def test_one_account_has_nowhere_to_go(self):
        assert can_auto_switch("codex", [_acct("only@x.com", provider="codex")]) is False

    def test_two_accounts_can_switch(self):
        accounts = [_acct("a@x.com", provider="codex"), _acct("b@x.com", provider="codex")]
        assert can_auto_switch("codex", accounts) is True

    def test_the_other_provider_does_not_count(self):
        accounts = [_acct("a@x.com", provider="codex"), _acct("b@x.com", provider="claude")]
        assert can_auto_switch("codex", accounts) is False
