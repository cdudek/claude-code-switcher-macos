"""Tests for self-update.

The archive is downloaded over the internet and then becomes the app the user
runs, so the tests that matter here are the ones that refuse things: an untrusted
URL, a zip that escapes its directory, an archive with no app in it.
"""

import io
import os
import stat
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
    swap_script,
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


def _plist(executable: str | None = "Claude Switcher") -> bytes:
    import plistlib
    return plistlib.dumps({"CFBundleExecutable": executable} if executable else {})


def _zip(entries: dict[str, bytes], executable: tuple[str, ...] = ()) -> bytes:
    """Build an archive, carrying the Unix mode the way a real macOS zip does."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            mode = 0o755 if name in executable else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)
    return buf.getvalue()


APP = "Claude Switcher.app"
EXE = f"{APP}/Contents/MacOS/Claude Switcher"


def _good_app(**overrides) -> bytes:
    entries = {f"{APP}/Contents/Info.plist": _plist(), EXE: b"bin"}
    entries.update(overrides.pop("entries", {}))
    return _zip(entries, executable=overrides.pop("executable", (EXE,)))


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
        mock_open.return_value = self._serve(_good_app())
        app = download_update(GOOD_URL, tmp_path)
        assert app.name == "Claude Switcher.app"
        assert (app / "Contents" / "Info.plist").is_file()

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_a_zip_that_escapes_its_directory(self, mock_open, tmp_path):
        """Zip slip: an entry naming ../ would overwrite files outside the staging dir."""
        mock_open.return_value = self._serve(_zip({
            "Claude Switcher.app/Contents/Info.plist": _plist(),
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
            "A.app/Contents/Info.plist": _plist(),
            "B.app/Contents/Info.plist": _plist(),
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
        mock_open.return_value = self._serve(_good_app(entries={
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


class TestSwapScript:
    """The swap runs after this process is gone, so it gets tested as a script.

    An update once left /Applications with no app in it and nothing in the Trash.
    These run the real generated bash against throwaway directories.
    """

    def _bundle(self, root: Path, name: str, version: str) -> Path:
        app = root / name
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_text(f"<plist>{version}</plist>")
        (app / "Contents" / "MacOS" / "run").write_text("#!/bin/bash\ntrue\n")
        return app

    def _run(self, staged, target, backup, log, tmp):
        from claude_switcher.updater import swap_script
        import subprocess as sp
        script = tmp / "swap.sh"
        # pid 1 is always alive, so use this process's pid: it has already
        # "exited" from the script's point of view only if we pass a dead one.
        script.write_text(swap_script(staged, target, backup, log, 999999))
        script.chmod(0o700)
        # `open` is not wanted in a test; stub it on PATH.
        stub = tmp / "bin"
        stub.mkdir(exist_ok=True)
        (stub / "open").write_text("#!/bin/bash\nexit 0\n")
        (stub / "open").chmod(0o755)
        env = dict(**{**__import__("os").environ, "PATH": f"{stub}:/usr/bin:/bin:/usr/sbin"})
        return sp.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=60)

    def test_replaces_the_installed_app(self, tmp_path):
        staged = self._bundle(tmp_path / "stage", "Claude Switcher.app", "0.5.0")
        target = self._bundle(tmp_path / "apps", "Claude Switcher.app", "0.4.3")
        from claude_switcher.updater import backup_path
        backup = backup_path(target)
        log = tmp_path / "swap.log"

        self._run(staged, target, backup, log, tmp_path)

        assert target.is_dir(), f"target gone. log:\n{log.read_text() if log.exists() else '(none)'}"
        assert "0.5.0" in (target / "Contents" / "Info.plist").read_text()
        assert backup.is_dir(), "the previous version must be kept, not deleted"
        assert backup.name.startswith("."), "the rollback copy must be hidden from Finder"
        assert "0.4.3" in (backup / "Contents" / "Info.plist").read_text()

    def test_works_when_nothing_is_installed_yet(self, tmp_path):
        """`set -e` with `[ -d x ] && mv` used to exit here and install nothing."""
        staged = self._bundle(tmp_path / "stage", "Claude Switcher.app", "0.5.0")
        target = tmp_path / "apps" / "Claude Switcher.app"
        target.parent.mkdir()
        log = tmp_path / "swap.log"

        self._run(staged, target, __import__("claude_switcher.updater", fromlist=["x"]).backup_path(target), log, tmp_path)

        assert target.is_dir(), f"nothing installed. log:\n{log.read_text() if log.exists() else '(none)'}"
        assert "0.5.0" in (target / "Contents" / "Info.plist").read_text()

    def test_restores_the_previous_version_when_the_copy_fails(self, tmp_path):
        """A staged app that has vanished must not cost the user the working one."""
        staged = tmp_path / "stage" / "Claude Switcher.app"   # never created
        target = self._bundle(tmp_path / "apps", "Claude Switcher.app", "0.4.3")
        from claude_switcher.updater import backup_path
        backup = backup_path(target)
        log = tmp_path / "swap.log"

        self._run(staged, target, backup, log, tmp_path)

        assert target.is_dir(), "the previous version was not restored"
        assert "0.4.3" in (target / "Contents" / "Info.plist").read_text()

    def test_writes_a_log(self, tmp_path):
        staged = self._bundle(tmp_path / "stage", "Claude Switcher.app", "0.5.0")
        target = self._bundle(tmp_path / "apps", "Claude Switcher.app", "0.4.3")
        log = tmp_path / "swap.log"
        self._run(staged, target, __import__("claude_switcher.updater", fromlist=["x"]).backup_path(target), log, tmp_path)
        assert log.is_file() and "installed ok" in log.read_text()


class TestBackupPath:
    """Derived by the code under test, not recomputed by the test."""

    def test_is_hidden_from_finder(self):
        from claude_switcher.updater import backup_path
        b = backup_path(Path("/Applications/Claude Switcher.app"))
        assert b.name.startswith("."), f"{b.name} would be visible in /Applications"

    def test_sits_beside_the_target(self):
        """Restoring must be a rename on one filesystem, not a copy across two."""
        from claude_switcher.updater import backup_path
        t = Path("/Applications/Claude Switcher.app")
        assert backup_path(t).parent == t.parent

    def test_names_the_app_it_backs_up(self):
        from claude_switcher.updater import backup_path
        assert backup_path(Path("/x/Foo.app")).name == ".Foo.app.previous"


class TestUnpackedBundleMustBeLaunchable:
    """v0.7.3 shipped an app nobody could open: zipfile.extractall drops the
    executable bit, so launchd answered "Launch failed" (POSIX 111)."""

    def _serve(self, payload: bytes):
        resp = MagicMock()
        chunks = [payload[i:i+65536] for i in range(0, len(payload), 65536)] + [b""]
        resp.read.side_effect = chunks
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        return resp

    @patch("claude_switcher.updater.urlopen")
    def test_the_executable_bit_survives_unpacking(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_good_app())
        app = download_update(GOOD_URL, tmp_path)
        exe = app / "Contents" / "MacOS" / "Claude Switcher"
        assert os.access(exe, os.X_OK)

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_app_whose_executable_is_not_executable(self, mock_open, tmp_path):
        mock_open.return_value = self._serve(_good_app(executable=()))
        with pytest.raises(ValueError, match="not executable"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_app_with_no_cfbundleexecutable(self, mock_open, tmp_path):
        payload = _zip({f"{APP}/Contents/Info.plist": _plist(None), EXE: b"bin"},
                       executable=(EXE,))
        mock_open.return_value = self._serve(payload)
        with pytest.raises(ValueError, match="CFBundleExecutable"):
            download_update(GOOD_URL, tmp_path)

    @patch("claude_switcher.updater.urlopen")
    def test_rejects_an_app_with_no_executable_file(self, mock_open, tmp_path):
        payload = _zip({f"{APP}/Contents/Info.plist": _plist(),
                        f"{APP}/Contents/Resources/x": b"y"})
        mock_open.return_value = self._serve(payload)
        with pytest.raises(ValueError, match="no executable at"):
            download_update(GOOD_URL, tmp_path)


class TestLegacyBackupIsSwept:
    def test_the_undotted_pre_0_7_1_backup_is_removed_too(self):
        script = swap_script(Path("/s/App.app"), Path("/Applications/App.app"),
                             Path("/Applications/.App.app.previous"),
                             Path("/tmp/l.log"), 42)
        assert '-name "*.app.previous"' in script
        assert '-name ".*.previous"' in script
