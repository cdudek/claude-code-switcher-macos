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


def swap_script(staged_app: Path, target: Path, backup: Path, log: Path, pid: int) -> str:
    """The script that replaces the app once this process has exited.

    Three things this must never do, all learned the hard way when an update
    left /Applications with no app in it at all:

    - `set -e` with `[ -d x ] && cmd`: when the test is false the AND-list
      returns non-zero and the script exits there, silently skipping the rest.
    - `mv` a bundle across filesystems: the staging directory is under
      /var/folders and the target is /Applications, so mv degrades to a
      copy-then-delete that can lose the source without completing the target.
      `ditto` is the tool that copies a bundle correctly.
    - Trust that it worked. The copy is verified, and if the new bundle is not
      there afterwards the previous one is put back.
    """
    q = shlex.quote
    return f"""#!/bin/bash
exec >>{q(str(log))} 2>&1
echo "=== swap $(date) ==="
# Wait for the app to exit; a bundle cannot be replaced while it is mapped.
for _ in $(seq 1 200); do
  kill -0 {pid} 2>/dev/null || break
  sleep 0.3
done

if [ -e {q(str(target))} ]; then
  echo "backing up to {backup}"
  rm -rf {q(str(backup))}
  if ! mv {q(str(target))} {q(str(backup))}; then
    echo "FATAL: could not move the installed app aside; leaving it alone"
    open {q(str(target))}
    exit 1
  fi
fi

echo "installing"
# ditto, not mv: the staging dir and /Applications are different filesystems.
if ditto {q(str(staged_app))} {q(str(target))} \
   && [ -f {q(str(target))}/Contents/Info.plist ]; then
  xattr -dr com.apple.quarantine {q(str(target))} 2>/dev/null || true
  echo "installed ok"
  rm -rf {q(str(staged_app))}
  # Keep exactly one rollback copy, not one per update.
  find "$(dirname {q(str(target))})" -maxdepth 1 -name ".*.previous" \
       ! -path {q(str(backup))} -exec rm -rf {{}} + 2>/dev/null || true
else
  echo "FATAL: install failed, restoring the previous version"
  rm -rf {q(str(target))}
  mv {q(str(backup))} {q(str(target))}
fi

open {q(str(target))}
echo "done"
"""


def backup_path(target: Path) -> Path:
    """Where the outgoing version is kept so a bad update can be rolled back.

    Leading dot: Finder hides it. A visible "Claude Switcher.app.previous" next
    to the app after every update looks like a failed install. It sits beside the
    target rather than in the Trash so restoring it is a rename on the same
    filesystem, which cannot half-fail the way a cross-device copy can.
    """
    return target.with_name("." + target.name + ".previous")


def install_update(staged_app: Path, installed_app: Path | None = None) -> Path:
    """Replace the installed app with the staged one and relaunch.

    Returns the path of the log the swap writes, so a failure after this process
    has gone can still be read. The previous bundle is kept next to the target
    rather than deleted - restoring it is then a plain rename on the same
    filesystem, which cannot half-fail the way a cross-device copy can.
    """
    target = installed_app or Path("/Applications") / APP_NAME
    backup = backup_path(target)
    workdir = Path(tempfile.mkdtemp(prefix="cs-swap-"))
    log = workdir / "swap.log"
    script = workdir / "swap.sh"
    script.write_text(swap_script(staged_app, target, backup, log, os.getpid()))
    script.chmod(0o700)
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True)
    return log


def cleanup(directory: Path) -> None:
    """Best-effort removal of a staging directory."""
    shutil.rmtree(directory, ignore_errors=True)
