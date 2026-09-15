"""The Manage Accounts screen.

Switching, adding and removing moved out of the menu and into a window. In the
menu they were three separate places - a click on a row, an "Add ..." item, a
"Remove account" submenu - and the row you clicked to switch was also the row
that showed usage, so the panel could not be read without being a control.

The window owns the actions; the menu panel is now only a reading.
"""

from __future__ import annotations

import AppKit
import objc
from Foundation import NSMakeRect

from code_agent_switcher.ui import DotView, PillView, _label, pill_width

WIDTH = 520.0
ROW_HEIGHT = 44.0
GROUP_PAD = 10.0
PROVIDER_LABELS = {"claude": "Claude Code", "codex": "Codex CLI"}


class _RowActions(AppKit.NSObject):
    """Target for the per-row menu. Kept alive by the window controller."""

    def initWithController_email_provider_active_(self, controller, email, provider, active):
        self = objc.super(_RowActions, self).init()
        if self is None:
            return None
        self._controller = controller
        self._email = email
        self._provider = provider
        self._active = active
        return self

    def showMenu_(self, sender):
        menu = AppKit.NSMenu.alloc().init()
        if not self._active:
            item = menu.addItemWithTitle_action_keyEquivalent_("Switch to this account", "switch:", "")
            item.setTarget_(self)
        item = menu.addItemWithTitle_action_keyEquivalent_("Sign in again", "signIn:", "")
        item.setTarget_(self)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        item = menu.addItemWithTitle_action_keyEquivalent_("Remove from the list", "remove:", "")
        item.setTarget_(self)
        point = AppKit.NSPoint(0, sender.frame().size.height + 2)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, point, sender)

    def switch_(self, _):
        self._controller.switchAccount(self._provider, self._email)

    def signIn_(self, _):
        self._controller.addAccount(self._provider)

    def remove_(self, _):
        self._controller.removeAccount(self._provider, self._email)


class _AddAction(AppKit.NSObject):
    def initWithController_provider_(self, controller, provider):
        self = objc.super(_AddAction, self).init()
        if self is None:
            return None
        self._controller = controller
        self._provider = provider
        return self

    def fire_(self, _):
        self._controller.addAccount(self._provider)


class _CloseAction(AppKit.NSObject):
    def initWithWindow_(self, window):
        self = objc.super(_CloseAction, self).init()
        if self is None:
            return None
        self._window = window
        return self

    def fire_(self, _):
        self._window.close()


