import io
import json
import time
from unittest.mock import patch, MagicMock
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from claude_switcher.core import (
    check_claude_cli,
    get_auth_status,
    run_auth_logout,
    run_auth_login,
    import_current_account,
    switch_account,
    has_valid_tokens,
    refresh_claude_credentials,
    ClaudeCredentialsExpiredError,
    add_new_account,
    remove_saved_account,
)
from claude_switcher.config import AccountInfo


class TestClaudeCLI:
    @patch("claude_switcher.core.shutil.which")
    def test_check_cli_found(self, mock_which):
        mock_which.return_value = "/usr/local/bin/claude"
        assert check_claude_cli() is True

    @patch("claude_switcher.core.Path.is_file", return_value=False)
    @patch("claude_switcher.core.shutil.which", return_value=None)
    def test_check_cli_not_found(self, mock_which, mock_is_file):
        assert check_claude_cli() is False

    @patch("claude_switcher.core.subprocess.run")
    def test_get_auth_status(self, mock_run):
        status = {"loggedIn": True, "email": "test@test.com", "subscriptionType": "pro", "orgName": "Org"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(status))
        result = get_auth_status()
        assert result["email"] == "test@test.com"

    @patch("claude_switcher.core.subprocess.run")
    def test_get_auth_status_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_auth_status()
        assert result is None


class TestImportCurrentAccount:
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_import_success(self, mock_kc, mock_status, mock_oauth, tmp_path):
        mock_status.return_value = {"email": "test@test.com", "subscriptionType": "pro", "orgName": "Org"}
        mock_kc.read_credentials.return_value = '{"accessToken":"tok","refreshToken":"ref"}'
        mock_kc.read_account_attribute.return_value = "testuser"
        mock_oauth.return_value = {"emailAddress": "test@test.com"}

        config_path = tmp_path / "accounts.json"
        result = import_current_account(config_path)

        assert result is not None
        assert result.email == "test@test.com"
        assert result.oauth_account == {"emailAddress": "test@test.com"}
        mock_kc.write_credentials.assert_called_once_with(
            "claude-switcher:test@test.com", "testuser", '{"accessToken":"tok","refreshToken":"ref"}'
        )

    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_import_no_credentials(self, mock_kc, mock_status, tmp_path):
        mock_kc.read_credentials.return_value = None
        result = import_current_account(tmp_path / "accounts.json")
        assert result is None


class TestSwitchAccount:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_saves_current_then_loads_target(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org B", False, "userb",
                                oauth_account={"emailAddress": "b@test.com"}), config_path)

        mock_kc.read_credentials.side_effect = [
            '{"accessToken":"refreshed-a","refreshToken":"ref-a"}',
            '{"accessToken":"tok-b","refreshToken":"ref-b"}',
        ]
        mock_read_oauth.return_value = {"emailAddress": "a@test.com"}

        switch_account("b@test.com", config_path)

        mock_kc.write_credentials.assert_any_call(
            "claude-switcher:a@test.com", "usera", '{"accessToken":"refreshed-a","refreshToken":"ref-a"}'
        )
        mock_kc.write_credentials.assert_any_call(
            "Claude Code-credentials", "userb", '{"accessToken":"tok-b","refreshToken":"ref-b"}'
        )
        mock_write_oauth.assert_called_once_with({"emailAddress": "b@test.com"})

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_missing_keychain_entry_raises(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), config_path)
        mock_kc.read_credentials.side_effect = ['{"tok":"a"}', None]
        mock_read_oauth.return_value = None

        try:
            switch_account("b@test.com", config_path)
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "not found" in str(e).lower()


class TestAddNewAccount:
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login")
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_add_account_full_flow(self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)

        mock_kc.read_credentials.side_effect = [
            '{"accessToken":"tok-a","refreshToken":"ref-a"}',
            '{"accessToken":"tok-new","refreshToken":"ref-new"}',
        ]
        mock_kc.read_account_attribute.side_effect = ["newuser"]
        mock_kc.delete_credentials.return_value = False
        mock_login.return_value = True
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "pro", "orgName": "New Org"}
        mock_oauth.return_value = {"emailAddress": "new@test.com"}

        result = add_new_account(config_path)
        assert result is not None
        assert result.email == "new@test.com"
        mock_login.assert_called_once()

    @patch("claude_switcher.core.run_auth_login")
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_add_account_login_cancelled(self, mock_kc, mock_logout, mock_login, tmp_path):
        config_path = tmp_path / "accounts.json"
        mock_kc.read_credentials.return_value = None
        mock_kc.delete_credentials.return_value = False
        mock_login.return_value = False

        result = add_new_account(config_path)
        assert result is None


