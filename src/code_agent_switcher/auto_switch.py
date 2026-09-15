"""Which account to move to when the one in use runs out.

The first version took the first account in the config file that was not
exhausted. That is a coin flip dressed as a decision: it ignored how much room
each account had, ignored that a window about to reset is as good as an empty
one, and ignored that a percentage point is worth a different number of tokens
on a Max plan than on a Team seat.

What it weighs now, in order:

1. A window that resets within a few minutes is treated as already reset.
   Switching away from an account that is about to be handed its whole
   allowance back is the wrong move.
2. Headroom on the *binding* window - the one closest to its limit, since that
   is the one that stops you, not the average of the two.
3. Headroom priced in tokens, when there is a measurement. usage_log records
   what a percentage point actually bought on that account, so 20% left on one
   plan can be ranked against 40% left on another instead of assumed equal.

An account with no reading at all is still a candidate, but the last one: it
might be fine, and a broken reading should not strand you with nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from code_agent_switcher.config import AccountInfo
from code_agent_switcher.usage_state import UsageState, UsageWindow

AccountKey = tuple[str, str]

# A window this close to resetting is counted as reset. Short enough that the
# wait is shorter than noticing and switching by hand.
RESET_IMMINENT_MINUTES = 10.0


def account_key(account: AccountInfo) -> AccountKey:
    """Return the stable cache key for an account.

    Keyed by ref, not email: two accounts can share an address.
    """
    return (account.provider, account.ref)


def should_auto_switch(active_usage: UsageState, enabled: bool, threshold: float) -> bool:
    """Return whether the active account should trigger an auto-switch."""
    return enabled and active_usage.available and active_usage.is_exhausted(threshold)


def minutes_to_reset(window: UsageWindow, now: datetime | None = None) -> float | None:
    """Minutes until the window resets, or None when it does not say."""
    if not window.resets_at:
        return None
    try:
        at = datetime.fromisoformat(str(window.resets_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    now = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (at - now).total_seconds() / 60


def effective_percent(window: UsageWindow, now: datetime | None = None) -> float:
    """The figure to judge by, counting an imminent reset as already done."""
    remaining = minutes_to_reset(window, now)
    if remaining is not None and remaining <= RESET_IMMINENT_MINUTES:
        return 0.0
    return float(window.percent)


def headroom(state: UsageState, now: datetime | None = None) -> float | None:
    """Percent left on the binding window, or None when there is no reading."""
    if not state or not state.available or not state.windows:
        return None
    worst = max(effective_percent(w, now) for w in state.windows)
    return max(0.0, 100.0 - worst)


def tokens_left(
    account: AccountInfo,
    state: UsageState,
    per_point: dict[tuple[str, str], float] | None,
    now: datetime | None = None,
) -> float | None:
    """Headroom priced in tokens, when this account has been measured.

    Uses the binding window's own rate, because a point of the 5-hour window and
    a point of the 7-day window are not the same amount of work.
    """
    room = headroom(state, now)
    if room is None or not per_point:
        return None
    binding = max(state.windows, key=lambda w: effective_percent(w, now), default=None)
    if binding is None:
        return None
    rate = per_point.get((account.email, binding.label))
    if not rate:
        return None
    return room * rate


def choose_auto_switch_target(
    provider: str,
    accounts: list[AccountInfo],
    active_ref: str,
    usage_by_account: dict[AccountKey, UsageState],
    has_credentials: Callable[[AccountInfo], bool],
    threshold: float = 100.0,
    per_point: dict[tuple[str, str], float] | None = None,
    now: datetime | None = None,
) -> AccountInfo | None:
    """Pick the account with the most room, not the first one in the file."""
    candidates = [
        account
        for account in accounts
        if account.provider == provider
        and account.ref != active_ref
        and has_credentials(account)
    ]

    ranked: list[tuple[float, float, AccountInfo]] = []
    unknown: list[AccountInfo] = []
    for account in candidates:
        state = usage_by_account.get(account_key(account))
        room = headroom(state, now)
        if room is None:
            unknown.append(account)
            continue
        if room <= 100.0 - threshold:
            continue  # already at or past the threshold itself
        priced = tokens_left(account, state, per_point, now)
        # Measured tokens first, raw headroom as the tie-break, so a measured
        # account never loses to an unmeasured one on a smaller percentage.
        ranked.append((priced if priced is not None else -1.0, room, account))

    if ranked:
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return ranked[0][2]
    if unknown:
        return unknown[0]
    return None


def can_auto_switch(provider: str, accounts: list[AccountInfo]) -> bool:
    """Auto-switch needs somewhere to go: two accounts on the same provider."""
    return sum(1 for a in accounts if a.provider == provider) >= 2
