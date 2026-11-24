from plugins.dashboard_auth import nous as nous_plugin


class _Ctx:
    def __init__(self):
        self.providers = []

    def register_dashboard_auth_provider(self, provider):
        self.providers.append(provider)


def test_multiplex_profile_skips_root_owned_nous_registration(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/Users/jkw/.hermes/profiles/nova2")
    monkeypatch.setenv("HERMES_DASHBOARD_OAUTH_CLIENT_ID", "agent:nova2")
    ctx = _Ctx()
    nous_plugin.register(ctx)
    assert ctx.providers == []
    assert "root-only" in nous_plugin.LAST_SKIP_REASON


def test_root_home_retains_single_nous_registration(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/Users/jkw/.hermes")
    monkeypatch.setenv("HERMES_DASHBOARD_OAUTH_CLIENT_ID", "agent:root-test")
    ctx = _Ctx()
    nous_plugin.register(ctx)
    assert len(ctx.providers) == 1
    assert ctx.providers[0].name == "nous"
    assert nous_plugin.LAST_SKIP_REASON == ""