class TestRemoveSavedAccount:
    @patch("claude_switcher.core.keychain")
    def test_remove_account(self, mock_kc, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, AccountInfo
        add_account(AccountInfo("a@test.com", "pro", "Org", False, "u"), config_path)

        remove_saved_account("a@test.com", config_path)

        mock_kc.delete_credentials.assert_called_once_with("claude-switcher:a@test.com")
        from claude_switcher.config import load_accounts
        assert len(load_accounts(config_path)) == 0


class TestCoreWithMixedProviders:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account")
    @patch("claude_switcher.core.keychain")
    def test_switch_claude_ignores_codex_account_with_same_email(
        self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path
    ):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account, load_accounts

        add_account(
            AccountInfo("user@test.com", "pro", "", True, "claude-user", provider="claude"),
            config_path,
        )
        add_account(
            AccountInfo(
                "other@test.com",
                "pro",
                "",
                False,
                "claude-other",
                oauth_account={"emailAddress": "other@test.com"},
                provider="claude",
            ),
            config_path,
        )
        add_account(
            AccountInfo("user@test.com", "plus", "", True, "codex-user", provider="codex"),
            config_path,
        )
        mock_kc.read_credentials.side_effect = ['{"accessToken":"current","refreshToken":"ref-current"}', '{"accessToken":"target","refreshToken":"ref-target"}']
        mock_read_oauth.return_value = {"emailAddress": "user@test.com"}

        switch_account("other@test.com", config_path)

        accounts = load_accounts(config_path)
        codex = next(a for a in accounts if a.provider == "codex")
        claude_target = next(a for a in accounts if a.email == "other@test.com")
        assert codex.active is True
        assert claude_target.active is True
        mock_write_oauth.assert_called_once_with({"emailAddress": "other@test.com"})


class TestHasValidTokens:
    def test_full_blob_is_valid(self):
        blob = json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r"}})
        assert has_valid_tokens(blob) is True

    def test_legacy_top_level_blob_is_valid(self):
        assert has_valid_tokens('{"accessToken":"a","refreshToken":"r"}') is True

    def test_empty_token_strings_are_invalid(self):
        """The husk Claude Code writes for ~1s after login."""
        blob = json.dumps({
            "claudeAiOauth": {
                "accessToken": "",
                "refreshToken": "",
                "expiresAt": 0,
                "subscriptionType": "team",
            }
        })
        assert has_valid_tokens(blob) is False

    def test_missing_refresh_token_is_invalid(self):
        assert has_valid_tokens('{"claudeAiOauth":{"accessToken":"a"}}') is False

    def test_none_and_garbage_are_invalid(self):
        assert has_valid_tokens(None) is False
        assert has_valid_tokens("") is False
        assert has_valid_tokens("not json") is False
        assert has_valid_tokens("[]") is False


class TestImportRejectsTokenlessBlob:
    @patch("claude_switcher.core.time.sleep")
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_husk_is_never_snapshotted(self, mock_kc, mock_status, mock_sleep, tmp_path):
        husk = '{"claudeAiOauth":{"accessToken":"","refreshToken":"","expiresAt":0}}'
        mock_kc.read_credentials.return_value = husk

        assert import_current_account(tmp_path / "accounts.json") is None
        mock_kc.write_credentials.assert_not_called()

    @patch("claude_switcher.core.time.sleep")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.keychain")
    def test_retries_until_tokens_land(self, mock_kc, mock_status, mock_oauth, mock_sleep, tmp_path):
        husk = '{"claudeAiOauth":{"accessToken":"","refreshToken":""}}'
        good = '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"}}'
        mock_kc.read_credentials.side_effect = [husk, husk, good]
        mock_kc.read_account_attribute.return_value = "u"
        mock_status.return_value = {"email": "x@test.com", "subscriptionType": "max", "orgName": "O"}

        result = import_current_account(tmp_path / "accounts.json")

        assert result is not None and result.email == "x@test.com"
        mock_kc.write_credentials.assert_called_once_with("claude-switcher:x@test.com", "u", good)


class TestSwitchGuards:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.keychain")
    def test_tokenless_target_raises(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), config_path)
        mock_kc.read_credentials.side_effect = [
            '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"}}',
            '{"claudeAiOauth":{"accessToken":"","refreshToken":""}}',
        ]

        try:
            switch_account("b@test.com", config_path)
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "incomplete" in str(e).lower()
        mock_kc.write_credentials.assert_called_once_with(
            "claude-switcher:a@test.com", "u",
            '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"}}',
        )

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.keychain")
    def test_husk_does_not_clobber_current_snapshot(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)
        mock_kc.read_credentials.side_effect = [
            '{"claudeAiOauth":{"accessToken":"","refreshToken":""}}',
            '{"claudeAiOauth":{"accessToken":"b","refreshToken":"rb"}}',
        ]

        switch_account("b@test.com", config_path)

        for call in mock_kc.write_credentials.call_args_list:
            assert call.args[0] != "claude-switcher:a@test.com"


