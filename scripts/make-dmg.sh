#!/bin/bash
# Build the drag-to-Applications disk image from dist/Claude Switcher.app.
#
# The window is laid out by AppleScript against a background image: app on the
# left, an alias to /Applications on the right, an arrow between them. That is
# the layout every Mac user already knows how to complete, which is the point -
# nobody reads installation instructions.
#
# Usage: scripts/make-dmg.sh [output.dmg]

set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/Claude Switcher.app"
VOL="Claude Switcher"
if [ "${1:-}" = "--capture" ]; then CAPTURE=1; shift; fi
OUT="${1:-dist/Claude-Switcher.dmg}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

[ -d "$APP" ] || { echo "No $APP - run ./build_app.sh first" >&2; exit 1; }

echo "Staging"
mkdir -p "$STAGE/.background"
ditto "$APP" "$STAGE/Claude Switcher.app"
ln -s /Applications "$STAGE/Applications"
# A symlink to Terminal, for the same reason the Applications alias is one: it is
# not a downloaded file, so Gatekeeper has nothing to quarantine and double-click
# just works. The audience is engineers - hand them a prompt, not a wizard.
ln -s /System/Applications/Utilities/Terminal.app "$STAGE/Terminal"
cp resources/dmg-background.png "$STAGE/.background/background.png"

# macOS 15 removed right-click > Open for quarantined items, so a shell script
# shipped in here cannot unblock anything: the script is quarantined too, and
# Sequoia refuses it outright. An earlier version of this image carried an
# "Open Anyway.command" that simply did not run. This file plus the Terminal
# alias next to it replace it.
cat > "$STAGE/How to open this.txt" <<'TXTEOF'
Claude Switcher - first launch
==============================

Drag the app into Applications, then run this once:

    xattr -dr com.apple.quarantine "/Applications/Claude Switcher.app"

Terminal is sitting right next to this file in the disk image. Double-click it,
paste the line, open the app.


Why
---

The app is not signed with an Apple Developer ID and not notarised, so
Gatekeeper refuses it. The command strips the com.apple.quarantine flag that
macOS attaches to anything downloaded.

Without Terminal: open the app, let it be refused, then go to System Settings >
Privacy & Security > Security and click Open Anyway. The button only appears
after a blocked attempt.

Build it yourself and none of this applies, because nothing downloaded it:

    git clone https://github.com/cdudek/claude-code-switcher-macos.git
    cd claude-code-switcher-macos && ./install.sh


The app runs in the menu bar, not the Dock. Look for the brackets icon top right.
TXTEOF

echo "Creating the image"
RW="$STAGE/../rw.dmg"
rm -f "$RW"
hdiutil create -srcfolder "$STAGE" -volname "$VOL" -fs HFS+ \
  -format UDRW -ov -quiet "$RW"

# Read the mount point from hdiutil rather than assuming /Volumes/$VOL. If a
# volume of that name is already mounted, macOS appends " 1" and everything
# after this writes into the wrong place - which on CI meant the layout was
# copied somewhere that was not the image.
ATTACH=$(hdiutil attach -readwrite -noverify -noautoopen "$RW")
DEV=$(echo "$ATTACH" | grep '^/dev/' | head -1 | awk '{print $1}')
MOUNT=$(echo "$ATTACH" | grep -o '/Volumes/.*$' | head -1)
[ -n "$DEV" ] && [ -d "$MOUNT" ] || { echo "Could not mount the image" >&2; exit 1; }
echo "  mounted $DEV at $MOUNT"
sleep 2

# CI has no logged-in Finder, so `tell application "Finder"` cannot lay out a
# window there. The layout lives in the volume's .DS_Store, which is just a file:
# generate it once on a machine with a desktop, commit it, and copy it in
# everywhere else. Run `scripts/make-dmg.sh --capture` after changing positions.
DS="resources/dmg-DS_Store"
if [ "${CAPTURE:-0}" != "1" ] && [ -f "$DS" ]; then
  echo "Applying the saved window layout"
  cp "$DS" "$MOUNT/.DS_Store"
  sync
else

echo "Laying out the window"
# Target the volume by the name it actually got. If a "Claude Switcher" image is
# already mounted - a released DMG the user opened, say - this one mounts as
# "Claude Switcher 1", and a hardcoded name lays out somebody else's window.
osascript - "$(basename "$MOUNT")" <<'APPLESCRIPT'
on run argv
tell application "Finder"
  tell disk (item 1 of argv)
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {180, 110, 900, 650}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 80
    set text size of opts to 13
    set background picture of opts to file ".background:background.png"
    set position of item "Claude Switcher.app" of container window to {196, 196}
    set position of item "Applications" of container window to {500, 196}
    set position of item "How to open this.txt" of container window to {196, 404}
    set position of item "Terminal" of container window to {500, 404}
    close
    open
    update without registering applications
    delay 2
  end tell
end tell
end run
APPLESCRIPT

fi

# Finder records the window settings in .DS_Store. Asking Finder to read the
# background back is unreliable, but the file it writes is not: a window with a
# picture background carries a BKGD record naming the image. Without this gate a
# broken AppleScript ships an image with no drag arrow and nobody notices.
# A byte search, not grep: BSD grep's behaviour on a binary file depends on the
# locale, and "did these exact bytes survive" is the actual question.
if ! python3 - "$MOUNT/.DS_Store" "$DS" <<'PY'
import hashlib, pathlib, sys
live, saved = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
if not live.is_file():
    print(f"  no .DS_Store at {live}", file=sys.stderr); sys.exit(1)
data = live.read_bytes()
print(f"  .DS_Store {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}")
if saved.is_file():
    ref = saved.read_bytes()
    print(f"  saved     {len(ref)} bytes, sha256 {hashlib.sha256(ref).hexdigest()[:16]}"
          f"  {'IDENTICAL' if ref == data else 'DIFFERS'}")
sys.exit(0 if b"background.png" in data else 1)
PY
then
  echo "ERROR: the window background was not applied - the image would ship without its drag arrow" >&2
  hdiutil detach "$DEV" -quiet || true
  exit 1
fi
echo "  background: applied"

if [ "${CAPTURE:-0}" = "1" ]; then
  cp "$MOUNT/.DS_Store" "$DS"
  echo "  saved the layout to $DS - commit it so CI can use it"
fi

chmod -Rf go-w "$MOUNT" 2>/dev/null || true
sync
hdiutil detach "$DEV" -quiet
sleep 1

echo "Compressing"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
hdiutil convert "$RW" -format UDZO -imagekey zlib-level=9 -o "$OUT" -quiet
rm -f "$RW"

echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
