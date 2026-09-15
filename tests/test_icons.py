"""The menu bar icon must exist at every scale, or the bar shows nothing.

A status item with no icon and no title is invisible and unclickable, so a
missing file is not a cosmetic bug.
"""

from pathlib import Path

from claude_switcher.icons import ICON_SLUG, ICONS_DIR, icon_path


class TestMenuBarIcon:
    def test_the_icon_file_exists(self):
        assert Path(icon_path()).is_file()

    def test_icon_path_points_at_the_app_mark(self):
        assert Path(icon_path()).name == f"{ICON_SLUG}.png"

    def test_every_scale_ships(self):
        for suffix in ("", "@2x", "@3x"):
            assert (ICONS_DIR / f"{ICON_SLUG}{suffix}.png").is_file()

    def test_nothing_else_ships(self):
        """The picker is gone; stray icons are dead weight in the bundle."""
        pngs = {p.name for p in ICONS_DIR.glob("*.png")}
        assert pngs == {f"{ICON_SLUG}.png", f"{ICON_SLUG}@2x.png", f"{ICON_SLUG}@3x.png"}