class TestMcpOAuthCarriedOver:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.keychain")
    def test_mcp_tokens_survive_a_switch(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)

        live = json.dumps({
            "claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"},
            "mcpOAuth": {"vercel": {"accessToken": "v"}, "notion": {"accessToken": "n"}},
        })
        target = json.dumps({"claudeAiOauth": {"accessToken": "b", "refreshToken": "rb"}})
        mock_kc.read_credentials.side_effect = [live, target]

        switch_account("b@test.com", config_path)

        written = next(
            c.args[2] for c in mock_kc.write_credentials.call_args_list
            if c.args[0] == "Claude Code-credentials"
        )
        blob = json.loads(written)
        assert blob["claudeAiOauth"]["accessToken"] == "b"
        assert blob["mcpOAuth"] == {"vercel": {"accessToken": "v"}, "notion": {"accessToken": "n"}}

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.keychain")
    def test_target_mcp_tokens_win(self, mock_kc, mock_read_oauth, mock_write_oauth, tmp_path):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)

        live = json.dumps({
            "claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"},
            "mcpOAuth": {"vercel": {"accessToken": "live"}, "notion": {"accessToken": "n"}},
        })
        target = json.dumps({
            "claudeAiOauth": {"accessToken": "b", "refreshToken": "rb"},
            "mcpOAuth": {"vercel": {"accessToken": "target"}},
        })
        mock_kc.read_credentials.side_effect = [live, target]

        switch_account("b@test.com", config_path)

        written = next(
            c.args[2] for c in mock_kc.write_credentials.call_args_list
            if c.args[0] == "Claude Code-credentials"
        )
        mcp = json.loads(written)["mcpOAuth"]
        assert mcp["vercel"]["accessToken"] == "target"
        assert mcp["notion"]["accessToken"] == "n"


class TestAddAccountPreservesMcpOAuth:
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login", return_value=True)
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_mcp_tokens_survive_add_account(
        self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path
    ):
        """`claude auth logout` wipes the whole blob, MCP server tokens included."""
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)

        before = json.dumps({
            "claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"},
            "mcpOAuth": {"vercel": {"accessToken": "v"}, "notion": {"accessToken": "n"}},
        })
        after_login = json.dumps({"claudeAiOauth": {"accessToken": "new", "refreshToken": "rn"}})
        # reads: pre-logout, import loop, _restore_mcp_oauth
        mock_kc.read_credentials.side_effect = [before, after_login, after_login]
        mock_kc.read_account_attribute.return_value = "newuser"
        mock_kc.delete_credentials.return_value = False
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "max", "orgName": "O"}

        result = add_new_account(config_path)

        assert result is not None and result.email == "new@test.com"
        final_live = next(
            c.args[2] for c in reversed(mock_kc.write_credentials.call_args_list)
            if c.args[0] == "Claude Code-credentials"
        )
        blob = json.loads(final_live)
        assert blob["claudeAiOauth"]["accessToken"] == "new"
        assert blob["mcpOAuth"] == {"vercel": {"accessToken": "v"}, "notion": {"accessToken": "n"}}

    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login", return_value=True)
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_no_mcp_tokens_to_preserve_is_a_noop(
        self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path
    ):
        config_path = tmp_path / "accounts.json"
        after_login = json.dumps({"claudeAiOauth": {"accessToken": "new", "refreshToken": "rn"}})
        mock_kc.read_credentials.side_effect = [None, after_login]
        mock_kc.read_account_attribute.return_value = "newuser"
        mock_kc.delete_credentials.return_value = False
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "max", "orgName": "O"}

        assert add_new_account(config_path) is not None
        for call in mock_kc.write_credentials.call_args_list:
            assert call.args[0] != "Claude Code-credentials"


