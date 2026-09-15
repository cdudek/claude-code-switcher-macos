"""Account list persistence in ~/.config/claude-switcher/accounts.json.

The path keeps the old name on purpose. The app was renamed to Code Agent
Switcher; moving this file, or the `claude-switcher:<email>` Keychain items
beside it, would lose every saved account on the machines that already have
them. A product name is a label, these are addresses.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

from code_agent_switcher import backups


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "claude-switcher" / "accounts.json"
CONFIG_VERSION = 2
DEFAULT_PROVIDERS = ("claude", "codex")


@dataclass
class AccountInfo:
    email: str
    subscription_type: str
    org_name: str
    active: bool
    keychain_account: str
    oauth_account: dict | None = None
    provider: str = "claude"
    # One email can hold a team seat and a personal plan in different
    # organisations, and the CLI treats those as two sessions with two token
    # pairs. The email alone is therefore not an identity. `slot` disambiguates:
    # empty for the first account saved on an address - which keeps its historic
    # `claude-switcher:<email>` Keychain item untouched - and the organisation's
    # uuid for any later one. Nothing on an existing machine moves.
    slot: str = ""

    @property
    def ref(self) -> str:
        """The identity used for the Keychain item and everywhere in the app."""
        return f"{self.email}#{self.slot}" if self.slot else self.email

    @property
    def org_uuid(self) -> str:
        account = self.oauth_account or {}
        value = account.get("organizationUuid") if isinstance(account, dict) else None
        return value if isinstance(value, str) else ""


def ref_email(ref: str) -> str:
    """The address part of a ref, for display and for validation."""
    return ref.split("#", 1)[0]


def assign_slot(account: AccountInfo, existing: list[AccountInfo]) -> str:
    """The slot a new account should take.

    First account on an address keeps the bare email, so every install that
    exists today is untouched. A second account on the same address in a
    different organisation takes that organisation's uuid.
    """
    same_address = [
        a for a in existing
        if a.provider == account.provider and a.email == account.email
    ]
    if not same_address:
        return ""
    for other in same_address:
        if other.org_uuid == account.org_uuid:
            return other.slot  # the same account again; keep where it lives
    return account.org_uuid or "personal"


@dataclass
class AppSettings:
    auto_switch: dict[str, bool] = field(
        default_factory=lambda: {provider: False for provider in DEFAULT_PROVIDERS}
    )
    auto_switch_threshold: float = 100.0
    auto_update: bool = True


def _default_settings_dict() -> dict:
    return asdict(AppSettings())


def _read_config_data(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Read raw config JSON. Returns a valid empty config shape on failure."""
    if not path.exists():
        return {"version": CONFIG_VERSION, "settings": _default_settings_dict(), "accounts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": CONFIG_VERSION, "settings": _default_settings_dict(), "accounts": []}
    if not isinstance(data, dict):
        return {"version": CONFIG_VERSION, "settings": _default_settings_dict(), "accounts": []}
    return data


def _write_config_data(data: dict, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write raw config JSON with private file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.setdefault("version", CONFIG_VERSION)
    data.setdefault("settings", _default_settings_dict())
    data.setdefault("accounts", [])
    backups.snapshot(path)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def _settings_from_dict(data: dict | None) -> AppSettings:
    defaults = AppSettings()
    if not isinstance(data, dict):
        return defaults

    auto_switch = dict(defaults.auto_switch)
    raw_auto_switch = data.get("auto_switch")
    if isinstance(raw_auto_switch, dict):
        for provider, enabled in raw_auto_switch.items():
            if isinstance(provider, str):
                auto_switch[provider] = bool(enabled)

    threshold = defaults.auto_switch_threshold
    try:
        threshold = float(data.get("auto_switch_threshold", threshold))
    except (TypeError, ValueError):
        threshold = defaults.auto_switch_threshold

    auto_update = data.get("auto_update", defaults.auto_update)
    if not isinstance(auto_update, bool):
        auto_update = defaults.auto_update

    return AppSettings(
        auto_switch=auto_switch,
        auto_switch_threshold=threshold,
        auto_update=auto_update,
    )


def load_accounts(path: Path = DEFAULT_CONFIG_PATH) -> list[AccountInfo]:
    """Load accounts from JSON file. Returns empty list if file doesn't exist."""
    data = _read_config_data(path)
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list):
        return []

    loaded = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        fields = {k: v for k, v in acc.items() if k in AccountInfo.__dataclass_fields__}
        try:
            loaded.append(AccountInfo(**fields))
        except TypeError:
            continue
    return loaded


