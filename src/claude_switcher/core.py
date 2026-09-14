"""Business logic for account management."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from claude_switcher import keychain
from claude_switcher.config import (
    AccountInfo,
    add_account,
    get_active_account,
    load_accounts,
    remove_account,
    set_active_account,
    DEFAULT_CONFIG_PATH,
)

CLAUDE_SERVICE = keychain.CLAUDE_SERVICE
CLAUDE_STATE_FILE = Path.home() / ".claude.json"

CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_OAUTH_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_SESSION_EXPIRED_MESSAGE = (
    "This saved Claude session is no longer valid. Anthropic revokes an account's "
    "tokens when that account signs in again, so a /login or an Add Account since "
    "this snapshot was taken has invalidated it. Remove the account and add it again."
)


class ClaudeCredentialsExpiredError(RuntimeError):
    """Raised when saved Claude tokens have been revoked or already consumed."""

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(email: str) -> str:
    """Validate email before using it in Keychain service names."""
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise RuntimeError(f"Invalid email format: {email}")
    return email


def _oauth_section(blob: str | None) -> dict | None:
    """Return the claudeAiOauth object from a credential blob, or None if unreadable.

    Older blobs stored the token fields at the top level, so fall back to the
    whole object when the claudeAiOauth key is absent.
    """
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    section = data.get("claudeAiOauth", data)
    return section if isinstance(section, dict) else None


def has_valid_tokens(blob: str | None) -> bool:
    """True if a credential blob carries a usable OAuth token pair.

    Claude Code writes the Keychain entry in stages: for up to a second after
    login the record exists with empty accessToken/refreshToken strings. Saving
    that husk as an account snapshot makes a later switch to it fail with
    "Login expired - Please run /login", because the empty tokens are copied
    verbatim into the live Keychain entry.
    """
    section = _oauth_section(blob)
    if section is None:
        return False
    return bool(section.get("accessToken")) and bool(section.get("refreshToken"))


def _carry_over_mcp_oauth(target_creds: str, live_creds: str | None) -> str:
    """Preserve machine-local MCP OAuth tokens across an account switch.

    The Keychain blob holds two unrelated things: claudeAiOauth (the Claude
    account) and mcpOAuth (tokens for MCP servers such as Vercel or Notion).
    MCP tokens authenticate the machine's user to those third parties and have
    nothing to do with which Claude account is active, so overwriting the blob
    wholesale silently logs every MCP server out. Entries already present in the
    target win, so a snapshot that carries its own MCP tokens keeps them.
    """
    try:
        target = json.loads(target_creds)
        live = json.loads(live_creds) if live_creds else {}
    except (json.JSONDecodeError, TypeError):
        return target_creds
    if not isinstance(target, dict) or not isinstance(live, dict):
        return target_creds
    live_mcp = live.get("mcpOAuth")
    if not isinstance(live_mcp, dict):
        return target_creds
    target_mcp = target.get("mcpOAuth")
    target["mcpOAuth"] = {**live_mcp, **(target_mcp if isinstance(target_mcp, dict) else {})}
    return json.dumps(target)


def _mcp_section(blob: str | None) -> dict:
    """Return the mcpOAuth object from a credential blob, or an empty dict."""
    if not blob:
        return {}
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    mcp = data.get("mcpOAuth")
    return mcp if isinstance(mcp, dict) else {}


def _restore_mcp_oauth(preserved_mcp: dict, email: str, keychain_account: str) -> None:
    """Put the machine's MCP tokens back after a logout/login cycle wiped them.

    `claude auth logout` plus the Keychain cleanup in add_new_account() delete
    the whole entry, and the blob Claude Code writes on the next login carries
    no mcpOAuth section — so adding an account silently logs every MCP server
    out. Write the preserved tokens into both the live entry and the snapshot
    that was just taken from it.
    """
    if not preserved_mcp:
        return
    live = keychain.read_credentials(CLAUDE_SERVICE)
    if not has_valid_tokens(live):
        return
    merged = _carry_over_mcp_oauth(live, json.dumps({"mcpOAuth": preserved_mcp}))
    keychain.write_credentials(CLAUDE_SERVICE, keychain_account, merged)
    keychain.write_credentials(f"claude-switcher:{email}", keychain_account, merged)


def refresh_claude_credentials(creds: str) -> str | None:
    """Return the blob with a freshly minted token pair, or None on a transient failure.

    Saved credentials rot in a way no local check can see: Anthropic revokes an
    account's token pair when that account authenticates again, so a snapshot can
    carry a shape-valid, not-yet-expired accessToken that the API answers with 401.
    Refreshing at switch time both proves the saved session is still real and keeps
    the snapshot alive, which is the same treatment the Codex path already gets.

    Raises ClaudeCredentialsExpiredError when the server rejects the refresh token
    outright — that snapshot is dead and only a fresh login can replace it.
    """
    section = _oauth_section(creds)
    if not section or not section.get("refreshToken"):
        return None

    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": section["refreshToken"],
        "client_id": CLAUDE_OAUTH_CLIENT_ID,
    }).encode("utf-8")
    req = Request(CLAUDE_OAUTH_TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            refreshed = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if exc.code in {400, 401} and (
            "invalid_grant" in text or "not found or invalid" in text
        ):
            raise ClaudeCredentialsExpiredError(CLAUDE_SESSION_EXPIRED_MESSAGE) from exc
        return None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    if not refreshed.get("access_token"):
        return None

    try:
        blob = json.loads(creds)
    except (json.JSONDecodeError, TypeError):
        return None
    target = blob.get("claudeAiOauth") if isinstance(blob.get("claudeAiOauth"), dict) else blob
    target["accessToken"] = refreshed["access_token"]
    if refreshed.get("refresh_token"):
        target["refreshToken"] = refreshed["refresh_token"]
    if refreshed.get("expires_in"):
        target["expiresAt"] = int(time.time() * 1000) + int(refreshed["expires_in"]) * 1000
    return json.dumps(blob)


def _read_oauth_account() -> dict | None:
    """Read the oauthAccount object from ~/.claude.json."""
    try:
        data = json.loads(CLAUDE_STATE_FILE.read_text(encoding="utf-8"))
        return data.get("oauthAccount")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_oauth_account(oauth_account: dict) -> None:
    """Write the oauthAccount object into ~/.claude.json (merge, not overwrite)."""
    try:
        data = json.loads(CLAUDE_STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    data["oauthAccount"] = oauth_account
    CLAUDE_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")


_EXTRA_PATHS = [
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
]


def _find_claude() -> str | None:
    """Find the claude binary, checking common install locations beyond PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for d in _EXTRA_PATHS:
        candidate = d / "claude"
        if candidate.is_file():
            return str(candidate)
    return None


