"""Tests for self-update.

The archive is downloaded over the internet and then becomes the app the user
runs, so the tests that matter here are the ones that refuse things: an untrusted
URL, a zip that escapes its directory, an archive with no app in it.
"""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_switcher.updater import (
    REPO,
    _asset_url,
    check_for_update,
    download_update,
    is_newer,
    is_trusted_asset,
    parse_version,
)

GOOD_URL = f"https://github.com/{REPO}/releases/download/v9.9.9/Claude-Switcher-v9.9.9.zip"


class TestVersionCompare:
    @pytest.mark.parametrize("text,expected", [
        ("v1.2.3", (1, 2, 3)), ("1.2.3", (1, 2, 3)), ("v10.0.1", (10, 0, 1)),
        ("v1.2.3-beta.1", (1, 2, 3)),
    ])
    def test_parses(self, text, expected):
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", [None, "", "latest", "v1.2", "nightly"])
    def test_rejects_non_versions(self, text):
        assert parse_version(text) is None

    def test_newer(self):
        assert is_newer("v0.5.0", "0.4.3") is True
        assert is_newer("v0.4.4", "0.4.3") is True
        assert is_newer("v1.0.0", "0.9.9") is True

    def test_not_newer(self):
        assert is_newer("v0.4.3", "0.4.3") is False
        assert is_newer("v0.4.2", "0.4.3") is False

    def test_compares_numerically_not_as_strings(self):
        """String order would put v0.10.0 before v0.9.0."""
        assert is_newer("v0.10.0", "0.9.0") is True
        assert is_newer("v0.9.0", "0.10.0") is False

    def test_unparseable_is_never_newer(self):
        assert is_newer("latest", "0.4.3") is False
        assert is_newer("v1.0.0", "unknown") is False


class TestTrustedAsset:
    def test_accepts_this_repos_release_asset(self):
        assert is_trusted_asset(GOOD_URL) is True

    @pytest.mark.parametrize("url", [
        "http://github.com/cdudek/claude-code-switcher-macos/releases/download/v1/a.zip",
        "https://github.com/someone-else/evil/releases/download/v1/a.zip",
        "https://evil.example.com/a.zip",
        f"https://evil.example.com/https://github.com/{REPO}/releases/download/v1/a.zip",
        "",
    ])
    def test_refuses_anything_else(self, url):
        """The archive is unsigned, so the URL check is the whole trust boundary."""
        assert is_trusted_asset(url) is False

    def test_download_refuses_an_untrusted_url(self, tmp_path):
        with pytest.raises(ValueError, match="untrusted"):
            download_update("https://evil.example.com/a.zip", tmp_path)