def save_accounts(accounts: list[AccountInfo], path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Save accounts to JSON file, preserving app settings."""
    data = _read_config_data(path)
    data["version"] = CONFIG_VERSION
    data["settings"] = asdict(_settings_from_dict(data.get("settings")))
    data["accounts"] = [asdict(acc) for acc in accounts]
    _write_config_data(data, path)


def add_account(account: AccountInfo, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Add or update an account, matched by address AND organisation.

    Matching on the address alone is what made a team seat and a personal plan
    on one email replace each other.
    """
    accounts = load_accounts(path)
    if not account.slot:
        account.slot = assign_slot(account, accounts)
    accounts = [
        a for a in accounts
        if not (a.provider == account.provider and a.ref == account.ref)
    ]
    accounts.append(account)
    save_accounts(accounts, path)


def remove_account(ref: str, path: Path = DEFAULT_CONFIG_PATH, provider: str = "claude") -> None:
    """Remove one account by ref and provider."""
    accounts = load_accounts(path)
    accounts = [a for a in accounts if not (a.ref == ref and a.provider == provider)]
    save_accounts(accounts, path)


def find_account(
    accounts: list[AccountInfo], provider: str, ref: str
) -> AccountInfo | None:
    return next((a for a in accounts if a.provider == provider and a.ref == ref), None)


# Work plans first, then personal, then whatever we do not recognise. The app
# shows both providers' lists in two places and they must agree.
PLAN_RANK = {"enterprise": 0, "team": 1, "max": 2, "pro": 3, "free": 4}
UNKNOWN_PLAN_RANK = 5


def plan_rank(subscription_type: str | None) -> int:
    return PLAN_RANK.get((subscription_type or "").strip().lower(), UNKNOWN_PLAN_RANK)


def sort_accounts(accounts: list[AccountInfo]) -> list[AccountInfo]:
    """A stable order: work plans first, and one email's accounts kept together.

    The list used to come back in config order, which changes every time an
    account is switched or re-added - so the row you were about to click moved.
    Sorting on the email's BEST plan rather than each row's own keeps a personal
    and a team seat on the same address adjacent instead of splitting them
    across the two tiers.
    """
    best_for_email: dict[str, int] = {}
    for account in accounts:
        rank = plan_rank(account.subscription_type)
        email = account.email.lower()
        best_for_email[email] = min(best_for_email.get(email, UNKNOWN_PLAN_RANK), rank)
    return sorted(
        accounts,
        key=lambda a: (
            best_for_email[a.email.lower()],
            a.email.lower(),
            plan_rank(a.subscription_type),
        ),
    )


def get_active_account(
    path: Path = DEFAULT_CONFIG_PATH, provider: str = "claude"
) -> AccountInfo | None:
    """Return the active account for a provider, or None."""
    for acc in load_accounts(path):
        if acc.active and acc.provider == provider:
            return acc
    return None


def set_active_account(
    ref: str, path: Path = DEFAULT_CONFIG_PATH, provider: str = "claude"
) -> None:
    """Set an account active within a provider, deactivating only that provider."""
    accounts = load_accounts(path)
    for acc in accounts:
        if acc.provider == provider:
            acc.active = (acc.ref == ref)
    save_accounts(accounts, path)


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> AppSettings:
    """Load persisted app settings with defaults for missing fields."""
    data = _read_config_data(path)
    return _settings_from_dict(data.get("settings"))


def save_settings(settings: AppSettings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persist app settings without modifying accounts."""
    data = _read_config_data(path)
    data["version"] = CONFIG_VERSION
    data["settings"] = asdict(settings)
    data["accounts"] = data.get("accounts", [])
    _write_config_data(data, path)


def is_auto_switch_enabled(provider: str, path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """Return whether auto-switch is enabled for a provider."""
    settings = load_settings(path)
    return bool(settings.auto_switch.get(provider, False))


def set_auto_update(enabled: bool, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Turn the update check on or off."""
    settings = load_settings(path)
    settings.auto_update = bool(enabled)
    save_settings(settings, path)


def set_auto_switch_enabled(
    provider: str, enabled: bool, path: Path = DEFAULT_CONFIG_PATH
) -> None:
    """Enable or disable auto-switch for one provider."""
    settings = load_settings(path)
    settings.auto_switch[provider] = bool(enabled)
    save_settings(settings, path)
