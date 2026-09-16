"""What a running agent session does to an account switch.

Claude Code reads its credential once, at startup, and holds the token pair in
memory. Switching accounts replaces the Keychain item and ~/.claude.json, and a
session already running never looks again: it keeps spending the account you
thought you left. Measured on real samples - one account burned 8% of its
five-hour window across six polling intervals while the app had it marked
inactive.

The second half is worse and less obvious. Anthropic keeps one live token pair
per account and revokes the previous one on refresh, so when that old session
refreshes, Claude Code writes the new pair back into the same Keychain slot the
switcher just pointed at a different account. The live account silently flips
back.

Neither can be fixed from here - only Claude Code can re-read its own
credential. What this module does is let the app say so: which accounts are
being spent by something else, and how many sessions will ignore a switch.
"""

from __future__ import annotations

import subprocess
from datetime import timedelta

# A rise has to be attributable to the interval it sits in. The poll is every
# few minutes; a gap longer than this spans an unknown amount of work.
MAX_ATTRIBUTABLE_GAP = timedelta(minutes=30)

# "Is something else spending this right now" - a rise from this morning is not
# an answer to that.
RECENT_WINDOW = timedelta(hours=1)


def spent_elsewhere(samples, now=None, window: timedelta = RECENT_WINDOW) -> set[tuple[str, str]]:
    """Accounts whose usage rose recently while they were not the one in use.

    Judged from each sample's own `active` flag, not from which account is live
    now: an account that was live yesterday and spent tokens then is not being
    spent by anything else, and keying on the current live account reported
    three of four accounts as haunted.

    Only the last `window` counts. This answers "is something else spending
    this right now", and a rise from this morning does not.
    """
    if not samples:
        return set()
    cutoff = (now or max(s.at for s in samples)) - window

    by_key: dict[tuple[str, str, str], list] = {}
    for sample in samples:
        if sample.at < cutoff:
            continue
        for label in sample.windows:
            by_key.setdefault((sample.provider, sample.account, label), []).append(sample)

    out: set[tuple[str, str]] = set()
    for (provider, account, label), series in by_key.items():
        series.sort(key=lambda s: s.at)
        for before, after in zip(series, series[1:]):
            gap = after.at - before.at
            if not (timedelta(0) < gap <= MAX_ATTRIBUTABLE_GAP):
                continue
            # Neither end was the account in use, and it still went up.
            if before.active or after.active:
                continue
            if after.windows[label] > before.windows[label]:
                out.add((provider, account))
                break
    return out


def running_sessions(binary: str = "claude") -> int:
    """How many agent processes are running, and will therefore ignore a switch.

    Counts the CLI itself, not this app and not a shell that merely mentions it,
    because the number is shown to a person about to switch and an inflated one
    teaches them to ignore it.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", binary],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.split("\n") if line.strip()])
