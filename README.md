# Claude Code Switcher for macOS

> **A fork of [Symbioose/claude-account-switcher](https://github.com/Symbioose/claude-account-switcher)
> by [Emile Jouannet](https://github.com/Symbioose).** He wrote the app; this fork adds fixes for
> five ways a saved sign-in was silently destroyed, and a dialog that lets you recover from the
> sixth. They are offered back upstream in
> [Symbioose/claude-account-switcher#11](https://github.com/Symbioose/claude-account-switcher/pull/11)
> — if that merges, this fork exists only to ship builds.

[![macOS](https://img.shields.io/badge/macOS-12%2B-000?style=flat-square&logo=apple)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)

Switch between multiple Claude Code and Codex CLI accounts from your macOS menu bar.

Claude Switcher keeps separate account sessions for Claude Code and Codex CLI, shows live usage, and can automatically switch to another saved account when a provider reaches its limit.

![Claude Switcher screenshot](screenshot.png)

## Why use it?

AI coding CLIs are great until you need to jump between personal, work, team, or backup accounts. Without this app, switching usually means logging out, opening a browser, logging back in, and interrupting whatever you were doing in the terminal.

Claude Switcher stores account backups in macOS Keychain and swaps the active CLI session in one click. Claude and Codex are handled independently, so the active Claude account never changes your active Codex account.

## Features

- **Claude Code account switching** - swap saved Claude Code sessions instantly
- **Codex CLI account switching** - save and restore Codex `auth.json` sessions
- **Provider-separated state** - same email can exist once for Claude and once for Codex
- **Live usage in the menu bar** - Claude 5-hour/7-day windows and Codex primary/secondary windows
- **Optional auto-switch** - per-provider failover when active usage reaches 100%
- **First-launch import** - imports the currently logged-in Claude and Codex accounts when available
- **macOS Keychain backups** - saved credentials are stored in Keychain, not plaintext config
- **Standalone `.app` build** - no Python install required for normal users

## Install

### From source — no security warning

```bash
git clone https://github.com/cdudek/claude-code-switcher-macos.git
cd claude-code-switcher-macos
./install.sh
```

That builds it, runs the tests, moves any installed copy to the Trash and launches
the new one. It needs a Homebrew CPython (`brew install python@3.14`); `install.sh`
finds it, or you can point at one with `PYTHON=/path/to/python3 ./install.sh`.

> **Why not uv?** py2app reads `zlib.__file__`, which uv's Python does not have, and
> the build dies partway through with an `AttributeError` that explains nothing.

### From a release — one extra step

Download the zip from [Releases](https://github.com/cdudek/claude-code-switcher-macos/releases),
unzip, drag **Claude Switcher.app** into `/Applications`, then read the next section
before you double-click it.

## Opening it the first time

**macOS will refuse to open a downloaded build, and the wording makes it sound like
malware.** Depending on your version you get *"Apple could not verify Claude Switcher
is free of malware"*, or *"cannot be opened because the developer cannot be
verified"*.

Nothing is wrong with the app. Apple's check is asking whether a **paid Apple
Developer account** signed and notarised it. This one is signed ad-hoc — a real
signature, but not one tied to a registered developer, because notarising costs
$99/year. macOS treats that the same as unsigned.

Two ways past it, both one-time:

**Terminal** — strips the quarantine flag the download attached:

```bash
xattr -dr com.apple.quarantine "/Applications/Claude Switcher.app"
open "/Applications/Claude Switcher.app"
```

**Or System Settings** — open the app once and let it be blocked, then go to
**System Settings → Privacy & Security**, scroll down to Security, and click
**Open Anyway** next to the message naming Claude Switcher. Confirm with Touch ID.

Building from source avoids all of this: the quarantine flag is attached by whatever
*downloads* a file, so an app you compiled has never had one.

## Updates

The app checks [Releases](https://github.com/cdudek/claude-code-switcher-macos/releases)
on launch and every six hours, and offers anything newer. **Updates → Check now**
forces a check; **Check automatically** turns the background one off.

It never installs without asking. Installing downloads the release zip, verifies it
unpacks to exactly one app bundle, then hands the swap to a short script that waits
for the app to quit — a running bundle cannot replace itself. Your current version
goes to the Trash, not the bin, so a bad build is one drag away from being undone.

The download is not signed, so the update only trusts a URL under
`https://github.com/cdudek/claude-code-switcher-macos/releases/download/`, and it
refuses an archive that unpacks anywhere outside its own staging directory.

## What this fork changes

Every one of these destroys a saved sign-in with no visible error, so the failure shows up
later as `Login expired · Please run /login` on an account that looked fine:

1. **A snapshot could be saved with empty tokens.** Claude Code writes its Keychain entry in
   stages; for about a second after login the record exists with empty token strings, and the
   import accepted any non-empty string.
2. **Switching wiped MCP server logins.** The blob holds `claudeAiOauth` *and* `mcpOAuth` —
   tokens for Vercel, Notion, Linear and friends, which belong to the machine, not the
   account. Switching replaced the blob wholesale.
3. **Add Account wiped them too**, and worse: `claude auth logout` deletes the whole entry.
4. **Saved pairs get revoked server-side.** Anthropic keeps one live sign-in per account, so a
   `/login` anywhere revokes the saved one. The snapshot still looks perfect — non-empty
   tokens, `expiresAt` hours away — and the API answers it with 401. Switching now refreshes
   the pair first, which both proves it is real and keeps the snapshot from ageing out.
5. **Add Account revoked the previously active account**, by calling `claude auth logout` on
   the credentials it had just saved as that account's snapshot.
6. **A revoked sign-in was a dead end in the UI** — an `Error` alert with a single OK button.
   It now gets `Sign in again` / `Remove account` / `Cancel`.

Full detail, with the measurements behind each:
[the upstream PR](https://github.com/Symbioose/claude-account-switcher/pull/11).

Two things this fork adds that are not in that PR, because they are features rather
than fixes:

- **Fourteen menu bar icons, pickable from the menu** (**Icon**). Two families —
  sparks and relays — plus Claude's own sunburst, a graph and a toggle. Every mark
  is a compromise between saying *switch*, saying *AI*, and staying legible at 22
  points, and which compromise wins depends on what else is in your menu bar.
  Each ships at 22/44/66 px with the SVG it was rendered from.
- **Self-update from GitHub Releases** — see [Updates](#updates).

### GitHub release

1. Open the [latest release](https://github.com/Symbioose/claude-account-switcher/releases/latest)
2. Download `Claude-Switcher-vX.Y.Z.zip`
3. Unzip it and drag **Claude Switcher.app** to `/Applications`
4. Launch it. The app appears as a menu bar icon.

On first launch, macOS may block the app because it is not signed. Open **System Settings -> Privacy & Security** and click **Open Anyway**.

## Usage

Open the menu bar icon to:

- click any Claude or Codex account to make it active
- add a Claude account with `claude auth login`
- add a Codex account with a visible Terminal-based `codex login` flow
- refresh live usage manually
- enable or disable auto-switch separately for Claude and Codex
- remove saved inactive accounts from Keychain

After switching Claude, verify from any terminal:

```bash
claude auth status
```

After switching Codex, verify with:

```bash
codex login status
```

## How it works

Claude Code stores OAuth credentials in macOS Keychain under `Claude Code-credentials` and account metadata in `~/.claude.json`. Claude Switcher backs up each saved Claude account under `claude-switcher:{email}`, then restores the selected backup into Claude Code's active credential slot and updates `~/.claude.json`.

Codex CLI stores ChatGPT authentication in `~/.codex/auth.json` when `cli_auth_credentials_store = "file"` is used. Claude Switcher backs up each saved Codex session under `codex-switcher:{email}` and restores the selected session back to `~/.codex/auth.json` with `0600` permissions.

Auto-switch is disabled by default. When enabled, usage refreshes in the background and the app switches only within the same provider. A full Claude account switches to another Claude account; a full Codex account switches to another Codex account.

### Architecture

```text
macOS Keychain
├── Claude Code-credentials       active Claude token
├── claude-switcher:user1@...     saved Claude account
├── claude-switcher:user2@...     saved Claude account
├── codex-switcher:user1@...      saved Codex auth.json
└── codex-switcher:user2@...      saved Codex auth.json

~/.claude.json
└── oauthAccount                  swapped on Claude account switch

~/.codex/auth.json
└── tokens                        swapped on Codex account switch

~/.config/claude-switcher/accounts.json
└── provider, email, plan, active state, settings
```

## Codex setup note

Codex keyring storage is detected but not switched in this release. When adding a Codex account, the app opens Terminal and runs Codex login with file-mode credentials. You can also configure file mode explicitly:

```toml
# ~/.codex/config.toml
cli_auth_credentials_store = "file"
```

Then run:

```bash
codex login
```

If a saved Codex session expires because its refresh token was already rotated, the app refuses to restore that stale session and shows **Login required** for usage. Add that Codex account again to refresh the saved Keychain backup.

## Security

- Saved account backups are stored in macOS Keychain
- The app config stores metadata only and is written with `0600` permissions
- The config directory is written with `0700` permissions
- Email validation prevents Keychain service-name injection
- Subprocess calls do not use `shell=True`
- Network usage checks run with short timeouts in background workers
- Keychain reads/writes use short timeouts so the menu bar UI does not hang indefinitely

## Requirements

- macOS 12 or later
- Claude Code CLI for Claude account switching
- Codex CLI for Codex account switching
- Codex file-mode credentials for Codex switching in this version

## Developing

```bash
git clone https://github.com/cdudek/claude-code-switcher-macos.git
cd claude-code-switcher-macos
/opt/homebrew/bin/python3.14 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pip install "py2app>=0.28" rumps
```

Install it editable, not just on the path: a non-editable install means `pytest`
silently imports the *installed* copy and your edits do not run. That cost an hour
once — two test passes reported green against stale code.

```bash
./.venv/bin/python -m pytest tests/ -q     # tests
./.venv/bin/python -m claude_switcher      # run without building
./build_app.sh                             # dist/Claude Switcher.app
```

## Cutting a release

The version lives in **one** place, `src/claude_switcher/__init__.py`; `setup.py`
and `pyproject.toml` read it, and the release workflow fails if the tag disagrees
with it.

```bash
# bump __version__, commit, then:
git tag v0.5.0 && git push origin v0.5.0
```

`.github/workflows/release.yml` builds on a macOS runner, runs the tests, signs
ad-hoc, packages with `ditto` (which keeps the bundle's symlinks — `zip` does not)
and publishes `Claude-Switcher-vX.Y.Z.zip`.

**The release must carry exactly one `.zip` asset.** The in-app updater refuses a
release with two, because picking between them means guessing which binary to run.

## Credits

**[Emile Jouannet](https://github.com/Symbioose) wrote this app.** The menu bar UI, the
Keychain-backed account store, the usage meters, auto-switch and the Codex support are all
his — see [Symbioose/claude-account-switcher](https://github.com/Symbioose/claude-account-switcher).

This fork adds credential-loss fixes and a recovery dialog, offered back upstream as
[PR #11](https://github.com/Symbioose/claude-account-switcher/pull/11). Maintained by
[Calvin Dudek](https://github.com/cdudek).

## License

MIT — see [LICENSE](LICENSE). Upstream declared MIT in `pyproject.toml` but shipped no
license file; this fork adds the full text, crediting both copyright holders.
