"""Issue #1: a team seat and a personal plan on one address.

Claude Code lets one email belong to several organisations, and those are two
sessions with two token pairs. The app keyed everything on the address, so
adding the second one replaced the first: its config row, and worse, its
Keychain item.

The rule these tests pin down: the FIRST account saved on an address keeps the
bare `claude-switcher:<email>` item, so nothing on an existing machine moves. A
later account on the same address in a different organisation gets its own.
"""

import pytest

from code_agent_switcher.config import (
    AccountInfo,
    add_account,
    find_account,
    load_accounts,
    remove_account,
    set_active_account,
    sort_accounts,
)
from code_agent_switcher.core import snapshot_service


def _acct(email, org_uuid, plan="team", org_name="", provider="claude"):
    return AccountInfo(
        email=email, subscription_type=plan, org_name=org_name, active=False,
        keychain_account=email, provider=provider,
        oauth_account={"organizationUuid": org_uuid} if org_uuid else None,
    )


@pytest.fixture
def config(tmp_path):
    return tmp_path / "accounts.json"


class TestBothSurvive:
    def test_the_second_organisation_does_not_replace_the_first(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM", plan="team"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL", plan="max"), config)
        rows = load_accounts(config)
        assert len(rows) == 2
        assert {r.subscription_type for r in rows} == {"team", "max"}

    def test_the_same_account_again_updates_rather_than_duplicates(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM", plan="team"), config)
        add_account(_acct("a@x.com", "ORG-TEAM", plan="enterprise"), config)
        rows = load_accounts(config)
        assert len(rows) == 1
        assert rows[0].subscription_type == "enterprise"

    def test_a_different_address_is_untouched(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        add_account(_acct("b@x.com", "ORG-TEAM"), config)
        assert len(load_accounts(config)) == 2


class TestKeychainNames:
    def test_the_first_account_keeps_the_historic_item(self, config):
        """Every install that exists today must keep working untouched."""
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        first = load_accounts(config)[0]
        assert first.slot == ""
        assert snapshot_service(first.ref) == "claude-switcher:a@x.com"

    def test_the_second_account_gets_its_own_item(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL"), config)
        services = {snapshot_service(r.ref) for r in load_accounts(config)}
        assert services == {
            "claude-switcher:a@x.com",
            "claude-switcher:a@x.com#ORG-PERSONAL",
        }

    def test_an_account_with_no_organisation_recorded_still_gets_a_slot(self, config):
        """Otherwise the second row would collide with the first again."""
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        add_account(_acct("a@x.com", None), config)
        refs = {r.ref for r in load_accounts(config)}
        assert refs == {"a@x.com", "a@x.com#personal"}


class TestSelectionIsPerAccount:
    def test_activating_one_does_not_activate_the_other(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL"), config)
        rows = load_accounts(config)
        personal = next(r for r in rows if r.slot)
        set_active_account(personal.ref, config)
        states = {r.ref: r.active for r in load_accounts(config)}
        assert states == {"a@x.com": False, personal.ref: True}

    def test_removing_one_leaves_the_other(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL"), config)
        personal = next(r for r in load_accounts(config) if r.slot)
        remove_account(personal.ref, config)
        rows = load_accounts(config)
        assert [r.ref for r in rows] == ["a@x.com"]

    def test_find_account_resolves_by_ref_not_address(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM", plan="team"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL", plan="max"), config)
        rows = load_accounts(config)
        found = find_account(rows, "claude", "a@x.com#ORG-PERSONAL")
        assert found.subscription_type == "max"


class TestOrdering:
    def test_the_pair_stays_adjacent_and_work_comes_first(self, config):
        add_account(_acct("a@x.com", "ORG-TEAM", plan="team"), config)
        add_account(_acct("a@x.com", "ORG-PERSONAL", plan="max"), config)
        add_account(_acct("b@x.com", "ORG-OTHER", plan="max"), config)
        rows = sort_accounts(load_accounts(config))
        assert [(r.email, r.subscription_type) for r in rows] == [
            ("a@x.com", "team"),
            ("a@x.com", "max"),
            ("b@x.com", "max"),
        ]