@pytest.mark.real_refresh
class TestRefreshClaudeCredentials:
    def _blob(self, access="old", refresh="r-old", mcp=None):
        b = {"claudeAiOauth": {"accessToken": access, "refreshToken": refresh,
                               "expiresAt": 1, "subscriptionType": "max"}}
        if mcp:
            b["mcpOAuth"] = mcp
        return json.dumps(b)

    def _response(self, payload):
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        return resp

    @patch("claude_switcher.core.urlopen")
    def test_rotated_pair_is_written_into_the_blob(self, mock_open):
        mock_open.return_value = self._response(
            {"access_token": "new", "refresh_token": "r-new", "expires_in": 28800}
        )
        out = refresh_claude_credentials(self._blob(mcp={"vercel": {"accessToken": "v"}}))
        o = json.loads(out)
        assert o["claudeAiOauth"]["accessToken"] == "new"
        assert o["claudeAiOauth"]["refreshToken"] == "r-new"
        assert o["claudeAiOauth"]["expiresAt"] > time.time() * 1000
        assert o["claudeAiOauth"]["subscriptionType"] == "max"
        assert o["mcpOAuth"] == {"vercel": {"accessToken": "v"}}

    @patch("claude_switcher.core.urlopen")
    def test_server_keeping_the_refresh_token_leaves_it_alone(self, mock_open):
        mock_open.return_value = self._response({"access_token": "new", "expires_in": 100})
        o = json.loads(refresh_claude_credentials(self._blob()))
        assert o["claudeAiOauth"]["refreshToken"] == "r-old"

    @patch("claude_switcher.core.urlopen")
    def test_revoked_refresh_token_raises(self, mock_open):
        mock_open.side_effect = HTTPError(
            "u", 400, "Bad Request", {},
            io.BytesIO(b'{"error":"invalid_grant","error_description":'
                       b'"Refresh token not found or invalid"}'),
        )
        with pytest.raises(ClaudeCredentialsExpiredError):
            refresh_claude_credentials(self._blob())

    @patch("claude_switcher.core.urlopen")
    def test_server_error_is_transient_not_fatal(self, mock_open):
        mock_open.side_effect = HTTPError("u", 500, "Server Error", {}, io.BytesIO(b"boom"))
        assert refresh_claude_credentials(self._blob()) is None

    @patch("claude_switcher.core.urlopen")
    def test_offline_is_transient_not_fatal(self, mock_open):
        mock_open.side_effect = URLError("offline")
        assert refresh_claude_credentials(self._blob()) is None

    def test_blob_without_refresh_token_is_skipped(self):
        assert refresh_claude_credentials('{"claudeAiOauth":{"accessToken":"a"}}') is None


