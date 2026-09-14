import pytest


@pytest.fixture(autouse=True)
def no_network_refresh(request, monkeypatch):
    """Keep the suite off the network.

    switch_account() refreshes the target's token against Anthropic to prove the
    saved session is still real. Stub it to the transient-failure return so tests
    exercise the stored-credentials path; tests that care opt out with
    @pytest.mark.real_refresh and patch it themselves.
    """
    if request.node.get_closest_marker("real_refresh"):
        return
    import claude_switcher.core as core
    monkeypatch.setattr(core, "refresh_claude_credentials", lambda creds: None)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_refresh: test drives refresh_claude_credentials itself"
    )
