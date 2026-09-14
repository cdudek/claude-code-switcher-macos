"""Tests for the shipped icon set and the persisted choice.

The menu bar item has no title - the icon IS the whole control. If icon_path()
ever returns something macOS cannot load, the app becomes an invisible,
unclickable gap in the bar, so the fallback behaviour is what matters most here.
"""

import json

import pytest

from claude_switcher.config import AppSettings, load_settings, save_settings, set_icon
from claude_switcher.icons import (
    DEFAULT_ICON,
    ICONS_DIR,
    ICON_LABELS,
    icon_path,
    is_known,
)


class TestShippedSet:
    def test_every_listed_icon_ships_all_three_scales(self):
        for slug in ICON_LABELS:
            for suffix in ("", "@2x", "@3x"):
                f = ICONS_DIR / f"{slug}{suffix}.png"
                assert f.is_file(), f"missing {f.name}"
                assert f.stat().st_size > 0, f"empty {f.name}"

    def test_default_is_in_the_set(self):
        assert DEFAULT_ICON in ICON_LABELS
        assert is_known(DEFAULT_ICON)

    def test_labels_are_unique(self):
        """Two identical menu entries would be unpickable."""
        labels = list(ICON_LABELS.values())
        assert len(labels) == len(set(labels))

    def test_every_icon_keeps_its_vector_source(self):
        for slug in ICON_LABELS:
            assert (ICONS_DIR / f"{slug}.svg").is_file(), f"no source for {slug}"


class TestIconPath:
    def test_known_slug_resolves_to_its_own_file(self):
        assert icon_path("relay").endswith("/relay.png")

    @pytest.mark.parametrize("slug", ["", "nope", "../etc/passwd", "relay.png"])
    def test_unknown_slug_falls_back_to_the_default(self, slug):
        """A stale config must never leave the bar item with no image."""
        assert icon_path(slug).endswith(f"/{DEFAULT_ICON}.png")

    def test_fallback_file_actually_exists(self):
        from pathlib import Path
        assert Path(icon_path("nope")).is_file()

    def test_is_known_rejects_what_is_not_shipped(self):
        assert is_known("relay") is True
        assert is_known("nope") is False


class TestIconSetting:
    def test_defaults_when_absent(self, tmp_path):
        assert load_settings(tmp_path / "accounts.json").icon == DEFAULT_ICON

    def test_round_trips(self, tmp_path):
        p = tmp_path / "accounts.json"
        set_icon("relay-bold", p)
        assert load_settings(p).icon == "relay-bold"

    def test_a_non_string_in_the_file_falls_back(self, tmp_path):
        p = tmp_path / "accounts.json"
        p.write_text(json.dumps({"version": 2, "settings": {"icon": 17}, "accounts": []}))
        assert load_settings(p).icon == DEFAULT_ICON

    def test_setting_the_icon_leaves_auto_switch_alone(self, tmp_path):
        p = tmp_path / "accounts.json"
        save_settings(AppSettings(auto_switch={"claude": True, "codex": False}), p)
        set_icon("graph", p)
        s = load_settings(p)
        assert s.icon == "graph"
        assert s.auto_switch["claude"] is True
