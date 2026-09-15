"""macOS menu bar application using rumps."""

import tempfile
import subprocess
import threading
import time
from pathlib import Path

import rumps
from Foundation import NSOperationQueue

from code_agent_switcher import keychain
from code_agent_switcher.auto_switch import (
    can_auto_switch,
    account_key,
    choose_auto_switch_target,
    should_auto_switch,
)
from code_agent_switcher.codex_core import (
    check_codex_cli,
    live_codex_email,
    import_current_codex_account,
    switch_codex_account,
    add_new_codex_account,
    remove_codex_account,
    CodexCredentialsExpiredError,
)
from code_agent_switcher.codex_usage import (
    fetch_active_codex_usage,
    fetch_codex_usage_for_account,
    codex_usage_state,
)
from code_agent_switcher.config import (
    load_accounts,
    get_active_account,
    set_active_account,
    load_settings,
    sort_accounts,
    set_auto_switch_enabled,
    set_auto_update,
    DEFAULT_CONFIG_PATH,
)
from code_agent_switcher import updater
from code_agent_switcher.icons import icon_path
from code_agent_switcher.core import (
    check_claude_cli,
    live_claude_email,
    import_current_account,
    switch_account,
    add_new_account,
    remove_saved_account,
    ClaudeCredentialsExpiredError,
)
from code_agent_switcher.usage import fetch_usage_detail_for_account, fetch_active_usage_detail, claude_usage_state
from code_agent_switcher.usage_state import UsageState
from code_agent_switcher.ledger import load_records, since_days, tokens_between
from code_agent_switcher.report import write_report
from datetime import timedelta
from code_agent_switcher import ui, usage_log
from code_agent_switcher.accounts_window import AccountsWindowController

import AppKit
import objc
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

# While the panel is open the reading is worth keeping current; while it is shut
# nobody is looking, and polling four accounts every five minutes for an empty
# screen is four requests a minute nobody asked for.
OPEN_POLL_SECONDS = 15.0
# The budget rates change slowly; recomputing them per decision would re-read a
# week of transcripts for nothing.
BUDGET_RATE_TTL_SECONDS = 3600.0
BACKGROUND_POLL_SECONDS = 300.0

# A month is long enough to see a trend and short enough to read in a few
# seconds; the whole transcript tree here is 1.3 GB.
REPORT_DAYS = 30


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


