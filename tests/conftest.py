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
    import code_agent_switcher.core as core
    monkeypatch.setattr(core, "refresh_claude_credentials", lambda creds: None)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_refresh: test drives refresh_claude_credentials itself"
    )


@pytest.fixture(autouse=True)
def _backups_stay_in_the_sandbox(tmp_path, monkeypatch):
    """No test writes into the real backup directory.

    Without this the suite filled it with 30 copies of tmp-file junk in four
    minutes, and `accounts.json` reached its 20-copy limit - so a real backup
    would have been evicted by a test run. The directory exists to survive
    exactly that kind of accident.
    """
    from code_agent_switcher import backups
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")
