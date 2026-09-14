"""The menu bar icon set, and which one is in use.

Icons are macOS *template* images: black artwork on transparency, no colour.
The system tints the alpha channel to match the bar, so one file covers both
light and dark. Each icon ships at 22, 44 and 66 px; macOS picks by display
scale from the @2x / @3x suffixes, so only the 22 px path is ever named.
"""

from pathlib import Path

ICONS_DIR = Path(__file__).parent / "resources" / "icons"
DEFAULT_ICON = "twin-spark"

# slug -> the name shown in the menu. Order is the menu order: the two families
# worth choosing between first, then the rest.
ICON_LABELS: dict[str, str] = {
    "twin-spark": "Twin spark",
    "spark-tight": "Twin spark, bold",
    "spark-outline": "Spark outline",
    "spark-pair": "Spark pair",
    "spark-trio": "Spark trio",
    "relay": "Relay",
    "relay-bold": "Relay, bold",
    "relay-spark": "Relay with spark",
    "relay-tips": "Relay, spark tips",
    "relay-curved": "Relay, curved",
    "graph": "Graph",
    "burst": "Claude mark",
    "burst-cycle": "Claude mark in cycle",
    "toggle": "Toggle",
}


def icon_path(slug: str) -> str:
    """Absolute path to an icon, falling back to the default if it is missing.

    A slug can go stale two ways: the config carries one this build no longer
    ships, or a file was lost. Either way the app must still show an icon -
    a menu bar item with no icon and no title is invisible and unclickable.
    """
    candidate = ICONS_DIR / f"{slug}.png"
    if slug in ICON_LABELS and candidate.is_file():
        return str(candidate)
    return str(ICONS_DIR / f"{DEFAULT_ICON}.png")


def is_known(slug: str) -> bool:
    """True if the slug names an icon this build ships."""
    return slug in ICON_LABELS and (ICONS_DIR / f"{slug}.png").is_file()