class TestAssetSelection:
    def test_picks_the_single_zip(self):
        r = {"assets": [{"browser_download_url": GOOD_URL},
                        {"browser_download_url": "https://x/notes.txt"}]}
        assert _asset_url(r) == GOOD_URL

    def test_refuses_when_there_are_two_zips(self):
        """Ambiguity here means guessing which binary to run."""
        r = {"assets": [{"browser_download_url": GOOD_URL},
                        {"browser_download_url": GOOD_URL.replace("v9.9.9", "v9.9.8")}]}
        assert _asset_url(r) is None

    @pytest.mark.parametrize("release", [{}, {"assets": None}, {"assets": []}, {"assets": [{}]}])
    def test_handles_a_release_with_no_usable_asset(self, release):
        assert _asset_url(release) is None


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestDownloadValidation:
    def _serve(self, payload: bytes):
        resp = MagicMock()
        chunks = [payload[i:i+65536] for i in range(0, len(payload), 65536)] + [b""]
        resp.read.side_effect = chunks
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        return resp

    @patch("claude_switcher.updater.urlopen")
    def test_unpacks_a_good_archive(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_zip({
            "Claude Switcher.app/Contents/Info.plist": b"<plist/>",
            "Claude Switcher.app/Contents/MacOS/Claude Switcher": b"bin",
        }))
        app = download_update(GOOD_URL, tmp_path)
        assert app.name == "Claude Switcher.app"
        assert (app / "Contents" / "Info.plist").is_file()

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_a_zip_that_escapes_its_directory(self, mock_open, tmp_path):
        """Zip slip: an entry naming ../ would overwrite files outside the staging dir."""
        mock_open.return_value = self._serve(_zip({
            "Claude Switcher.app/Contents/Info.plist": b"<plist/>",
            "../../../../tmp/pwned": b"x",
        }))
        with pytest.raises(ValueError, match="unsafe path"):
            download_update(GOOD_URL, tmp_path)
        assert not (tmp_path.parent / "pwned").exists()

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_archive_with_no_app(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_zip({"readme.txt": b"hi"}))
        with pytest.raises(ValueError, match="exactly one"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_archive_with_two_apps(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_zip({
            "A.app/Contents/Info.plist": b"<plist/>",
            "B.app/Contents/Info.plist": b"<plist/>",
        }))
        with pytest.raises(ValueError, match="exactly one"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_app_without_an_info_plist(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_zip({"Claude Switcher.app/Contents/MacOS/x": b"b"}))
        with pytest.raises(ValueError, match="Info.plist"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.MAX_DOWNLOAD_BYTES", 128)
    @patch("claude_switcher.updater.urlopen")
    def test_stops_an_oversized_download(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(b"x" * 5000)
        with pytest.raises(ValueError, match="implausibly large"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.urlopen")
    def test_ignores_the_macosx_metadata_folder(self, mock_open, tmp_path):
        """macOS' own zip writes __MACOSX/ alongside the real bundle."""
        mock_open.return_value = self._serve(_zip({
            "Claude Switcher.app/Contents/Info.plist": b"<plist/>",
            "__MACOSX/Claude Switcher.app/Contents/Info.plist": b"junk",
        }))
        assert download_update(GOOD_URL, tmp_path).name == "Claude Switcher.app"


class TestCheckForUpdate:
    def _api(self, payload):
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        return resp

    @patch("claude_switcher.updater.current_version", return_value="0.4.3")
    @patch("claude_switcher.updater.urlopen")
    def test_finds_a_newer_release(self, mock_open, _v):
        mock_open.return_value = self._api({
            "tag_name": "v9.9.9", "body": "notes",
            "assets": [{"browser_download_url": GOOD_URL}],
        })
        assert check_for_update() == ("v9.9.9", GOOD_URL, "notes")

    @patch("claude_switcher.updater.current_version", return_value="9.9.9")
    @patch("claude_switcher.updater.urlopen")
    def test_same_version_is_no_update(self, mock_open, _v):
        mock_open.return_value = self._api({
            "tag_name": "v9.9.9", "assets": [{"browser_download_url": GOOD_URL}]})
        assert check_for_update() is None

    @patch("claude_switcher.updater.current_version", return_value="0.4.3")
    @patch("claude_switcher.updater.urlopen")
    @pytest.mark.parametrize("flag", ["draft", "prerelease"])
    def test_drafts_and_prereleases_are_skipped(self, mock_open, _v, flag):
        mock_open.return_value = self._api({
            "tag_name": "v9.9.9", flag: True,
            "assets": [{"browser_download_url": GOOD_URL}]})
        assert check_for_update() is None

    @patch("claude_switcher.updater.current_version", return_value="0.4.3")
    @patch("claude_switcher.updater.urlopen")
    def test_a_release_pointing_off_github_is_ignored(self, mock_open, _v):
        mock_open.return_value = self._api({
            "tag_name": "v9.9.9",
            "assets": [{"browser_download_url": "https://evil.example.com/a.zip"}]})
        assert check_for_update() is None

    @patch("claude_switcher.updater.urlopen", side_effect=OSError("offline"))
    def test_being_offline_is_silent(self, _):
        """A failed check must never interrupt anyone."""
        assert check_for_update() is None

    @patch("claude_switcher.updater.urlopen")
    def test_garbage_json_is_silent(self, mock_open):
        resp = MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        mock_open.return_value = resp
        assert check_for_update() is None
