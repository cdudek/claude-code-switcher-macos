# Claude Code Switcher for macOS

Switch between several Claude Code and Codex CLI accounts from the menu bar,
without logging out, opening a browser, and logging back in every time.

> A fork of [Symbioose/claude-account-switcher](https://github.com/Symbioose/claude-account-switcher)
> by [Emile Jouannet](https://github.com/Symbioose), who wrote the app. This fork fixes
> six ways a saved sign-in was silently destroyed, and adds an icon set, self-update
> and a real installer. The fixes are offered back upstream as
> [PR #11](https://github.com/Symbioose/claude-account-switcher/pull/11).

## Install

Download the latest **`.dmg`** from
[Releases](https://github.com/cdudek/claude-code-switcher-macos/releases/latest)
and drag Claude Switcher into Applications. That is the whole install.

Prefer to build it? `./install.sh` compiles, tests, installs and launches — and
skips the security warning below entirely, because a locally built app was never
downloaded.

### macOS will refuse to open the download the first time

You will get *"Apple could not verify Claude Switcher is free of malware"*, or
*"cannot be opened because the developer cannot be verified"*.

**Nothing is wrong with the app.** That check asks one question: did a **paid Apple
Developer account** sign and notarise this? It did not — notarising costs $99/year.
The build is signed, just not by a registered developer, and macOS treats that the
same as unsigned.

The disk image ships **Open Anyway.command** for exactly this. Right-click it,
choose Open, and it clears the flag and launches the app. Or do it yourself:

```bash
xattr -dr com.apple.quarantine "/Applications/Claude Switcher.app"
```

Or open **System Settings → Privacy & Security**, scroll to Security, and click
**Open Anyway**.

## What it does

- **Switch Claude Code accounts** from the menu bar — personal, work, a backup on
  another plan
- **Switch Codex CLI accounts** independently; the active Claude account is
  untouched when you change the Codex one
- **Live usage in the bar** — Claude's 5-hour and 7-day windows, Codex's primary
  and secondary
- **Auto-switch at the limit**, per provider, off by default
- **Fourteen menu bar icons** under **Icon** — five sparks, five relays, plus
  Claude's sunburst, a graph and a toggle. Every mark trades saying *switch*
  against saying *AI* against staying readable at 22 points, and which trade wins
  depends on what else is in your bar, so the set ships rather than one winner.
- **Self-update** — see below

Credentials live in the macOS Keychain, never in a config file.

## Updates

The app checks [Releases](https://github.com/cdudek/claude-code-switcher-macos/releases)
on launch and every six hours, and offers anything newer. **Updates → Check now**
forces a check; **Check automatically** turns the background one off.

It never installs without asking. Installing verifies the download unpacks to
exactly one app bundle, then hands the swap to a script that waits for the app to
quit — a running bundle cannot replace itself. Your current version is kept next to
the new one as `Claude Switcher.app.previous`, so a bad build is one rename away
from undone.

The download is unsigned, so two refusals are the whole trust boundary: the URL
must be a release asset on this repository, and no entry in the archive may escape
its staging directory. Both are covered by tests that fail if the check is removed.

## What this fork fixes

Each of these destroyed a saved sign-in with no visible error. The damage showed up
later as `Login expired · Please run /login` on an account that looked perfectly
healthy in the menu.

1. **A snapshot could be saved with empty tokens.** Claude Code writes its Keychain
   entry in stages; for about a second after login the record exists with empty
   token strings, and the import accepted any non-empty *string*.
2. **Switching wiped every MCP server login.** The Keychain blob holds two unrelated
   things: the Claude account, and `mcpOAuth` — tokens for Vercel, Notion, Linear
   and so on, which belong to the machine rather than the account. Switching
   replaced the blob wholesale. On one machine that was 58 logins.
3. **Add Account wiped them too**, and harder: `claude auth logout` deletes the
   whole entry.
4. **Saved credentials get revoked server-side.** Anthropic keeps one live sign-in
   per account, so a `/login` anywhere revokes the saved one. The snapshot still
   looks perfect — non-empty tokens, `expiresAt` hours away — and the API answers
   it with 401. Switching now refreshes the pair first, which proves it is real and
   keeps the snapshot from ageing out.
5. **Add Account revoked the previously active account**, by running
   `claude auth logout` on the credentials it had just saved as that account's
   snapshot.
6. **A revoked sign-in was a dead end** — an `Error` alert with one OK button, and
   no hint that the fix is to remove the account and add it back. It now offers
   *Sign in again* / *Remove account* / *Cancel*.

The measurements behind each are in
[the upstream PR](https://github.com/Symbioose/claude-account-switcher/pull/11).

## Known limits

- **A `/login` you run in the terminal revokes that account's saved session.** One
  live sign-in per account is Anthropic's rule, not something this app can work
  around. The next switch to that account will offer to sign in again.
- **Concurrent sessions race.** Several running `claude` processes refresh tokens
  independently, and each refresh invalidates the previous pair. On a machine with
  many sessions a saved snapshot can go stale between switches.
- **Not notarised**, so every download needs the one-time step above.

## Requirements

macOS 12 or newer. Claude Code and/or Codex CLI installed. Nothing else — the app
bundle carries its own Python.

## Developing

```bash
git clone https://github.com/cdudek/claude-code-switcher-macos.git
cd claude-code-switcher-macos
/opt/homebrew/bin/python3.14 -m venv .venv
./.venv/bin/pip install -e ".[dev]" "py2app>=0.28" rumps
```

Install it **editable**. A plain install means `pytest` imports the installed copy
and your edits do not run — two green test runs against stale code is how that
announced itself.

```bash
./.venv/bin/python -m pytest tests/ -q     # 196 tests
./build_app.sh                             # dist/Claude Switcher.app
./scripts/make-dmg.sh                      # dist/Claude-Switcher.dmg
./install.sh                               # all of the above, into /Applications
```

`build_app.sh` needs a real framework CPython. py2app reads `zlib.__file__`, which
uv's Python does not have, and the build dies partway through with an
`AttributeError` that explains nothing.

## Cutting a release

The version lives in **one** place, `src/claude_switcher/__init__.py`. `setup.py`
and `pyproject.toml` read it, and the release workflow fails if the tag disagrees.

```bash
# bump __version__, commit, then:
git tag v0.6.0 && git push origin v0.6.0
```

The workflow builds on a macOS runner, runs the tests, signs ad-hoc, and publishes
a `.dmg` for people and a `.zip` for the updater.

**Exactly one `.zip` per release.** The updater refuses a release with two, because
choosing between them means guessing which binary to run.

## Credits

**[Emile Jouannet](https://github.com/Symbioose) wrote this app** — the menu bar UI,
the Keychain-backed account store, the usage meters, auto-switch and Codex support
are all his.

This fork is maintained by [Calvin Dudek](https://github.com/cdudek).

## License

MIT — see [LICENSE](LICENSE). Upstream declared MIT in `pyproject.toml` but shipped
no license file; this fork adds the full text, naming both copyright holders.
