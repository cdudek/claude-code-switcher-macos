"""
py2app build script.

Usage:
    python setup.py py2app

This creates a standalone macOS .app bundle in the dist/ folder.
The .app is fully standalone — no Python install needed on the target machine.
"""

import re
from pathlib import Path

from setuptools import setup

# One source of truth. Two hardcoded copies drifted apart the moment a release
# was cut from a branch, and the release workflow checks the tag against this.
VERSION = re.search(
    r'__version__ = "([^"]+)"',
    Path("src/code_agent_switcher/__init__.py").read_text(encoding="utf-8"),
).group(1)

# py2app conflicts with pyproject.toml's install_requires,
# so we keep this file minimal and self-contained.
APP = ["src/code_agent_switcher/app.py"]
ICON = "src/code_agent_switcher/resources/AppIcon.icns"

OPTIONS = {
    "argv_emulation": False,
    # Without this py2app ships its own PythonApplet.icns and the app shows up
    # in Finder, the Dock and the Trash as a generic Python rocket.
    "iconfile": ICON,
    # LSUIElement=True = menu bar app only (no Dock icon, no Cmd+Tab entry)
    "plist": {
        "CFBundleName": "Code Agent Switcher",
        "CFBundleDisplayName": "Code Agent Switcher",
        "CFBundleIdentifier": "com.emilejouannet.claude-switcher",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,
        "LSMinimumSystemVersion": "12.0",
    },
    # Include our package + rumps and its dependencies
    "packages": ["code_agent_switcher", "rumps"],
    "includes": ["objc", "Foundation", "AppKit"],
    "excludes": ["pytest", "_pytest", "pygments", "iniconfig", "pluggy", "setuptools.tests"],
    "resources": ["src/code_agent_switcher/resources"],
}

setup(
    app=APP,
    name="Code Agent Switcher",
    options={"py2app": OPTIONS},
)
