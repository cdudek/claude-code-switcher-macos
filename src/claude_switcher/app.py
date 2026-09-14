"""macOS menu bar application using rumps."""

import tempfile
import threading
import time
from pathlib import Path

import rumps
from Foundation import NSOperationQueue

from claude_switcher import keychain
from claude_switcher.auto_switch import (
    account_key,
    choose_auto_switch_target,
    should_auto_switch,
)
from claude_switcher.codex_core import (
    check_codex_cli,
    import_current_codex_account,
    switch_codex_account,
    add_new_codex_account,
    remove_codex_account,
    CodexCredentialsExpiredError,
)
from claude_switcher.codex_usage import (
    fetch_active_codex_usage,
    fetch_codex_usage_for_account,
    codex_usage_state,
)
from claude_switcher.config import (
    load_accounts,
    get_active_account,
    load_settings,
    set_auto_switch_enabled,
    set_auto_update,
    set_icon,
    DEFAULT_CONFIG_PATH,
)
from claude_switcher import updater
from claude_switcher.icons import ICON_LABELS, icon_path, is_known
from claude_switcher.core import (
    check_claude_cli,
    import_current_account,
    switch_account,
    add_new_account,
    remove_saved_account,
    ClaudeCredentialsExpiredError,
)
from claude_switcher.usage import fetch_usage_for_account, fetch_active_usage, claude_usage_state
from claude_switcher.usage_state import UsageState


PROVIDER_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex CLI",
}
AUTO_SWITCH_COOLDOWN_SECONDS = 60
UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
UPDATE_CHECK_DELAY_SECONDS = 20  # let the app settle before touching the network

# rumps.alert maps its three buttons onto Cocoa's return codes: ok -> 1,
# other -> -1, cancel -> 0. Naming them keeps the dialog handler readable.
ALERT_OK, ALERT_OTHER, ALERT_CANCEL = 1, -1, 0

# The dialog writes its own copy rather than showing the exception text. The
# exception explains the cause to a developer; a person facing the dialog needs
# one sentence and two buttons. Showing both said the same thing twice, at length.
EXPIRED_SESSION_TITLE = "Signed out of {email}"
EXPIRED_SESSION_MESSAGE = (
    "Signing in to this account somewhere else ended this saved session. "
    "Only one sign-in stays valid at a time."
)


def is_expired_session(exc: BaseException) -> bool:
    """True when a switch failed because the saved sign-in was revoked.

    Worth its own dialog rather than the generic error alert: nothing is broken,
    the account just needs signing in again, and the user can act on it from here.
    """
    return isinstance(exc, (ClaudeCredentialsExpiredError, CodexCredentialsExpiredError))


def expired_session_action(button: int) -> str:
    """Map the dialog's button code to what the app should do next."""
    return {ALERT_OK: "signin", ALERT_OTHER: "remove"}.get(button, "cancel")


