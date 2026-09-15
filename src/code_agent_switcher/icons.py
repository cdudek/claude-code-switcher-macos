"""The menu bar icon.

A macOS *template* image: black artwork on transparency, no colour. The system
tints the alpha channel to match the bar, so one file covers light and dark. It
ships at 22, 44 and 66 px; macOS picks by display scale from the @2x / @3x
suffixes, so only the 22 px path is ever named.
"""

from pathlib import Path

ICONS_DIR = Path(__file__).parent / "resources" / "icons"
ICON_SLUG = "brackets"


def icon_path() -> str:
    """Absolute path to the menu bar icon."""
    return str(ICONS_DIR / f"{ICON_SLUG}.png")
