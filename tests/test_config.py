import json
from pathlib import Path

from code_agent_switcher.config import (
    AccountInfo,
    AppSettings,
    load_accounts,
    save_accounts,
    add_account,
    remove_account,
    get_active_account,
    set_active_account,
    load_settings,
    save_settings,
    is_auto_switch_enabled,
    set_auto_switch_enabled,
)


class TestAccountInfo:
    def test_create_account(self):
        acc = AccountInfo(
            email="test@test.com",
            subscription_type="pro",
            org_name="Test Org",
            active=True,
            keychain_account="testuser",
        )
        assert acc.email == "test@test.com"
        assert acc.active is True


class TestLoadSave:
    def test_load_empty_returns_empty_list(self, tmp_path):
        result = load_accounts(tmp_path / "accounts.json")
        assert result == []

    def test_load_corrupted_json_returns_empty_list(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text("not valid json{{{")
        result = load_accounts(path)
        assert result == []

    def test_load_missing_accounts_key_returns_empty_list(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text('{"other": []}')
        result = load_accounts(path)
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "accounts.json"
        accounts = [
            AccountInfo("a@test.com", "pro", "Org A", True, "usera"),
            AccountInfo("b@test.com", "pro", "Org B", False, "userb"),
        ]
        save_accounts(accounts, path)
        loaded = load_accounts(path)
        assert len(loaded) == 2
        assert loaded[0].email == "a@test.com"
        assert loaded[1].active is False


class TestAccountOperations:
    def test_add_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        acc = AccountInfo("a@test.com", "pro", "Org", False, "usera")
        add_account(acc, path)
        loaded = load_accounts(path)
        assert len(loaded) == 1

    def test_add_existing_updates(self, tmp_path):
        path = tmp_path / "accounts.json"
        acc1 = AccountInfo("a@test.com", "pro", "Org", True, "usera")
        acc2 = AccountInfo("a@test.com", "team", "New Org", True, "usera")
        add_account(acc1, path)
        add_account(acc2, path)
        loaded = load_accounts(path)
        assert len(loaded) == 1
        assert loaded[0].subscription_type == "team"

    def test_remove_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        remove_account("a@test.com", path)
        loaded = load_accounts(path)
        assert len(loaded) == 1
        assert loaded[0].email == "b@test.com"

    def test_get_active_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        active = get_active_account(path)
        assert active is not None
        assert active.email == "a@test.com"

    def test_set_active_account(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("a@test.com", "pro", "Org", True, "u"), path)
        add_account(AccountInfo("b@test.com", "pro", "Org", False, "u"), path)
        set_active_account("b@test.com", path)
        loaded = load_accounts(path)
        assert loaded[0].active is False
        assert loaded[1].active is True


class TestProviderField:
    def test_default_provider_is_claude(self):
        acc = AccountInfo("test@test.com", "pro", "", True, "user")
        assert acc.provider == "claude"

    def test_codex_provider(self):
        acc = AccountInfo(
            "test@test.com",
            "plus",
            "",
            True,
            "user",
            provider="codex",
        )
        assert acc.provider == "codex"

    def test_load_legacy_config_defaults_to_claude(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({
            "accounts": [{
                "email": "old@test.com",
                "subscription_type": "pro",
                "org_name": "",
                "active": True,
                "keychain_account": "user",
            }]
        }))

        accounts = load_accounts(path)

        assert len(accounts) == 1
        assert accounts[0].provider == "claude"

    def test_same_email_different_providers(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("user@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("user@test.com", "plus", "", True, "u", provider="codex"), path)

        accounts = load_accounts(path)

        assert len(accounts) == 2
        assert {a.provider for a in accounts} == {"claude", "codex"}

    def test_remove_only_matching_provider(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("user@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("user@test.com", "plus", "", False, "u", provider="codex"), path)

        remove_account("user@test.com", path, provider="codex")

        accounts = load_accounts(path)
        assert len(accounts) == 1
        assert accounts[0].provider == "claude"

    def test_set_active_only_affects_same_provider(self, tmp_path):
        path = tmp_path / "accounts.json"
        add_account(AccountInfo("claude@test.com", "pro", "", True, "u", provider="claude"), path)
        add_account(AccountInfo("codex-a@test.com", "plus", "", True, "u", provider="codex"), path)
        add_account(AccountInfo("codex-b@test.com", "plus", "", False, "u", provider="codex"), path)

        set_active_account("codex-b@test.com", path, provider="codex")

        accounts = load_accounts(path)
        claude = next(a for a in accounts if a.provider == "claude")
        codex_a = next(a for a in accounts if a.email == "codex-a@test.com")
        codex_b = next(a for a in accounts if a.email == "codex-b@test.com")
        assert claude.active is True
        assert codex_a.active is False
        assert codex_b.active is True


class TestSettings:
    def test_default_auto_switch_disabled(self, tmp_path):
        path = tmp_path / "accounts.json"
        settings = load_settings(path)
        assert settings.auto_switch == {"claude": False, "codex": False}
        assert settings.auto_switch_threshold == 100.0

    def test_settings_roundtrip(self, tmp_path):
        path = tmp_path / "accounts.json"
        save_settings(AppSettings(auto_switch={"claude": True, "codex": False}), path)
        assert is_auto_switch_enabled("claude", path) is True
        assert is_auto_switch_enabled("codex", path) is False

    def test_toggle_auto_switch(self, tmp_path):
        path = tmp_path / "accounts.json"
        set_auto_switch_enabled("codex", True, path)
        assert is_auto_switch_enabled("codex", path) is True

    def test_save_accounts_preserves_settings(self, tmp_path):
        path = tmp_path / "accounts.json"
        set_auto_switch_enabled("claude", True, path)
        save_accounts([AccountInfo("a@test.com", "pro", "", True, "u")], path)
        assert is_auto_switch_enabled("claude", path) is True


from code_agent_switcher.config import plan_rank, sort_accounts


def _acct(email, plan, provider="claude"):
    return AccountInfo(email, plan, "", False, email, provider=provider)


class TestAccountOrder:
    """The list is rebuilt after every switch; config order moves rows under
    the cursor, so the order has to come from the accounts themselves."""

    def test_work_plans_come_before_personal(self):
        rows = sort_accounts([_acct("b@x.com", "max"), _acct("a@x.com", "team")])
        assert [a.email for a in rows] == ["a@x.com", "b@x.com"]

    def test_enterprise_outranks_team(self):
        rows = sort_accounts([_acct("b@x.com", "team"), _acct("a@x.com", "enterprise")])
        assert [a.email for a in rows] == ["a@x.com", "b@x.com"]

    def test_one_email_keeps_its_accounts_together(self):
        """A personal and a team seat on the same address must not be split
        across the two tiers."""
        rows = sort_accounts([
            _acct("same@x.com", "max"),
            _acct("other@x.com", "team"),
            _acct("same@x.com", "team"),
        ])
        assert [(a.email, a.subscription_type) for a in rows] == [
            ("other@x.com", "team"),
            ("same@x.com", "team"),
            ("same@x.com", "max"),
        ]

    def test_grouping_survives_an_alphabet_that_would_split_it(self):
        """Ranking each row on its own plan puts alpha/max between zed's two
        seats. The group has to be ranked by the email's best plan."""
        rows = sort_accounts([
            _acct("zed@x.com", "team"),
            _acct("alpha@x.com", "max"),
            _acct("zed@x.com", "max"),
        ])
        assert [(a.email, a.subscription_type) for a in rows] == [
            ("zed@x.com", "team"),
            ("zed@x.com", "max"),
            ("alpha@x.com", "max"),
        ]

    def test_within_an_email_the_better_plan_comes_first(self):
        rows = sort_accounts([_acct("a@x.com", "pro"), _acct("a@x.com", "team")])
        assert [a.subscription_type for a in rows] == ["team", "pro"]

    def test_an_unknown_plan_sorts_last(self):
        rows = sort_accounts([_acct("b@x.com", "mystery"), _acct("a@x.com", "free")])
        assert [a.email for a in rows] == ["a@x.com", "b@x.com"]

    def test_the_order_does_not_depend_on_the_input_order(self):
        rows = [_acct("b@x.com", "max"), _acct("a@x.com", "team"), _acct("c@x.com", "pro")]
        assert [a.email for a in sort_accounts(rows)] == \
               [a.email for a in sort_accounts(list(reversed(rows)))]

    def test_plan_rank_is_case_and_space_insensitive(self):
        assert plan_rank("  Team ") == plan_rank("team")
