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
cp resources/dmg-background.png "$STAGE/.background/background.png"

# Gatekeeper blocks a downloaded build because it is ad-hoc signed rather than
# notarised. This is the one-click way out, shipped inside the image so nobody
# has to find and retype an xattr command. It is itself quarantined, so it needs
# right-click > Open - which the background image says.
cat > "$STAGE/Open Anyway.command" <<'CMDEOF'
#!/bin/bash
APP="/Applications/Claude Switcher.app"
echo
echo "  Claude Switcher — allow it to open"
echo "  ─────────────────────────────────────────────────────────"
echo
if [ ! -d "$APP" ]; then
  echo "  Claude Switcher is not in your Applications folder yet."
  echo "  Drag it there first, then run this again."
  echo
  read -n 1 -s -r -p "  Press any key to close."
  exit 1
fi
echo "  Removing the quarantine flag macOS attached when you downloaded it."
echo "  That flag is why it says Apple could not verify the app: this build is"
echo "  signed, but not by a paid Apple Developer account."
echo
xattr -dr com.apple.quarantine "$APP" && echo "  Done." || echo "  Could not remove it."
echo "  Opening Claude Switcher — look for the icon in your menu bar."
open "$APP"
echo
read -n 1 -s -r -p "  Press any key to close."
CMDEOF
chmod +x "$STAGE/Open Anyway.command"

echo "Creating the image"
RW="$STAGE/../rw.dmg"
rm -f "$RW"
hdiutil create -srcfolder "$STAGE" -volname "$VOL" -fs HFS+ \
  -format UDRW -ov -quiet "$RW"

DEV=$(hdiutil attach -readwrite -noverify -noautoopen "$RW" | grep '^/dev/' | head -1 | awk '{print $1}')
MOUNT="/Volumes/$VOL"
sleep 2

# CI has no logged-in Finder, so `tell application "Finder"` cannot lay out a
# window there. The layout lives in the volume's .DS_Store, which is just a file:
# generate it once on a machine with a desktop, commit it, and copy it in
# everywhere else. Run `scripts/make-dmg.sh --capture` after changing positions.
DS="resources/dmg-DS_Store"
if [ "${CAPTURE:-0}" != "1" ] && [ -f "$DS" ]; then
  echo "Applying the saved window layout"
  cp "$DS" "$MOUNT/.DS_Store"
else

echo "Laying out the window"
osascript <<'APPLESCRIPT'
tell application "Finder"
  tell disk "Claude Switcher"
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
    set position of item "Open Anyway.command" of container window to {500, 404}
    close
    open
    update without registering applications
    delay 2
  end tell
end tell
APPLESCRIPT

fi

# Finder records the window settings in .DS_Store. Asking Finder to read the
# background back is unreliable, but the file it writes is not: a window with a
# picture background carries a BKGD record naming the image. Without this gate a
# broken AppleScript ships an image with no drag arrow and nobody notices.
if ! grep -aq "background.png" "$MOUNT/.DS_Store" 2>/dev/null; then
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
