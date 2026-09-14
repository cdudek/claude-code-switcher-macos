"""Self-update from GitHub Releases.

No Sparkle: it wants a Developer ID signature and an EdDSA-signed appcast, and
this app has neither. What it does instead is the same shape, smaller: ask the
releases API what the newest tag is, download that release's zip, check it really
contains an app bundle, then hand the swap to a detached script because a running
bundle cannot replace itself.

The download is not signed, so the only thing standing between this and running
arbitrary code is that the URL must be an HTTPS github.com asset on this repo and
the archive must unpack to exactly one .app. Both are enforced below.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from claude_switcher import __version__

REPO = "cdudek/claude-code-switcher-macos"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
APP_NAME = "Claude Switcher.app"
NETWORK_TIMEOUT = 30
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """Parse a `v1.2.3` tag into comparable parts, or None if it is not one."""
    if not text:
        return None
    m = _VERSION_RE.match(text.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def current_version() -> str:
    """The running app's version.

    Inside a bundle the Info.plist is the truth: py2app freezes a copy of the
    package, so __version__ is whatever was compiled in, which is the same thing
    today but drifts the moment a build is made from a dirty tree.
    """
    plist = Path(sys.executable).parent.parent / "Info.plist"
    try:
        with plist.open("rb") as fh:
            value = plistlib.load(fh).get("CFBundleShortVersionString")
        if isinstance(value, str) and value:
            return value
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    return __version__


def is_newer(candidate: str, installed: str) -> bool:
    """True if `candidate` is a strictly higher version than `installed`."""
    a, b = parse_version(candidate), parse_version(installed)
    return bool(a and b and a > b)


def _asset_url(release: dict) -> str | None:
    """The one downloadable .zip on a release, if there is exactly one."""
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    zips = [
        a.get("browser_download_url")
        for a in assets
        if isinstance(a, dict)
        and isinstance(a.get("browser_download_url"), str)
        and a["browser_download_url"].endswith(".zip")
    ]
    return zips[0] if len(zips) == 1 else None


def is_trusted_asset(url: str) -> bool:
    """Only an HTTPS GitHub asset belonging to this repo may be downloaded.

    The archive is unsigned, so this check is the trust boundary. A release body
    is attacker-controlled text as far as this app is concerned; the URL is not
    allowed to come from anywhere else.
    """
    return url.startswith(f"https://github.com/{REPO}/releases/download/")


def check_for_update() -> tuple[str, str, str] | None:
    """Return (version, zip url, release notes) when a newer release exists.

    Returns None for "up to date" and for every failure: a missing update is not
    worth interrupting anyone over.
    """
    req = Request(RELEASES_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"claude-switcher/{current_version()}",
    })
    try:
        with urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None

    tag = release.get("tag_name")
    if not isinstance(tag, str) or not is_newer(tag, current_version()):
        return None
    url = _asset_url(release)
    if not url or not is_trusted_asset(url):
        return None
    notes = release.get("body") or ""
    return tag, url, notes if isinstance(notes, str) else ""


def _safe_members(zf: zipfile.ZipFile) -> list[str]:
    """Reject an archive that would write outside the directory it unpacks into.

    A zip entry may name `../../x` or an absolute path; unpacking it blindly is
    the zip-slip bug. Every entry here must stay under `<something>.app/`.
    """
    names = zf.namelist()
    for name in names:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe path in archive: {name}")
    return names


def download_update(url: str, into: Path) -> Path:
    """Download and unpack a release zip, returning the .app inside it.

    Raises rather than returning None: every failure here is worth telling the
    user about, because they asked for the update.
    """
    if not is_trusted_asset(url):
        raise ValueError("refusing to download from an untrusted URL")

    archive = into / "update.zip"
    req = Request(url, headers={"User-Agent": f"claude-switcher/{current_version()}"})
    with urlopen(req, timeout=NETWORK_TIMEOUT) as resp, archive.open("wb") as out:
        read = 0
        while chunk := resp.read(65536):
            read += len(chunk)
            if read > MAX_DOWNLOAD_BYTES:
                raise ValueError("update archive is implausibly large")
            out.write(chunk)

    unpacked = into / "unpacked"
    with zipfile.ZipFile(archive) as zf:
        _safe_members(zf)
        zf.extractall(unpacked)

    apps = [p for p in unpacked.rglob("*.app") if p.is_dir()]
    apps = [p for p in apps if "__MACOSX" not in p.parts]
    if len(apps) != 1:
        raise ValueError(f"expected exactly one .app in the archive, found {len(apps)}")
    app = apps[0]
    if not (app / "Contents" / "Info.plist").is_file():
        raise ValueError("the .app in the archive has no Info.plist")
    return app


def install_update(staged_app: Path, installed_app: Path | None = None) -> None:
    """Replace the installed app with the staged one and relaunch.

    A bundle cannot overwrite itself while its own executable is mapped, so the
    swap runs in a detached shell script that first waits for this process to
    exit. The old bundle goes to the Trash rather than being deleted - if the new
    build does not launch, the previous one is one drag away.
    """
    target = installed_app or Path("/Applications") / APP_NAME
    script = Path(tempfile.mkdtemp(prefix="cs-update-")) / "swap.sh"
    trash = Path.home() / ".Trash" / f"Claude Switcher (replaced {os.getpid()}).app"
    script.write_text(f"""#!/bin/bash
set -e
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
[ -d {shlex.quote(str(target))} ] && mv {shlex.quote(str(target))} {shlex.quote(str(trash))}
mv {shlex.quote(str(staged_app))} {shlex.quote(str(target))}
xattr -dr com.apple.quarantine {shlex.quote(str(target))} 2>/dev/null || true
open {shlex.quote(str(target))}
""")
    script.chmod(0o700)
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True)


def cleanup(directory: Path) -> None:
    """Best-effort removal of a staging directory."""
    shutil.rmtree(directory, ignore_errors=True)
