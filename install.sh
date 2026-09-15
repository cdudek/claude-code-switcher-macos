#!/bin/bash
# Build Code Agent Switcher from this checkout and install it into /Applications.
#
# Building locally sidesteps Gatekeeper entirely: the quarantine flag is set by
# whatever DOWNLOADS a file, so an app you compiled yourself has never been
# quarantined and opens without a warning. Downloading a release is the path
# that needs the extra step - see README, "Opening it the first time".

set -euo pipefail
cd "$(dirname "$0")"

APP="Code Agent Switcher.app"
TARGET="/Applications/$APP"
PY="${PYTHON:-}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# py2app needs a real framework CPython. uv's build has no zlib.__file__ and the
# build dies partway through with an AttributeError that explains nothing.
if [ -z "$PY" ]; then
  for c in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 \
           /usr/local/bin/python3.14 /usr/local/bin/python3.13; do
    [ -x "$c" ] && { PY="$c"; break; }
  done
fi
[ -n "$PY" ] || die "No Homebrew python3.13/3.14 found. Install one:  brew install python@3.14
(or re-run as:  PYTHON=/path/to/python3 ./install.sh)"

say "Using $PY"
if [ ! -d .venv ] || [ ! -x .venv/bin/python ]; then
  say "Creating .venv"
  "$PY" -m venv .venv
fi
say "Installing dependencies"
./.venv/bin/pip -q install --upgrade pip >/dev/null
./.venv/bin/pip -q install "py2app>=0.28" rumps pytest
./.venv/bin/pip -q install -e .

say "Running tests"
./.venv/bin/python -m pytest tests/ -q

say "Building $APP"
./build_app.sh >/dev/null

[ -d "dist/$APP" ] || die "Build produced no app bundle."

if pgrep -f "$APP" >/dev/null; then
  say "Quitting the running copy"
  osascript -e 'tell application "Code Agent Switcher" to quit' 2>/dev/null || true
  for _ in $(seq 1 20); do pgrep -f "$APP" >/dev/null || break; sleep 0.3; done
  pkill -f "$APP" 2>/dev/null || true
fi

if [ -d "$TARGET" ]; then
  BACKUP="$HOME/.Trash/Code Agent Switcher (replaced $(date +%Y-%m-%d-%H%M%S)).app"
  say "Moving the installed copy to the Trash"
  mv "$TARGET" "$BACKUP"
fi

say "Installing to $TARGET"
cp -R "dist/$APP" "$TARGET"
xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true

say "Launching"
open "$TARGET"
say "Done. Code Agent Switcher $(./.venv/bin/python -c 'from claude_switcher import __version__;print(__version__)') is in your menu bar."
