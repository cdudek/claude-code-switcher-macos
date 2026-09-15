"""A claude.ai connector is authorised server-side per account, so a switch takes
every one of them away and the app has nothing to copy. These cover the part it
can do: read the list per account, and say what a switch would cost."""

import pytest

from code_agent_switcher import connectors
from code_agent_switcher.connectors import Connector, connected, lost_by_switching


def _payload(*rows):
    return {"data": [{"display_name": n, "eligibility_reason": r} for n, r in rows]}


def _stub(monkeypatch, payload, reason=None):
    monkeypatch.setattr(connectors, "authorized_fetch",
                        lambda service, request: (payload, reason))


class TestConnectedReading:
    def test_a_connector_that_was_never_authorised_is_not_connected(self, monkeypatch):
        _stub(monkeypatch, _payload(("Linear", "never_connected_no_auto_connect")))
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ()

    def test_an_authorised_connector_is_connected(self, monkeypatch):
        _stub(monkeypatch, _payload(("Linear", "connected")))
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ("Linear",)

    def test_a_failing_connector_still_counts_as_connected(self, monkeypatch):
        """Authorisation is in place and the last call failed. Counting it as
        missing would blame the switch for something already broken."""
        _stub(monkeypatch, _payload(("Asana", "connected_with_error")))
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ("Asana",)
        assert rows[0].erroring is True

    def test_disconnected_is_not_connected(self, monkeypatch):
        """Seen live after a failed auth knocked a working connector off. The
        word contains "connected", so a substring test would count it as usable
        and the card would promise a connector that is gone."""
        _stub(monkeypatch, _payload(("Linear", "disconnected")))
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ()

    def test_order_follows_the_api(self, monkeypatch):
        _stub(monkeypatch, _payload(("Slack", "connected"), ("Linear", "connected")))
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ("Slack", "Linear")


class TestMalformedPayloads:
    @pytest.mark.parametrize("data", [None, "nope", 5, {"Linear": "connected"}])
    def test_a_rows_field_that_is_not_a_list_reads_as_empty_not_a_crash(
        self, monkeypatch, data
    ):
        """An int is the one that bites: iterating it raises rather than
        yielding nothing, so the type check has to happen before the loop."""
        _stub(monkeypatch, {"data": data})
        rows, _ = connectors.fetch_connectors("svc")
        assert rows == ()

    def test_a_payload_with_no_data_field_reads_as_empty(self, monkeypatch):
        _stub(monkeypatch, {})
        rows, _ = connectors.fetch_connectors("svc")
        assert rows == ()

    def test_a_row_with_no_name_is_skipped(self, monkeypatch):
        _stub(monkeypatch, {"data": [{"eligibility_reason": "connected"},
                                     {"display_name": "Linear",
                                      "eligibility_reason": "connected"}]})
        rows, _ = connectors.fetch_connectors("svc")
        assert connected(rows) == ("Linear",)

    def test_a_row_with_no_reason_is_kept_but_not_connected(self, monkeypatch):
        _stub(monkeypatch, {"data": [{"display_name": "Linear"}]})
        rows, _ = connectors.fetch_connectors("svc")
        assert [r.name for r in rows] == ["Linear"]
        assert connected(rows) == ()


class TestFailureIsUnknownNotEmpty:
    def test_a_failed_read_returns_none_and_the_reason(self, monkeypatch):
        _stub(monkeypatch, None, "rate limited, try later")
        rows, why = connectors.fetch_connectors("svc")
        assert rows is None
        assert why == "rate limited, try later"


class TestWhatASwitchCosts:
    HAVE = (Connector("Linear", "connected"), Connector("Slack", "connected"))

    def test_it_names_what_the_target_has_not_authorised(self):
        target = (Connector("Linear", "connected"),
                  Connector("Slack", "never_connected_no_auto_connect"))
        assert lost_by_switching(self.HAVE, target) == ("Slack",)

    def test_nothing_is_lost_when_the_target_has_them_all(self):
        target = (Connector("Linear", "connected"), Connector("Slack", "connected"))
        assert lost_by_switching(self.HAVE, target) == ()

    def test_a_connector_the_target_does_not_even_offer_counts_as_lost(self):
        assert lost_by_switching(self.HAVE, ()) == ("Linear", "Slack")

    def test_an_unreadable_target_warns_about_nothing(self):
        """The case that matters: we know two connectors are live, and the read
        for the other account failed. Naming them would be a warning built on
        nothing, and a warning nobody can trust is worse than no warning."""
        assert lost_by_switching(self.HAVE, None) == ()

    def test_an_unreadable_current_account_warns_about_nothing(self):
        assert lost_by_switching(None, self.HAVE) == ()