def check_claude_cli() -> bool:
    """Check if the claude CLI is available."""
    return _find_claude() is not None


def _claude_cmd() -> str:
    """Return the path to the claude binary, or 'claude' as fallback."""
    return _find_claude() or "claude"


def get_auth_status() -> dict | None:
    """Run `claude auth status --json` and return parsed JSON, or None on failure."""
    result = subprocess.run(
        [_claude_cmd(), "auth", "status", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def run_auth_logout() -> None:
    """Run `claude auth logout`."""
    subprocess.run([_claude_cmd(), "auth", "logout"], capture_output=True, text=True)


def run_auth_login() -> bool:
    """Run `claude auth login`. Returns True if successful (exit code 0)."""
    result = subprocess.run([_claude_cmd(), "auth", "login"])
    return result.returncode == 0


def import_current_account(config_path: Path = DEFAULT_CONFIG_PATH) -> AccountInfo | None:
    """Import the currently logged-in Claude Code account. Returns AccountInfo or None."""
    # Retry keychain read — after login the entry can exist with empty tokens
    creds = None
    for _ in range(5):
        candidate = keychain.read_credentials(CLAUDE_SERVICE)
        if has_valid_tokens(candidate):
            creds = candidate
            break
        time.sleep(1)
    if not creds:
        return None

    acct_attr = keychain.read_account_attribute(CLAUDE_SERVICE) or "unknown"

    status = get_auth_status()
    if status and status.get("email"):
        email = status["email"]
        sub_type = status.get("subscriptionType", "unknown")
        org_name = status.get("orgName", "")
    else:
        try:
            email = json.loads(creds).get("email", "unknown@unknown")
        except (json.JSONDecodeError, AttributeError):
            return None
        sub_type = "unknown"
        org_name = ""

    _validate_email(email)
    keychain.write_credentials(f"claude-switcher:{email}", acct_attr, creds)

    oauth_account = _read_oauth_account()

    account = AccountInfo(
        email=email,
        subscription_type=sub_type,
        org_name=org_name,
        active=True,
        keychain_account=acct_attr,
        oauth_account=oauth_account,
        provider="claude",
    )
    add_account(account, config_path)
    set_active_account(email, config_path, provider="claude")
    return account


def switch_account(target_email: str, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Switch to a different account. Saves current credentials first."""
    active = get_active_account(config_path)

    live_creds = keychain.read_credentials(CLAUDE_SERVICE)

    if active:
        # Never overwrite a good snapshot with a half-written one
        if has_valid_tokens(live_creds):
            keychain.write_credentials(
                f"claude-switcher:{active.email}", active.keychain_account, live_creds
            )
        # Save current oauthAccount state from ~/.claude.json
        current_oauth = _read_oauth_account()
        if current_oauth:
            active.oauth_account = current_oauth
            add_account(active, config_path)

    _validate_email(target_email)
    target_creds = keychain.read_credentials(f"claude-switcher:{target_email}")
    if not target_creds:
        raise RuntimeError(f"Credentials not found in Keychain for {target_email}")
    if not has_valid_tokens(target_creds):
        raise RuntimeError(
            f"Saved credentials for {target_email} are incomplete — they carry no "
            "access token. Remove the account and add it again."
        )

    accounts = load_accounts(config_path)
    target_account = next(
        (a for a in accounts if a.email == target_email and a.provider == "claude"), None
    )
    if not target_account:
        raise RuntimeError(f"Account {target_email} not found in config")

    # Prove the saved session is still real, and refresh the snapshot while we are
    # here. A transient network failure falls through to the stored tokens.
    refreshed = refresh_claude_credentials(target_creds)
    if refreshed:
        target_creds = refreshed
        keychain.write_credentials(
            f"claude-switcher:{target_email}", target_account.keychain_account, refreshed
        )

    target_creds = _carry_over_mcp_oauth(target_creds, live_creds)
    keychain.write_credentials(CLAUDE_SERVICE, target_account.keychain_account, target_creds)

    # Restore target's oauthAccount into ~/.claude.json
    if target_account.oauth_account:
        _write_oauth_account(target_account.oauth_account)

    set_active_account(target_email, config_path, provider="claude")


def add_new_account(config_path: Path = DEFAULT_CONFIG_PATH) -> AccountInfo | None:
    """Add a new account via claude auth login. Returns AccountInfo or None if cancelled."""
    active = get_active_account(config_path)
    current_creds = keychain.read_credentials(CLAUDE_SERVICE)
    # The logout + Keychain cleanup below drop the machine's MCP server tokens
    # along with the account; they are not account-specific, so keep them.
    preserved_mcp = _mcp_section(current_creds)
    if active:
        if has_valid_tokens(current_creds):
            keychain.write_credentials(
                f"claude-switcher:{active.email}", active.keychain_account, current_creds
            )

    run_auth_logout()

    # Ensure all "Claude Code-credentials" entries are gone before login.
    # claude auth logout may not clean up the Keychain properly, and leftover
    # entries cause security find-generic-password -w to return the OLD token
    # instead of the freshly-issued one after login.
    while keychain.delete_credentials(CLAUDE_SERVICE):
        pass

    if not run_auth_login():
        if active:
            prev_creds = keychain.read_credentials(f"claude-switcher:{active.email}")
            if has_valid_tokens(prev_creds):
                keychain.write_credentials(CLAUDE_SERVICE, active.keychain_account, prev_creds)
        return None

    try:
        account = import_current_account(config_path)
        if account:
            _restore_mcp_oauth(preserved_mcp, account.email, account.keychain_account)
        return account
    except Exception:
        # Login succeeded but import failed — restore previous account
        if active:
            prev_creds = keychain.read_credentials(f"claude-switcher:{active.email}")
            if has_valid_tokens(prev_creds):
                keychain.write_credentials(CLAUDE_SERVICE, active.keychain_account, prev_creds)
        return None


def remove_saved_account(email: str, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Remove a saved account from config and Keychain."""
    keychain.delete_credentials(f"claude-switcher:{email}")
    remove_account(email, config_path)
