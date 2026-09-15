"""A switch rewrites files that belong to Claude Code and Codex. These cover the
copy taken first, and the rule that taking it can never block the write."""

from datetime import datetime
from pathlib import Path

from code_agent_switcher import backups

T = datetime(2026, 9, 15, 20, 30, 0)


def _src(tmp_path, text="{}"):
    p = tmp_path / ".claude.json"
    p.write_text(text)
    return p


class TestSnapshot:
    def test_it_copies_the_contents(self, tmp_path):
        src = _src(tmp_path, '{"a": 1}')
        out = backups.snapshot(src, tmp_path / "b", now=T)
        assert out.read_text() == '{"a": 1}'

    def test_the_copy_is_named_for_the_source_and_the_time(self, tmp_path):
        out = backups.snapshot(_src(tmp_path), tmp_path / "b", now=T)
        assert out.name == ".claude.json.20260915-203000"

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert backups.snapshot(tmp_path / "gone.json", tmp_path / "b") is None

    def test_a_directory_is_not_backed_up(self, tmp_path):
        (tmp_path / "d").mkdir()
        assert backups.snapshot(tmp_path / "d", tmp_path / "b") is None

    def test_two_writes_in_one_second_both_survive(self, tmp_path):
        """The one moment two copies are worth most is when something is
        rewriting the file in a loop."""
        src = _src(tmp_path, "first")
        first = backups.snapshot(src, tmp_path / "b", now=T)
        src.write_text("second")
        second = backups.snapshot(src, tmp_path / "b", now=T)
        assert first != second
        assert {first.read_text(), second.read_text()} == {"first", "second"}

    def test_an_unwritable_directory_does_not_raise(self, tmp_path, monkeypatch):
        """A failed backup must never stop a switch: not being able to switch
        because a disk is full is worse than switching without a copy."""
        monkeypatch.setattr(backups.shutil, "copy2",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
        assert backups.snapshot(_src(tmp_path), tmp_path / "b") is None


class TestPrune:
    def _fill(self, tmp_path, name, count):
        d = tmp_path / "b"
        d.mkdir(exist_ok=True)
        for i in range(count):
            (d / f"{name}.2026091{i // 10}-00000{i % 10}").write_text(str(i))
        return d

    def test_only_the_newest_are_kept(self, tmp_path):
        d = self._fill(tmp_path, ".claude.json", 25)
        backups.prune(".claude.json", d, keep=20)
        left = sorted(p.name for p in d.iterdir())
        assert len(left) == 20
        assert left[0] == ".claude.json.20260910-000005"

    def test_pruning_one_file_leaves_another_alone(self, tmp_path):
        """Per source file, not in total: a file rewritten on every switch must
        not push another file's only copy out of the directory."""
        d = self._fill(tmp_path, ".claude.json", 25)
        (d / "auth.json.20260901-000000").write_text("codex")
        backups.prune(".claude.json", d, keep=20)
        assert (d / "auth.json.20260901-000000").exists()
        # Counting the kept copies is the half that bites: a prune that globs
        # everything also leaves auth.json alone, but it pays for it by keeping
        # one fewer of the file it was actually asked to prune.
        assert len(list(d.glob(".claude.json.*"))) == 20

    def test_a_missing_directory_is_not_an_error(self, tmp_path):
        backups.prune(".claude.json", tmp_path / "nope", keep=5)


class TestEveryWriterTakesOne:
    """The copy is worthless if a writer forgets to take it, and forgetting is
    invisible until the day it matters."""

    def _spy(self, monkeypatch, module):
        seen = []
        monkeypatch.setattr(module.backups, "snapshot",
                            lambda path, *a, **k: seen.append(Path(path)) or None)
        return seen

    def test_writing_claude_state_backs_it_up_first(self, monkeypatch, tmp_path):
        from code_agent_switcher import core
        state = tmp_path / ".claude.json"
        state.write_text('{"oauthAccount": {"emailAddress": "old@x.com"}}')
        monkeypatch.setattr(core, "CLAUDE_STATE_FILE", state)
        seen = self._spy(monkeypatch, core)
        core._write_oauth_account({"emailAddress": "new@x.com"})
        assert seen == [state]
        assert "new@x.com" in state.read_text()

    def test_writing_codex_credentials_backs_them_up_first(self, monkeypatch, tmp_path):
        from code_agent_switcher import codex_core
        auth = tmp_path / "auth.json"
        auth.write_text("{}")
        monkeypatch.setattr(codex_core, "CODEX_AUTH_FILE", auth)
        seen = self._spy(monkeypatch, codex_core)
        codex_core._write_codex_credentials(
            '{"tokens": {"access_token": "a", "refresh_token": "r"}}')
        assert seen == [auth]

    def test_writing_the_account_list_backs_it_up_first(self, monkeypatch, tmp_path):
        from code_agent_switcher import config
        path = tmp_path / "accounts.json"
        path.write_text("{}")
        seen = self._spy(monkeypatch, config)
        config.save_accounts([], path)
        assert seen == [path]


class TestTheCopiesAreNotAWeakerCopy:
    """Two of these files carry live credentials, so a backup must not be an
    easier-to-read version of the original."""

    def test_the_directory_is_private(self, tmp_path):
        d = tmp_path / "b"
        backups.snapshot(_src(tmp_path), d)
        assert oct(d.stat().st_mode & 0o777) == "0o700"

    def test_the_copy_keeps_the_source_permissions(self, tmp_path):
        src = _src(tmp_path)
        src.chmod(0o600)
        out = backups.snapshot(src, tmp_path / "b")
        assert oct(out.stat().st_mode & 0o777) == "0o600"

    def test_only_five_copies_of_a_credential_file_are_kept(self, tmp_path):
        """Every extra copy is another plaintext copy of a working secret."""
        assert backups.KEEP_PER_FILE == 5
