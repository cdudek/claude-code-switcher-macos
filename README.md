# Code Agent Switcher

Switch between several Claude Code and Codex CLI accounts from the menu bar,
without logging out, opening a browser, and logging back in every time.

> A fork of claude-account-switcher by Emile Jouannet, who wrote the app. This fork
> fixes six ways a saved sign-in was silently destroyed, and adds a usage report,
> self-update and a real installer.

## Install

Download the latest **`.dmg`** from
[Releases](https://github.com/cdudek/code-agent-switcher/releases/latest)
and drag Code Agent Switcher into Applications. That is the whole install.

Prefer to build it? `./install.sh` compiles, tests, installs and launches — and
skips the security warning below entirely, because a locally built app was never
downloaded.

### macOS will refuse to open the download the first time

You will get *"Apple could not verify Code Agent Switcher is free of malware"*, or
*"cannot be opened because the developer cannot be verified"*.

**Nothing is wrong with the app.** That check asks one question: did a **paid Apple
Developer account** sign and notarise this? It did not — notarising costs $99/year.
The build is signed, just not by a registered developer, and macOS treats that the
same as unsigned.

Clear the flag once:

```bash
xattr -dr com.apple.quarantine "/Applications/Code Agent Switcher.app"
```

The disk image has a **Terminal** shortcut next to the app and a
**How to open this.txt** carrying that line, so it is a double-click and a paste.

No Terminal? Open the app, let macOS refuse it, then go to **System Settings →
Privacy & Security → Security** and click **Open Anyway**. The button only shows
up after a blocked attempt.

macOS 15 removed right-click → Open for downloaded apps, so ignore any guide that
tells you to use it — including v0.7.1 of this one, which shipped an
`Open Anyway.command` that could not work.

## What it does

- **Switch Claude Code accounts** from the menu bar — personal, work, a backup on
  another plan
- **Switch Codex CLI accounts** independently; the active Claude account is
  untouched when you change the Codex one
- **Live usage in the menu** — a bar per limit window, the figure, and when it
  resets. When there is no reading it says *why*: signed out, rate limited, no
  answer. Claude reports a 5-hour and a 7-day window, Codex a primary and a
  secondary.
- **Auto-switch at the limit**, per provider, off by default
- **Usage report** — what the last 30 days actually cost, by day, by model and by
  five-hour window. See below.
- **Self-update** — see below

Credentials live in the macOS Keychain, never in a config file.

## Usage report

**Usage report…** in the menu builds a page and opens it in your browser. It reads
the transcripts Claude Code and Codex already write to disk, which carry a
timestamp, a model and a full token breakdown for every message. Nothing leaves
your Mac and no API is called.

It answers four things:

- **What it costs.** Priced at Anthropic list rates, so it is an order of
  magnitude against your subscription, not an invoice. A model with no published
  rate is priced at its family's rate and marked *assumed*; one with no rate at
  all is counted in tokens and named.
- **By day.** Tokens split into cache read, cache write, input and output. On a
  long agent session cache reads are usually well over 90% of everything, which
  is the single most surprising number in the report.
- **How the five-hour window breathes.** A window opens on a message and the next
  message five hours later opens a new one, so the count of windows is the count
  of resets. Anthropic publishes only the window you are in right now; this is
  reconstructed from your own transcripts.
- **Where it goes.** Totals per model and per agent.

The same message appears in several transcripts when a session is resumed, so
records are deduplicated on message id. Without that the totals roughly double.

## Updates

The app checks [Releases](https://github.com/cdudek/code-agent-switcher/releases)
on launch and every six hours, and offers anything newer. **Updates → Check now**
forces a check; **Check automatically** turns the background one off.

It never installs without asking. Installing verifies the download unpacks to
exactly one app bundle, then hands the swap to a script that waits for the app to
quit — a running bundle cannot replace itself. Your current version is kept next to
the new one as `Code Agent Switcher.app.previous`, so a bad build is one rename away
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

The measurements behind each are in the commit history.

## claude.ai connectors do not survive a switch, and nothing can make them

If Linear, Gmail, HubSpot or another **claude.ai connector** stops working right
after you switch accounts, the switcher is not the problem and no switcher can
be. Those connectors are authorised on Anthropic's side against one account and
organisation. Nothing about them is on your machine — no token, no refresh
token, no expiry — so there is nothing for this app to copy. The account you
moved to simply never authorised them.

A **local MCP server** is the opposite case and already works: its OAuth token
lives in the `Claude Code-credentials` Keychain blob, which this app carries to
every account on every switch.

The app now shows which is which. Each account card carries a line saying how
many claude.ai connectors that account has, and a card you are about to switch
to says what the move costs — `Switching drops Linear, Slack +2` — before you
click it.

**The browser has to be signed in as the same account.** Connecting anything —
a claude.ai connector or your own MCP server — finishes in a browser, and the
pending record belongs to the account Claude Code is signed in as. If the
browser is on a different account the callback cannot find it and Anthropic
answers `{"type":"not_found_error","message":"Server not found"}`. The error
names neither account, so it reads like a broken server. It is not: check which
account the browser is on first. An SSO-managed browser session that can only
sign in as one address is the common way to hit this.

Two ways to stop a connector breaking, and they are different trades:

1. **Authorise it on each account.** Sign in as the other account, open
   <https://claude.ai/customize/connectors> and connect it there. Keeps the
   connector's icons and MCP Apps. One browser round trip per account per
   service, forever.
2. **Run it as your own MCP server instead.** One auth, and it then works on
   every account because the token lands in the Keychain blob:

   ```bash
   claude mcp add --transport http --scope user linear https://mcp.linear.app/mcp
   ```

   Then `/mcp` and authenticate once. Claude Code expects the overlap and says
   so in its own messages ("plugin server(s) that duplicate claude.ai
   connectors"), so running both is fine.

The connector list is read with `GET /v1/mcp_servers` and the header
`anthropic-beta: mcp-servers-2025-12-04`. That is undocumented and can change,
so a failed read shows no line at all — never "0 connectors", which would blame
an account for an endpoint that moved.

## Known security limit: the credential blob passes through `argv`

`keychain.write_credentials` calls `security add-generic-password -s ... -w <blob>`,
which puts the whole credential - access token **and** refresh token - into the
child process's command line. Any process running as the same user can read it
out of `ps` with no Keychain prompt and no ACL check, which is the protection
the Keychain was chosen for. It matters more here than in most apps, because
this one deliberately holds every account: an observer watching a few switches
collects all of them, and a refresh token outlives access-token expiry.

Three fixes were tried and none is shippable yet. Recorded so the next attempt
does not repeat them:

1. **Omit `-w` and pass the password on stdin** - stores an **empty** password
   and still exits 0. Silently destroys the credential.
2. **`security -i` with the command on stdin** - cannot parse a shell-quoted
   JSON blob; exits 2.
3. **`SecKeychainAddGenericPassword` / `SecKeychainItemCreateFromContent` via
   ctypes** - writes correctly and round-trips byte-identically, but the item is
   created with an ACL that does not trust the `security` binary, so every
   subsequent read prompts. Neither an empty trusted-application array
   (`SecAccessCreate`) nor a NULL application list on each ACL
   (`SecACLSetContents`) produced the permissive ACL that `security` itself
   creates.

The real fix is to move **reads as well as writes** onto the Security framework
so one binary owns the ACL, plus a migration for the items `security` already
wrote. That is a deliberate piece of work, not a patch.

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
git clone https://github.com/cdudek/code-agent-switcher.git
cd code-agent-switcher
/opt/homebrew/bin/python3.14 -m venv .venv
./.venv/bin/pip install -e ".[dev]" "py2app>=0.28" rumps
```

Install it **editable**. A plain install means `pytest` imports the installed copy
and your edits do not run — two green test runs against stale code is how that
announced itself.

```bash
./.venv/bin/python -m pytest tests/ -q     # 196 tests
./build_app.sh                             # dist/Code Agent Switcher.app
./scripts/make-dmg.sh                      # dist/Code-Agent-Switcher.dmg
./install.sh                               # all of the above, into /Applications
```

`build_app.sh` needs a real framework CPython. py2app reads `zlib.__file__`, which
uv's Python does not have, and the build dies partway through with an
`AttributeError` that explains nothing.

## Cutting a release

The version lives in **one** place, `src/code_agent_switcher/__init__.py`. `setup.py`
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

**Emile Jouannet wrote this app** — the menu bar UI,
the Keychain-backed account store, the usage meters, auto-switch and Codex support
are all his.

This fork is maintained by [Calvin Dudek](https://github.com/cdudek).

## License

MIT — see [LICENSE](LICENSE). Upstream declared MIT in `pyproject.toml` but shipped
no license file; this fork adds the full text, naming both copyright holders.