class GroupView(AppKit.NSView):
    """A bordered group of rows, with a hairline between them."""

    def initWithFrame_count_(self, frame, count):
        self = objc.super(GroupView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._count = count
        return self

    def drawRect_(self, rect):
        bounds = AppKit.NSInsetRect(self.bounds(), 0.5, 0.5)
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        AppKit.NSColor.labelColor().colorWithAlphaComponent_(0.04).setFill()
        path.fill()
        AppKit.NSColor.separatorColor().setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        for index in range(1, self._count):
            y = bounds.size.height - index * ROW_HEIGHT
            line = AppKit.NSBezierPath.bezierPath()
            line.moveToPoint_((1, y))
            line.lineToPoint_((bounds.size.width, y))
            line.setLineWidth_(1.0)
            line.stroke()


class AccountsWindowController:
    """Builds the window and rebuilds it whenever the account list changes."""

    def __init__(self, app):
        self.app = app
        self.window = None
        self._keep_alive: list = []

    # -- actions the rows call back into ---------------------------------
    def switchAccount(self, provider, email):
        self.app.switch_from_window(provider, email)

    def addAccount(self, provider):
        self.app.add_from_window(provider)

    def removeAccount(self, provider, email):
        self.app.remove_from_window(provider, email)

    # -- building --------------------------------------------------------
    def show(self, accounts, live_active: dict[str, str | None], version: str) -> None:
        if self.window is None:
            self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, WIDTH, 420),
                AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_("Manage Accounts")
            self.window.setReleasedWhenClosed_(False)
        self.rebuild(accounts, live_active, version)
        self.window.center()
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def rebuild(self, accounts, live_active, version: str) -> None:
        if self.window is None:
            return
        self._keep_alive = []
        groups = []
        for provider in ("claude", "codex"):
            rows = [a for a in accounts if a.provider == provider]
            groups.append((provider, rows))

        height = 60.0  # footer
        for _provider, rows in groups:
            height += 26.0 + (len(rows) + 1) * ROW_HEIGHT + GROUP_PAD * 2

        content = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, height))
        y = height - 24.0

        for provider, rows in groups:
            header = _label(
                PROVIDER_LABELS[provider].upper(), 11.0,
                AppKit.NSColor.secondaryLabelColor(),
                weight=AppKit.NSFontWeightSemibold,
            )
            header.setFrame_(NSMakeRect(24, y - 16, 300, 16))
            content.addSubview_(header)
            y -= 26.0

            group_height = (len(rows) + 1) * ROW_HEIGHT
            group = GroupView.alloc().initWithFrame_count_(
                NSMakeRect(22, y - group_height, WIDTH - 44, group_height), len(rows) + 1
            )
            content.addSubview_(group)

            row_y = group_height - ROW_HEIGHT
            for account in rows:
                self._add_row(group, account, row_y,
                              live_active.get(provider) == account.email)
                row_y -= ROW_HEIGHT
            self._add_add_row(group, provider, row_y)
            y -= group_height + GROUP_PAD * 2

        stamp = _label(f"v{version}", 11.0, AppKit.NSColor.tertiaryLabelColor())
        stamp.setFrame_(NSMakeRect(24, 18, 100, 16))
        content.addSubview_(stamp)

        done = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 110, 14, 86, 26))
        done.setTitle_("Done")
        done.setBezelStyle_(AppKit.NSBezelStyleRounded)
        done.setKeyEquivalent_("\r")
        closer = _CloseAction.alloc().initWithWindow_(self.window)
        self._keep_alive.append(closer)
        done.setTarget_(closer)
        done.setAction_("fire:")
        content.addSubview_(done)

        self.window.setContentSize_(AppKit.NSMakeSize(WIDTH, height))
        self.window.setContentView_(content)

    def _add_row(self, group, account, y, is_active) -> None:
        if is_active:
            dot = DotView.alloc().initWithFrame_(NSMakeRect(18, y + ROW_HEIGHT / 2 - 4, 8, 8))
            group.addSubview_(dot)

        name = _label(account.email, 13.0)
        name.sizeToFit()
        frame = name.frame()
        name.setFrame_(NSMakeRect(40, y + (ROW_HEIGHT - frame.size.height) / 2,
                                  frame.size.width, frame.size.height))
        group.addSubview_(name)

        plan = account.subscription_type or ""
        if plan:
            width = pill_width(plan)
            pill = PillView.alloc().initWithFrame_text_(
                NSMakeRect(48 + frame.size.width, y + ROW_HEIGHT / 2 - 8, width, 16), plan
            )
            group.addSubview_(pill)

        right = group.frame().size.width
        if is_active:
            tag = _label("Active", 12.0, AppKit.NSColor.controlAccentColor(),
                         align=AppKit.NSTextAlignmentRight)
            tag.setFrame_(NSMakeRect(right - 132, y + ROW_HEIGHT / 2 - 8, 70, 16))
            group.addSubview_(tag)

        actions = _RowActions.alloc().initWithController_email_provider_active_(
            self, account.email, account.provider, is_active
        )
        self._keep_alive.append(actions)
        button = AppKit.NSButton.alloc().initWithFrame_(
            NSMakeRect(right - 48, y + ROW_HEIGHT / 2 - 11, 30, 22)
        )
        button.setTitle_("⋯")
        button.setBordered_(False)
        button.setFont_(AppKit.NSFont.systemFontOfSize_(15.0))
        button.setTarget_(actions)
        button.setAction_("showMenu:")
        group.addSubview_(button)

    def _add_add_row(self, group, provider, y) -> None:
        action = _AddAction.alloc().initWithController_provider_(self, provider)
        self._keep_alive.append(action)
        label = "Add Claude account" if provider == "claude" else "Add Codex account"
        button = AppKit.NSButton.alloc().initWithFrame_(
            NSMakeRect(14, y + 6, group.frame().size.width - 28, ROW_HEIGHT - 12)
        )
        button.setTitle_(f"＋   {label}")
        button.setBordered_(False)
        button.setAlignment_(AppKit.NSTextAlignmentLeft)
        button.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
        button.setTarget_(action)
        button.setAction_("fire:")
        group.addSubview_(button)