@pytest.mark.real_refresh
class TestSwitchRefreshesTheSnapshot:
    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.refresh_claude_credentials")
    @patch("claude_switcher.core.keychain")
    def test_refreshed_pair_lands_in_snapshot_and_live(
        self, mock_kc, mock_refresh, mock_read_oauth, mock_write_oauth, tmp_path
    ):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)

        live = json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"},
                           "mcpOAuth": {"vercel": {"accessToken": "v"}}})
        stale = json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "r-old"}})
        fresh = json.dumps({"claudeAiOauth": {"accessToken": "new", "refreshToken": "r-new"}})
        mock_kc.read_credentials.side_effect = [live, stale]
        mock_refresh.return_value = fresh

        switch_account("b@test.com", config_path)

        snapshot = next(c.args[2] for c in mock_kc.write_credentials.call_args_list
                        if c.args[0] == "claude-switcher:b@test.com")
        assert json.loads(snapshot)["claudeAiOauth"]["accessToken"] == "new"

        written = next(c.args[2] for c in mock_kc.write_credentials.call_args_list
                       if c.args[0] == "Claude Code-credentials")
        blob = json.loads(written)
        assert blob["claudeAiOauth"]["accessToken"] == "new"
        assert blob["mcpOAuth"] == {"vercel": {"accessToken": "v"}}

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.refresh_claude_credentials")
    @patch("claude_switcher.core.keychain")
    def test_revoked_snapshot_aborts_the_switch(
        self, mock_kc, mock_refresh, mock_read_oauth, mock_write_oauth, tmp_path
    ):
        """A dead snapshot must not be written live — that is the Login expired loop."""
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)

        live = json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"}})
        stale = json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "r-old"}})
        mock_kc.read_credentials.side_effect = [live, stale]
        mock_refresh.side_effect = ClaudeCredentialsExpiredError("dead")

        with pytest.raises(ClaudeCredentialsExpiredError):
            switch_account("b@test.com", config_path)

        for call in mock_kc.write_credentials.call_args_list:
            assert call.args[0] != "Claude Code-credentials"

    @patch("claude_switcher.core._write_oauth_account")
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.refresh_claude_credentials", return_value=None)
    @patch("claude_switcher.core.keychain")
    def test_offline_falls_back_to_stored_tokens(
        self, mock_kc, mock_refresh, mock_read_oauth, mock_write_oauth, tmp_path
    ):
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), config_path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "ub"), config_path)

        live = json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"}})
        stored = json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "r-old"}})
        mock_kc.read_credentials.side_effect = [live, stored]

        switch_account("b@test.com", config_path)

        written = next(c.args[2] for c in mock_kc.write_credentials.call_args_list
                       if c.args[0] == "Claude Code-credentials")
        assert json.loads(written)["claudeAiOauth"]["accessToken"] == "old"


class TestAddAccountDoesNotRevokeThePreviousOne:
    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login", return_value=True)
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_logout_is_never_run(
        self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path
    ):
        """`claude auth logout` revokes the outgoing account server-side.

        Those are the tokens saved a moment earlier as that account's snapshot, so
        calling it turned every Add Account into a silent loss of the previous
        account's saved session. Clearing the Keychain entry is all the login needs.
        """
        config_path = tmp_path / "accounts.json"
        from claude_switcher.config import add_account
        add_account(AccountInfo("a@test.com", "pro", "Org A", True, "usera"), config_path)

        good = '{"claudeAiOauth":{"accessToken":"a","refreshToken":"ra"}}'
        mock_kc.read_credentials.side_effect = [good, good, good]
        mock_kc.read_account_attribute.return_value = "newuser"
        mock_kc.delete_credentials.return_value = False
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "max", "orgName": "O"}

        assert add_new_account(config_path) is not None
        mock_logout.assert_not_called()

    @patch("claude_switcher.core._read_oauth_account", return_value=None)
    @patch("claude_switcher.core.get_auth_status")
    @patch("claude_switcher.core.run_auth_login", return_value=True)
    @patch("claude_switcher.core.run_auth_logout")
    @patch("claude_switcher.core.keychain")
    def test_keychain_entry_is_still_cleared_before_login(
        self, mock_kc, mock_logout, mock_login, mock_status, mock_oauth, tmp_path
    ):
        """Without the clear, the post-login read returns the stale token."""
        config_path = tmp_path / "accounts.json"
        good = '{"claudeAiOauth":{"accessToken":"a","refreshToken":"ra"}}'
        mock_kc.read_credentials.side_effect = [None, good, good]
        mock_kc.read_account_attribute.return_value = "newuser"
        mock_kc.delete_credentials.side_effect = [True, True, False]
        mock_status.return_value = {"email": "new@test.com", "subscriptionType": "max", "orgName": "O"}

        assert add_new_account(config_path) is not None
        assert mock_kc.delete_credentials.call_count == 3
        mock_kc.delete_credentials.assert_any_call("Claude Code-credentials")
