"""Fetch Claude API usage stats via the OAuth usage endpoint."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from code_agent_switcher import keychain
from code_agent_switcher.usage_state import UsageState, UsageWindow
from code_agent_switcher.core import snapshot_service

USAGE_URL = "https://api.anthropic.com/oauth/usage"
USAGE_TIMEOUT_SECONDS = 10


def _extract_token(creds_json: str) -> str | None:
    """Extract the OAuth access token from a credentials JSON blob."""
    try:
        data = json.loads(creds_json)
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, AttributeError):
        return None


def _is_expired(creds_json: str) -> bool:
    """True when the blob's accessToken is past its expiresAt."""
    try:
        expires = json.loads(creds_json).get("claudeAiOauth", {}).get("expiresAt")
    except (json.JSONDecodeError, AttributeError):
        return False
    if not isinstance(expires, (int, float)):
        return False
    return expires / 1000 <= datetime.now(timezone.utc).timestamp()


def _request_usage(token: str) -> tuple[int, dict | None]:
    """Ask the usage endpoint. Returns (status, payload); status 0 means no answer."""
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Accept": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "Authorization": f"Bearer {token}",
            "User-Agent": "claude-code/2.1.11",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=USAGE_TIMEOUT_SECONDS) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return 0, None


def _refresh_stored(service: str, creds: str) -> str | None:
    """Mint a new token pair for a stored blob and write it back where it came from.

    A snapshot nobody has switched to in eight hours holds a dead accessToken, and
    nothing else in the app refreshes it - which is why an idle account's usage row
    went blank overnight. The refresh rotates the pair server-side, so if this blob
    is also the pair Claude Code is using right now, the live entry has to be moved
    with it or the running session is the thing we just revoked.
    """
    # Imported here: core reads config and Keychain at import time in some paths,
    # and usage is imported by the menu build.
    from code_agent_switcher.core import ClaudeCredentialsExpiredError, refresh_claude_credentials

    try:
        refreshed = refresh_claude_credentials(creds)
    except ClaudeCredentialsExpiredError:
        return None
    if not refreshed:
        return None

    account = keychain.read_account_attribute(service) or service
    keychain.write_credentials(service, account, refreshed)

    if service != keychain.CLAUDE_SERVICE:
        live = keychain.read_credentials(keychain.CLAUDE_SERVICE)
        if live and _extract_token(live) == _extract_token(creds):
            live_account = keychain.read_account_attribute(keychain.CLAUDE_SERVICE) or service
            keychain.write_credentials(keychain.CLAUDE_SERVICE, live_account, refreshed)
    return refreshed


def fetch_usage_detail(service: str) -> tuple[dict | None, str | None]:
    """Fetch usage, and say why when there is none.

    The menu used to print a bare "Usage unavailable" for six different causes -
    no saved credentials, a revoked sign-in, a rate limit, a timeout - which made
    the one case that needs the user to act indistinguishable from a blip.

    Response format on success:
        {
            "five_hour": {"utilization": 42.5, "resets_at": "..."},
            "seven_day": {"utilization": 18.3, "resets_at": "..."}
        }
    """
    creds = keychain.read_credentials(service)
    if not creds:
        return None, "no saved sign-in"

    token = _extract_token(creds)
    if not token:
        return None, "saved sign-in has no token"

    if _is_expired(creds):
        creds = _refresh_stored(service, creds) or creds
        token = _extract_token(creds) or token

    status, payload = _request_usage(token)
    if payload is not None:
        return payload, None
    # Only 401 says the token is the problem. Refreshing on a network blip would
    # rotate the pair for nothing and throw away a refresh token that still works.
    if status != 401:
        return None, _status_reason(status)

    refreshed = _refresh_stored(service, creds)
    if not refreshed:
        return None, "signed out, sign in again"
    new_token = _extract_token(refreshed)
    if not new_token:
        return None, "signed out, sign in again"
    status, payload = _request_usage(new_token)
    if payload is not None:
        return payload, None
    return None, _status_reason(status)


def _status_reason(status: int) -> str:
    if status == 0:
        return "no answer from the API"
    if status == 401:
        return "signed out, sign in again"
    if status == 429:
        return "rate limited, try later"
    if 500 <= status < 600:
        return f"API error {status}"
    return f"unexpected reply {status}"


def fetch_usage(service: str) -> dict | None:
    """Fetch usage for a Keychain service. Returns parsed JSON or None on failure."""
    return fetch_usage_detail(service)[0]


def fetch_usage_for_account(ref: str) -> dict | None:
    """Fetch usage for a saved account by its ref."""
    return fetch_usage(snapshot_service(ref))


def fetch_usage_detail_for_account(ref: str) -> tuple[dict | None, str | None]:
    return fetch_usage_detail(snapshot_service(ref))


def fetch_active_usage() -> dict | None:
    """Fetch usage for the currently active Claude Code session."""
    return fetch_usage(keychain.CLAUDE_SERVICE)


def fetch_active_usage_detail() -> tuple[dict | None, str | None]:
    return fetch_usage_detail(keychain.CLAUDE_SERVICE)


def _format_reset_delta(resets_at: str) -> str:
    """Convert an ISO 8601 resets_at timestamp to a human-readable relative time."""
    if not isinstance(resets_at, str):
        return "?"
    try:
        # Strip fractional seconds for simpler parsing
        cleaned = resets_at.replace("Z", "+00:00")
        reset_dt = datetime.fromisoformat(cleaned)
        now = datetime.now(timezone.utc)
        diff = int((reset_dt - now).total_seconds())

        if diff <= 0:
            return "now"

        days = diff // 86400
        hours = (diff % 86400) // 3600
        minutes = (diff % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except (ValueError, TypeError):
        return "?"


def claude_usage_state(usage: dict | None, reason: str | None = None) -> UsageState:
    """Convert Claude usage data into a normalized usage state."""
    if not usage:
        return UsageState(available=False, display="Usage unavailable", reason=reason)

    parts = []
    windows = []
    five_h = usage.get("five_hour", {})
    seven_d = usage.get("seven_day", {})

    for label, window in (("5h", five_h), ("7d", seven_d)):
        if not isinstance(window, dict) or "utilization" not in window:
            continue
        try:
            percent = float(window["utilization"])
        except (TypeError, ValueError):
            continue

        # resets_at is present but null on a window that is not running - an
        # account you have not used today has nothing counting down. The key
        # test said "present", which it is, and formatting None threw, which
        # the caller swallowed into a bare "Usage unavailable". That is why an
        # idle account showed no reading while the API was answering 200.
        reset = _format_reset_delta(window["resets_at"]) if window.get("resets_at") else None
        reset_suffix = f" ({reset})" if reset else ""
        parts.append(f"{label} {percent:.0f}%{reset_suffix}")
        windows.append(UsageWindow(label=label, percent=percent, resets_in=reset,
                                   resets_at=window.get("resets_at")))

    if not parts:
        return UsageState(available=False, display="Usage unavailable",
                          reason=reason or "the API returned no windows")

    return UsageState(available=True, display=" | ".join(parts), windows=tuple(windows))


def format_usage(usage: dict | None) -> str:
    """Format usage data into a readable string."""
    return claude_usage_state(usage).display
