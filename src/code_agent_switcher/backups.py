"""Keep a copy of a file before this app overwrites it.

Three files here belong to something else and get rewritten on a switch:
`~/.claude.json` is Claude Code's own state, `~/.codex/auth.json` is Codex's
credentials, and `accounts.json` is this app's record of what it knows. A bad
write to any of them is not a bug you can undo from the UI, and the first two
are not this app's to lose.

Best effort by design: a failed backup must never stop a switch. Losing the
ability to switch accounts because a disk is full is worse than switching
without a copy, and the copy is insurance, not the operation.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

BACKUP_DIR = (
    Path.home() / "Library" / "Application Support" / "Code Agent Switcher" / "backups"
)

# Per source file, not in total: a file written on every switch must not push
# another file's only copy out of the directory.
KEEP_PER_FILE = 20


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


def snapshot(path: Path, directory: Path | None = None, now: datetime | None = None):
    """Copy `path` aside. Returns the copy, or None when there was nothing to do.

    A file that does not exist yet has nothing worth keeping, and that is the
    normal case on a first run rather than an error.
    """
    # Resolved here, not as a default argument: a default binds at import, so
    # BACKUP_DIR could not be redirected afterwards - which is how the test
    # suite ended up writing into the real backup directory.
    directory = directory or BACKUP_DIR
    try:
        if not path.is_file():
            return None
        directory.mkdir(parents=True, exist_ok=True)
        # The source name is kept whole, dots included, so ".claude.json" and
        # "auth.json" cannot collide and each prunes only its own history.
        target = directory / f"{path.name}.{_stamp(now)}"
        # A second write inside the same second would otherwise overwrite the
        # copy it just made, which is exactly when two writes are worth keeping.
        suffix = 1
        while target.exists():
            target = directory / f"{path.name}.{_stamp(now)}.{suffix}"
            suffix += 1
        shutil.copy2(path, target)
        prune(path.name, directory)
        return target
    except OSError:
        return None


def prune(name: str, directory: Path | None = None, keep: int = KEEP_PER_FILE) -> None:
    """Drop all but the newest `keep` copies of one source file."""
    directory = directory or BACKUP_DIR
    try:
        copies = sorted(directory.glob(f"{name}.*"))
        for old in copies[:-keep] if keep else copies:
            old.unlink(missing_ok=True)
    except OSError:
        pass