class _MenuWatcher(AppKit.NSObject):
    """Tells the app when the panel is on screen, so it can poll only then."""

    def initWithApp_(self, app):
        self = objc.super(_MenuWatcher, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def menuWillOpen_(self, menu):
        self._app.on_menu_open()

    def menuDidClose_(self, menu):
        self._app.on_menu_close()


class ClaudeSwitcherApp(rumps.App):
    def __init__(self):
        self.config_path = DEFAULT_CONFIG_PATH
        super().__init__("", icon=icon_path(), template=True, quit_button=None)
        self._usage_cache: dict[tuple[str, str], str] = {}
        self._usage_state_cache: dict[tuple[str, str], UsageState] = {}
        self._usage_items: dict[tuple[str, str], object] = {}
        self._card_meta: dict[tuple[str, str], tuple[str, str, bool]] = {}
        self._menu_open = False
        self._open_timer = None
        self._accounts_window = AccountsWindowController(self)
        self._watcher = _MenuWatcher.alloc().initWithApp_(self)
        self._last_auto_switch_attempt: dict[str, float] = {}
        self._refresh_in_progress = False
        self._switch_in_progress: set[str] = set()
        self._first_launch()
        self._rebuild_menu()
        self._fetch_all_usage()
        # Kept only for auto-switch, which has to notice a spent limit while
        # nobody is looking at the panel.
        self._auto_switch_timer = rumps.Timer(
            self._on_periodic_usage_refresh, BACKGROUND_POLL_SECONDS
        )
        self._auto_switch_timer.start()
        self._update_in_progress = False
        self._report_in_progress = False
        self._budget_rates_cache: dict = {}
        self._budget_rates_at = 0.0
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
                    title="Code Agent Switcher",
                    subtitle="Claude account imported",
                    message=f"{imported.email} ({imported.subscription_type})",
                )

        if codex_available:
            try:
                imported = import_current_codex_account(self.config_path)
            except Exception as exc:
                imported = None
                rumps.notification(
                    title="Code Agent Switcher",
                    subtitle="Codex import skipped",
                    message=str(exc),
                )
            if imported:
                imported_any = True
                rumps.notification(
                    title="Code Agent Switcher",
                    subtitle="Codex account imported",
                    message=f"{imported.email} ({imported.subscription_type})",
                )

        if not imported_any and not claude_available and not codex_available:
            rumps.alert(
                title="CLI not found",
                message="Please install Claude Code or Codex CLI before using Code Agent Switcher.",
            )

    def _rebuild_menu(self):
        """Draw the panel: a reading at the top, the controls under it."""
        accounts = sort_accounts(load_accounts(self.config_path))
        self.menu.clear()
        self._usage_items = {}
        self._card_meta = {}

        nsmenu = self.menu._menu
        nsmenu.setDelegate_(self._watcher)
        nsmenu.addItem_(ui.menu_item_with_view(ui.panel_title("Usage")))

        for provider in ("claude", "codex"):
            rows = [a for a in accounts if a.provider == provider]
            if not rows:
                continue
            nsmenu.addItem_(ui.menu_item_with_view(ui.section_header(PROVIDER_LABELS[provider])))
            live = self._live_active_email(provider)
            for account in rows:
                key = account_key(account)
                active = self._is_row_active(account.email, live, account.active)
                self._card_meta[key] = (
                    account.email, account.subscription_type or "", active
                )
                item = ui.menu_item_with_view(self._card_for(key), enabled=not active)
                nsmenu.addItem_(item)
                self._usage_items[key] = item
            nsmenu.addItem_(ui.menu_item_with_view(ui.spacer(2)))

        if not accounts:
            nsmenu.addItem_(ui.menu_item_with_view(
                ui.section_header("no accounts saved yet")
            ))

        accounts_item = rumps.MenuItem("Manage accounts", callback=self._on_manage_accounts)
        ui.set_symbol(accounts_item, "person.2")
        self.menu.add(accounts_item)

        report_item = rumps.MenuItem("Analytics and Usage Report",
                                     callback=self._on_usage_report)
        ui.set_symbol(report_item, "chart.bar")
        self.menu.add(report_item)

        self.menu.add(rumps.separator)
        settings = rumps.MenuItem("Settings")
        ui.set_symbol(settings, "gearshape")
        self._add_auto_switch_menu(settings)
        self._add_update_menu(settings)
        self.menu.add(settings)

        self.menu.add(rumps.separator)
        quit_item = rumps.MenuItem(f"Quit  (v{updater.current_version()})",
                                   callback=rumps.quit_application)
        ui.set_symbol(quit_item, "power")
        self.menu.add(quit_item)

    def _card_for(self, key):
        """Build one account card from the cached reading."""
        email, plan, active = self._card_meta.get(key, ("", "", False))
        provider = key[0]
        state = self._usage_state_cache.get(key)
        on_click = None
        if not active and email:
            on_click = lambda: self._switch_account(provider, email)  # noqa: E731
        return ui.card_row(
            email, plan, active,
            ui.rows_for(state) if state else [],
            reason=getattr(state, "reason", None) if state else "reading\u2026",
            on_click=on_click,
        )

    # -- polling ---------------------------------------------------------
    def on_menu_open(self):
        """The panel is on screen: read now, then keep reading while it is up."""
        self._menu_open = True
        self._fetch_all_usage()
        if self._open_timer is None:
            self._open_timer = NSTimer.timerWithTimeInterval_repeats_block_(
                OPEN_POLL_SECONDS, True, lambda _timer: self._fetch_all_usage()
            )
            # A tracking menu runs the loop in event-tracking mode, where a
            # default-mode timer never fires. Common modes covers both.
            NSRunLoop.currentRunLoop().addTimer_forMode_(
                self._open_timer, NSRunLoopCommonModes
            )

    def on_menu_close(self):
        self._menu_open = False
        if self._open_timer is not None:
            self._open_timer.invalidate()
            self._open_timer = None

    # -- the accounts screen ---------------------------------------------
    def _on_manage_accounts(self, _):
        self._show_accounts_window()

    def _show_accounts_window(self):
        self._accounts_window.show(
            sort_accounts(load_accounts(self.config_path)),
            {p: self._live_active_email(p) for p in ("claude", "codex")},
            updater.current_version(),
        )

    def _refresh_accounts_window(self):
        self._accounts_window.rebuild(
            sort_accounts(load_accounts(self.config_path)),
            {p: self._live_active_email(p) for p in ("claude", "codex")},
            updater.current_version(),
        )

    def switch_from_window(self, provider, email):
        self._switch_account(provider, email)

    def add_from_window(self, provider):
        if provider == "claude":
            self._on_add_claude_account(None)
        else:
            self._on_add_codex_account(None)

    def remove_from_window(self, provider, email):
        self._remove_account(provider, email)

    def _add_update_menu(self, parent):
        version = updater.current_version()
        menu = rumps.MenuItem(f"Updates (v{version})")
        menu.add(rumps.MenuItem("Check now...", callback=self._on_check_for_update))
        auto = rumps.MenuItem("Check automatically", callback=self._on_toggle_auto_update)
        auto.state = 1 if load_settings(self.config_path).auto_update else 0
        menu.add(auto)
        parent.add(menu)

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
                        message=f"Code Agent Switcher {updater.current_version()} is the newest release.",
                    )

            _on_main_thread(_finish)

        threading.Thread(target=_work, daemon=True).start()

    def _offer_update(self, version: str, url: str, notes: str) -> None:
        body = f"Code Agent Switcher {version} is available. You have {updater.current_version()}."
        summary = updater.plain_notes(notes)
        if summary:
            body += "\n\n" + summary
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

    @staticmethod
    def _is_row_active(email: str, live_email: str | None, recorded_active: bool) -> bool:
        """Which row gets the filled dot.

        Extracted so it can be tested: inlined in the menu builder, dropping the
        live_email half still passed every test in the suite.
        """
        if live_email:
            return email == live_email
        return recorded_active

    def _live_active_email(self, provider: str) -> str | None:
        """Who is signed in right now, and repair our record when it disagrees.

        Reading `active` out of the config file made the selected-account dot lie
        whenever anything signed in outside the app. Worse, the click handler used
        the same record to decide "you are already on this account" and returned
        without doing anything, so the wrong row was marked and the right row was
        unclickable. Both now follow the live credentials.
        """
        try:
            email = live_claude_email() if provider == "claude" else live_codex_email()
        except Exception:
            return None
        if not email:
            return None
        recorded = get_active_account(self.config_path, provider=provider)
        if (recorded.email if recorded else None) != email:
            if any(a.email == email and a.provider == provider
                   for a in load_accounts(self.config_path)):
                set_active_account(email, self.config_path, provider=provider)
        return email

    def _add_auto_switch_menu(self, parent):
        settings = load_settings(self.config_path)
        accounts = load_accounts(self.config_path)
        auto_menu = rumps.MenuItem("Auto-switch")
        for provider in ("claude", "codex"):
            possible = can_auto_switch(provider, accounts)
            label = PROVIDER_LABELS[provider]
            if not possible:
                # Offering a switch with nowhere to switch to is a control that
                # cannot do anything; say why rather than let it be ticked.
                label += "  (needs a second account)"
            item = rumps.MenuItem(
                label,
                callback=self._on_toggle_auto_switch if possible else None,
            )
            item._provider = provider
            item.state = 1 if possible and settings.auto_switch.get(provider, False) else 0
            auto_menu.add(item)
        parent.add(auto_menu)

    def _has_credentials(self, account) -> bool:
        service = (
            f"claude-switcher:{account.email}"
            if account.provider == "claude"
            else f"codex-switcher:{account.email}"
        )
        return keychain.read_credentials(service) is not None

    def _switch_account(self, provider: str, email: str):
        live_email = self._live_active_email(provider)
        if live_email == email:
            return
        if provider in self._switch_in_progress:
            rumps.notification(
                title="Code Agent Switcher",
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
                        title="Code Agent Switcher",
                        subtitle=f"{PROVIDER_LABELS[provider]} account switched",
                        message=email,
                    )
                self._rebuild_menu()
                self._refresh_accounts_window()
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
            title="Code Agent Switcher",
            subtitle=f"{PROVIDER_LABELS[provider]} account removed",
            message=email,
        )
        self._rebuild_menu()
        self._refresh_accounts_window()
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
                        "Code Agent Switcher",
                        "Claude account added",
                        f"{result.email} ({result.subscription_type})",
                    )
                else:
                    title, subtitle, message = (
                        "Code Agent Switcher",
                        "Cancelled",
                        "Login was cancelled or failed.",
                    )
            except Exception as exc:
                title, subtitle, message = "Code Agent Switcher", "Error", str(exc)

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
                        "Code Agent Switcher",
                        "Codex account added",
                        f"{result.email} ({result.subscription_type})",
                    )
                else:
                    title, subtitle, message = (
                        "Code Agent Switcher",
                        "Cancelled",
                        "Login was cancelled or failed.",
                    )
            except Exception as exc:
                title, subtitle, message = "Code Agent Switcher", "Error", str(exc)

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
            title="Code Agent Switcher",
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
                    active = active_by_provider.get(account.provider)
                    state = self._fetch_usage_state(account, active)
                    self._usage_state_cache[key] = state
                    # Every poll is thrown away otherwise, and the series is the
                    # only place the real budget per account can be read from.
                    usage_log.record(
                        provider=account.provider,
                        account=account.email,
                        plan=account.subscription_type or "",
                        active=bool(active and active.email == account.email),
                        state=state,
                    )

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
                usage, reason = (
                    fetch_active_usage_detail()
                    if active_account and active_account.email == account.email
                    else fetch_usage_detail_for_account(account.email)
                )
                return claude_usage_state(usage, reason)
            if account.provider == "codex":
                usage = (
                    fetch_active_codex_usage()
                    if active_account and active_account.email == account.email
                    else fetch_codex_usage_for_account(account.email)
                )
                return codex_usage_state(usage)
        except Exception:
            pass
        return UsageState(available=False, display="Usage unavailable",
                          reason="the app hit an unexpected error")

    def _budget_rates(self) -> dict[tuple[str, str], float]:
        """Tokens per one percent, per account and window, from the recorded series.

        Only computed when an auto-switch is actually about to happen. It reads a
        week of transcripts, which is seconds of work - fine once in a while on a
        background thread, wasteful every five minutes for a decision that is not
        being made.
        """
        now = time.time()
        if self._budget_rates_at and now - self._budget_rates_at < BUDGET_RATE_TTL_SECONDS:
            return self._budget_rates_cache
        rates: dict[tuple[str, str], float] = {}
        try:
            records = load_records(since_days(7))
            samples = usage_log.load()
            totals: dict[tuple[str, str], list[float]] = {}
            for step in usage_log.steps(samples):
                spent = tokens_between(
                    records, step.at - timedelta(minutes=step.minutes), step.at
                )
                bucket = totals.setdefault((step.account, step.label), [0.0, 0.0])
                bucket[0] += spent
                bucket[1] += step.delta_percent
            for key, (spent, percent) in totals.items():
                if percent > 0:
                    rates[key] = spent / percent
        except Exception:
            rates = {}
        self._budget_rates_cache = rates
        self._budget_rates_at = now
        return rates

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
            per_point=self._budget_rates(),
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
                title="Code Agent Switcher",
                subtitle=f"Auto-switched {label}",
                message=result["email"],
            )
        elif result["status"] == "no_target":
            rumps.notification(
                title="Code Agent Switcher",
                subtitle=f"{label} limit reached",
                message="No available account to switch to.",
            )
        elif result["status"] == "error":
            rumps.notification(
                title="Code Agent Switcher",
                subtitle=f"{label} auto-switch failed",
                message=result.get("message", "Unknown error"),
            )

    def _update_usage_labels(self):
        """Redraw each card in place. Works while the panel is open."""
        for key, item in self._usage_items.items():
            try:
                item.setView_(self._card_for(key))
            except Exception:
                continue

    def _on_refresh_usage(self, _):
        """Refresh usage data for all accounts."""
        self._fetch_all_usage()

    def _on_usage_report(self, _):
        """Build the token and cost report and open it in the browser.

        Reading a month of transcripts takes a few seconds, so it happens off the
        main thread; blocking there freezes the whole menu bar, not just this app.
        """
        if self._report_in_progress:
            return
        self._report_in_progress = True

        def _work():
            error = None
            path = None
            try:
                records = load_records(since_days(REPORT_DAYS))
                path = write_report(records)
            except Exception as exc:
                error = str(exc)

            def _finish():
                self._report_in_progress = False
                if error:
                    rumps.alert(title="Could not build the report", message=error)
                    return
                subprocess.Popen(["/usr/bin/open", str(path)])

            _on_main_thread(_finish)

        threading.Thread(target=_work, daemon=True).start()

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