def _on_main_thread(fn):
    """Schedule fn() to run on the main thread via Cocoa's operation queue."""
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class ClaudeSwitcherApp(rumps.App):
    def __init__(self):
        self.config_path = DEFAULT_CONFIG_PATH
        chosen = load_settings(self.config_path).icon
        super().__init__("", icon=icon_path(chosen), template=True, quit_button=None)
        self._usage_cache: dict[tuple[str, str], str] = {}
        self._usage_state_cache: dict[tuple[str, str], UsageState] = {}
        self._usage_items: dict[tuple[str, str], rumps.MenuItem] = {}
        self._last_auto_switch_attempt: dict[str, float] = {}
        self._refresh_in_progress = False
        self._switch_in_progress: set[str] = set()
        self._first_launch()
        self._rebuild_menu()
        self._fetch_all_usage()
        self._auto_switch_timer = rumps.Timer(self._on_periodic_usage_refresh, 300)
        self._auto_switch_timer.start()
        self._update_in_progress = False
        self._update_timer = rumps.Timer(self._on_periodic_update_check, UPDATE_CHECK_INTERVAL_SECONDS)
        self._update_timer.start()
        # The first tick of a rumps.Timer fires immediately; defer the launch
        # check so a cold start is not competing with the usage fetches.
        threading.Timer(UPDATE_CHECK_DELAY_SECONDS, self._check_for_update, [False]).start()

    def _first_launch(self):
        """Import existing Claude and Codex accounts on first launch."""
        if self.config_path.exists():
            return

        imported_any = False
        claude_available = check_claude_cli()
        codex_available = check_codex_cli()

        if claude_available:
            imported = import_current_account(self.config_path)
            if imported:
                imported_any = True
                rumps.notification(
                    title="Claude Switcher",
                    subtitle="Claude account imported",
                    message=f"{imported.email} ({imported.subscription_type})",
                )

        if codex_available:
            try:
                imported = import_current_codex_account(self.config_path)
            except Exception as exc:
                imported = None
                rumps.notification(
                    title="Claude Switcher",
                    subtitle="Codex import skipped",
                    message=str(exc),
                )
            if imported:
                imported_any = True
                rumps.notification(
                    title="Claude Switcher",
                    subtitle="Codex account imported",
                    message=f"{imported.email} ({imported.subscription_type})",
                )

        if not imported_any and not claude_available and not codex_available:
            rumps.alert(
                title="CLI not found",
                message="Please install Claude Code or Codex CLI before using Claude Switcher.",
            )

    def _rebuild_menu(self):
        """Rebuild the menu from current account state."""
        accounts = load_accounts(self.config_path)
        self.menu.clear()
        self._usage_items = {}

        claude_accounts = [a for a in accounts if a.provider == "claude"]
        codex_accounts = [a for a in accounts if a.provider == "codex"]

        if claude_accounts:
            self._add_provider_section("claude", claude_accounts)
        if codex_accounts:
            if claude_accounts:
                self.menu.add(rumps.separator)
            self._add_provider_section("codex", codex_accounts)

        self.menu.add(rumps.separator)
        self._add_auto_switch_menu()
        self._add_icon_menu()
        self._add_update_menu()
        self.menu.add(rumps.MenuItem("\u271A  Add Claude account...", callback=self._on_add_claude_account))
        self.menu.add(rumps.MenuItem("\u271A  Add Codex account...", callback=self._on_add_codex_account))
        self.menu.add(rumps.MenuItem("\u21BB  Refresh usage", callback=self._on_refresh_usage))

        if accounts:
            remove_menu = rumps.MenuItem("\u2212  Remove account")
            for account in accounts:
                provider_label = "Claude" if account.provider == "claude" else "Codex"
                item = rumps.MenuItem(f"[{provider_label}] {account.email}", callback=self._on_remove_account)
                item._email = account.email
                item._provider = account.provider
                remove_menu.add(item)
            self.menu.add(remove_menu)

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("\u23FB  Quit", callback=rumps.quit_application))

    def _add_icon_menu(self):
        """Let the icon be changed from the bar it sits in.

        Every mark is a compromise between saying "switch", saying "AI" and
        staying legible at 22 points, and which compromise is right is a matter
        of taste and of what else is already in your menu bar. Cheaper to ship
        the set than to argue for one.
        """
        current = load_settings(self.config_path).icon
        menu = rumps.MenuItem("\u25C7  Icon")
        for slug, label in ICON_LABELS.items():
            item = rumps.MenuItem(label, callback=self._on_pick_icon)
            item.state = 1 if slug == current else 0
            item._slug = slug
            menu.add(item)
        self.menu.add(menu)

    def _on_pick_icon(self, sender):
        """Swap the menu bar icon and remember the choice."""
        slug = sender._slug
        if not is_known(slug):
            rumps.alert(
                title="Icon not available",
                message=f"This build does not ship an icon named {slug}.",
            )
            return
        set_icon(slug, self.config_path)
        # rumps redraws the status item when either property is assigned
        self.icon = icon_path(slug)
        self.template = True
        self._rebuild_menu()

    def _add_update_menu(self):
        version = updater.current_version()
        menu = rumps.MenuItem(f"\u2191  Updates (v{version})")
        menu.add(rumps.MenuItem("Check now...", callback=self._on_check_for_update))
        auto = rumps.MenuItem("Check automatically", callback=self._on_toggle_auto_update)
        auto.state = 1 if load_settings(self.config_path).auto_update else 0
        menu.add(auto)
        self.menu.add(menu)

    def _on_toggle_auto_update(self, sender):
        set_auto_update(not bool(sender.state), self.config_path)
        self._rebuild_menu()

    def _on_check_for_update(self, _):
        """Menu item: say something either way, because the user asked."""
        self._check_for_update(announce_up_to_date=True)

    def _on_periodic_update_check(self, _):
        self._check_for_update(announce_up_to_date=False)

    def _check_for_update(self, announce_up_to_date: bool):
        """Look for a newer release, and offer it.

        The background check is silent unless there is something to install; an
        explicit "Check now" says so either way. Never installs without asking -
        the download is unsigned, and swapping a running app under someone is not
        a thing to do quietly.
        """
        if self._update_in_progress:
            return
        if not announce_up_to_date and not load_settings(self.config_path).auto_update:
            return
        self._update_in_progress = True

        def _work():
            found = error = None
            try:
                found = updater.check_for_update()
            except Exception as exc:
                error = str(exc)

            def _finish():
                self._update_in_progress = False
                if error and announce_up_to_date:
                    rumps.alert(title="Could not check for updates", message=error)
                elif found:
                    self._offer_update(*found)
                elif announce_up_to_date:
                    rumps.alert(
                        title="You are up to date",
                        message=f"Claude Switcher {updater.current_version()} is the newest release.",
                    )

            _on_main_thread(_finish)

        threading.Thread(target=_work, daemon=True).start()

    def _offer_update(self, version: str, url: str, notes: str) -> None:
        body = f"Claude Switcher {version} is available. You have {updater.current_version()}."
        if notes.strip():
            body += "\n\n" + notes.strip()[:600]
        body += "\n\nInstalling replaces the app and restarts it. The version you have now goes to the Trash."
        if rumps.alert(title="Update available", message=body,
                       ok="Install and restart", cancel="Later") != ALERT_OK:
            return

        self._update_in_progress = True
        staging = Path(tempfile.mkdtemp(prefix="cs-update-"))

        def _work():
            error = staged = None
            try:
                staged = updater.download_update(url, staging)
            except Exception as exc:
                error = str(exc)

            def _finish():
                self._update_in_progress = False
                if error or staged is None:
                    updater.cleanup(staging)
                    rumps.alert(title="Update failed", message=error or "The download was unusable.")
                    return
                updater.install_update(staged)
                rumps.quit_application()

            _on_main_thread(_finish)

        threading.Thread(target=_work, daemon=True).start()

    def _add_provider_section(self, provider: str, accounts):
        header = rumps.MenuItem(f"\u2500\u2500 {PROVIDER_LABELS[provider]} \u2500\u2500")
        header.set_callback(None)
        self.menu.add(header)

        for account in accounts:
            has_creds = self._has_credentials(account)
            prefix = "\u25C9  " if account.active else "\u25CB  "
            if has_creds:
                label = f"{prefix}{account.email} ({account.subscription_type})"
                callback = (
                    self._on_claude_account_click
                    if provider == "claude"
                    else self._on_codex_account_click
                )
                item = rumps.MenuItem(label, callback=callback)
            else:
                item = rumps.MenuItem(f"{prefix}{account.email} (unavailable)", callback=None)
            item._email = account.email
            item._provider = provider
            self.menu.add(item)

            if has_creds:
                key = account_key(account)
                cached = self._usage_cache.get(key, "\u2022\u2022\u2022")
                usage_label = rumps.MenuItem(f"       \u2502  {cached}", callback=None)
                usage_label._email = account.email
                usage_label._provider = provider
                self._usage_items[key] = usage_label
                self.menu.add(usage_label)

    def _add_auto_switch_menu(self):
        settings = load_settings(self.config_path)
        auto_menu = rumps.MenuItem("Auto-switch")
        for provider in ("claude", "codex"):
            item = rumps.MenuItem(PROVIDER_LABELS[provider], callback=self._on_toggle_auto_switch)
            item._provider = provider
            item.state = 1 if settings.auto_switch.get(provider, False) else 0
            auto_menu.add(item)
        self.menu.add(auto_menu)

    def _has_credentials(self, account) -> bool:
        service = (
            f"claude-switcher:{account.email}"
            if account.provider == "claude"
            else f"codex-switcher:{account.email}"
        )
        return keychain.read_credentials(service) is not None

    def _on_claude_account_click(self, sender):
        self._switch_account("claude", sender._email)

    def _on_codex_account_click(self, sender):
        self._switch_account("codex", sender._email)

    def _switch_account(self, provider: str, email: str):
        active = get_active_account(self.config_path, provider=provider)
        if active and active.email == email:
            return
        if provider in self._switch_in_progress:
            rumps.notification(
                title="Claude Switcher",
                subtitle=f"{PROVIDER_LABELS[provider]} switch already running",
                message="Wait for the current switch to finish.",
            )
            return

        self._switch_in_progress.add(provider)

        def _switch():
            error = None
            expired = False
            try:
                if provider == "claude":
                    switch_account(email, self.config_path)
                else:
                    switch_codex_account(email, self.config_path)
            except Exception as exc:
                error = str(exc)
                expired = is_expired_session(exc)

            def _finish():
                self._switch_in_progress.discard(provider)
                if expired:
                    self._handle_expired_session(provider, email)
                    return
                if error:
                    rumps.alert(title="Error", message=error)
                else:
                    rumps.notification(
                        title="Claude Switcher",
                        subtitle=f"{PROVIDER_LABELS[provider]} account switched",
                        message=email,
                    )
                self._rebuild_menu()
                self._fetch_all_usage()

            _on_main_thread(_finish)

        threading.Thread(target=_switch, daemon=True).start()

    def _handle_expired_session(self, provider: str, email: str) -> None:
        """Offer the two things that actually fix a revoked sign-in.

        The old behaviour was a dead-end "Error" alert: the switch had silently
        left the account selected but unusable, and the only way out was to guess
        that Remove account followed by Add account was the fix.
        """
        choice = expired_session_action(
            rumps.alert(
                title=EXPIRED_SESSION_TITLE.format(email=email),
                message=EXPIRED_SESSION_MESSAGE,
                ok="Sign in again",
                other="Remove account",
                cancel="Cancel",
            )
        )
        if choice == "signin":
            if provider == "claude":
                self._on_add_claude_account(None)
            else:
                self._on_add_codex_account(None)
        elif choice == "remove":
            self._remove_account(provider, email)
        # "cancel" leaves the account in place; the previous session is untouched
        self._rebuild_menu()

    def _remove_account(self, provider: str, email: str) -> None:
        """Drop a saved account, reporting a failure instead of doing nothing."""
        try:
            if provider == "claude":
                remove_saved_account(email, self.config_path)
            else:
                remove_codex_account(email, self.config_path)
        except Exception as exc:
            rumps.alert(title="Could not remove account", message=f"{email}\n\n{exc}")
            return
        rumps.notification(
            title="Claude Switcher",
            subtitle=f"{PROVIDER_LABELS[provider]} account removed",
            message=email,
        )
        self._rebuild_menu()
        self._fetch_all_usage()

    def _on_add_claude_account(self, _):
        """Add a new Claude Code account via claude auth login."""
        if not check_claude_cli():
            rumps.alert(
                title="Claude CLI not found",
                message="Please install Claude Code before adding an account.",
            )
            return

        def _add():
            try:
                result = add_new_account(self.config_path)
                if result:
                    title, subtitle, message = (
                        "Claude Switcher",
                        "Claude account added",
                        f"{result.email} ({result.subscription_type})",
                    )
                else:
                    title, subtitle, message = (
                        "Claude Switcher",
                        "Cancelled",
                        "Login was cancelled or failed.",
                    )
            except Exception as exc:
                title, subtitle, message = "Claude Switcher", "Error", str(exc)

            def _finish():
                if subtitle == "Error":
                    # An alert cannot be silently dropped the way a notification can
                    rumps.alert(title="Could not add account", message=message)
                else:
                    rumps.notification(title=title, subtitle=subtitle, message=message)
                self._rebuild_menu()
                self._fetch_all_usage()

            _on_main_thread(_finish)

        threading.Thread(target=_add, daemon=True).start()

    def _on_add_codex_account(self, _):
        """Add a new Codex CLI account via codex login."""
        if not check_codex_cli():
            rumps.alert(
                title="Codex CLI not found",
                message="Please install Codex CLI before adding an account.",
            )
            return

        def _add():
            try:
                result = add_new_codex_account(self.config_path)
                if result:
                    title, subtitle, message = (
                        "Claude Switcher",
                        "Codex account added",
                        f"{result.email} ({result.subscription_type})",
                    )
                else:
                    title, subtitle, message = (
                        "Claude Switcher",
                        "Cancelled",
                        "Login was cancelled or failed.",
                    )
            except Exception as exc:
                title, subtitle, message = "Claude Switcher", "Error", str(exc)

            def _finish():
                if subtitle == "Error":
                    # An alert cannot be silently dropped the way a notification can
                    rumps.alert(title="Could not add account", message=message)
                else:
                    rumps.notification(title=title, subtitle=subtitle, message=message)
                self._rebuild_menu()
                self._fetch_all_usage()

            _on_main_thread(_finish)

        threading.Thread(target=_add, daemon=True).start()

    def _on_toggle_auto_switch(self, sender):
        provider = sender._provider
        settings = load_settings(self.config_path)
        enabled = not settings.auto_switch.get(provider, False)
        set_auto_switch_enabled(provider, enabled, self.config_path)
        self._rebuild_menu()
        rumps.notification(
            title="Claude Switcher",
            subtitle=f"Auto-switch {PROVIDER_LABELS[provider]}",
            message="Enabled" if enabled else "Disabled",
        )

    def _fetch_all_usage(self):
        """Fetch usage for all accounts in a background thread."""
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        accounts = load_accounts(self.config_path)
        active_by_provider = {
            "claude": get_active_account(self.config_path, provider="claude"),
            "codex": get_active_account(self.config_path, provider="codex"),
        }

        def _fetch():
            auto_switch_results = []
            try:
                for account in accounts:
                    key = account_key(account)
                    state = self._fetch_usage_state(account, active_by_provider.get(account.provider))
                    self._usage_state_cache[key] = state
                    self._usage_cache[key] = state.display

                for provider in ("claude", "codex"):
                    result = self._attempt_auto_switch(provider)
                    if result:
                        auto_switch_results.append(result)
            finally:
                def _finish():
                    self._refresh_in_progress = False
                    if any(result["status"] == "switched" for result in auto_switch_results):
                        self._rebuild_menu()
                    self._update_usage_labels()
                    for result in auto_switch_results:
                        self._notify_auto_switch_result(result)
                    if any(result["status"] == "switched" for result in auto_switch_results):
                        self._fetch_all_usage()

                _on_main_thread(_finish)

        threading.Thread(target=_fetch, daemon=True).start()

    def _fetch_usage_state(self, account, active_account) -> UsageState:
        try:
            if account.provider == "claude":
                usage = (
                    fetch_active_usage()
                    if active_account and active_account.email == account.email
                    else fetch_usage_for_account(account.email)
                )
                return claude_usage_state(usage)
            if account.provider == "codex":
                usage = (
                    fetch_active_codex_usage()
                    if active_account and active_account.email == account.email
                    else fetch_codex_usage_for_account(account.email)
                )
                return codex_usage_state(usage)
        except Exception:
            pass
        return UsageState(available=False, display="Usage unavailable")

    def _attempt_auto_switch(self, provider: str) -> dict | None:
        settings = load_settings(self.config_path)
        active = get_active_account(self.config_path, provider=provider)
        if not active:
            return None

        active_state = self._usage_state_cache.get(account_key(active))
        if not active_state or not should_auto_switch(
            active_state,
            settings.auto_switch.get(provider, False),
            settings.auto_switch_threshold,
        ):
            return None

        now = time.time()
        last_attempt = self._last_auto_switch_attempt.get(provider, 0)
        if now - last_attempt < AUTO_SWITCH_COOLDOWN_SECONDS:
            return None
        self._last_auto_switch_attempt[provider] = now

        accounts = load_accounts(self.config_path)
        target = choose_auto_switch_target(
            provider=provider,
            accounts=accounts,
            active_email=active.email,
            usage_by_account=self._usage_state_cache,
            has_credentials=self._has_credentials,
            threshold=settings.auto_switch_threshold,
        )
        if not target:
            return {"status": "no_target", "provider": provider, "email": active.email}

        try:
            if provider == "claude":
                switch_account(target.email, self.config_path)
            else:
                switch_codex_account(target.email, self.config_path)
        except Exception as exc:
            return {
                "status": "error",
                "provider": provider,
                "email": active.email,
                "message": str(exc),
            }

        return {"status": "switched", "provider": provider, "email": target.email}

    def _notify_auto_switch_result(self, result: dict):
        provider = result["provider"]
        label = PROVIDER_LABELS[provider]
        if result["status"] == "switched":
            rumps.notification(
                title="Claude Switcher",
                subtitle=f"Auto-switched {label}",
                message=result["email"],
            )
        elif result["status"] == "no_target":
            rumps.notification(
                title="Claude Switcher",
                subtitle=f"{label} limit reached",
                message="No available account to switch to.",
            )
        elif result["status"] == "error":
            rumps.notification(
                title="Claude Switcher",
                subtitle=f"{label} auto-switch failed",
                message=result.get("message", "Unknown error"),
            )

    def _update_usage_labels(self):
        """Update usage labels in the menu from cache."""
        for key, item in self._usage_items.items():
            usage_text = self._usage_cache.get(key, "Usage unavailable")
            item.title = f"       \u2502  {usage_text}"

    def _on_refresh_usage(self, _):
        """Refresh usage data for all accounts."""
        self._fetch_all_usage()

    def _on_periodic_usage_refresh(self, _):
        self._fetch_all_usage()

    def _on_remove_account(self, sender):
        """Remove a saved account."""
        email = sender._email
        provider = sender._provider
        active = get_active_account(self.config_path, provider=provider)

        if active and active.email == email:
            rumps.alert(
                title="Cannot remove",
                message=f"You cannot remove the active {PROVIDER_LABELS[provider]} account. Switch first.",
            )
            return

        self._remove_account(provider, email)


def main():
    ClaudeSwitcherApp().run()


if __name__ == "__main__":
    main()
