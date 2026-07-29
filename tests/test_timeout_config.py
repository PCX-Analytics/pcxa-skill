"""The configurable HTTP read timeout: `pcxa --timeout` / PCXA_HTTP_TIMEOUT.

``_api._request`` used to hard-code ``timeout=30`` with no way to raise it,
which aborted slow-but-succeeding calls on large projects: folder creation in
``files sync`` (PCX-Analytics/pcxa#1689) and ``files/bulk_delete/`` chunks in
``files purge`` (PCX-Analytics/pcxa#1454). The fix puts one default in the
transport and lets the CLI flag move it, so the knob covers every call path
rather than the handful that grew their own argument.
"""

import importlib

import pytest

import pcxa._http as http_mod
from pcxa._parser import build_parser
from tests.conftest import FakeResponse


@pytest.fixture(autouse=True)
def restore_default_timeout():
    original = http_mod.get_default_timeout()
    yield
    http_mod.set_default_timeout(original)


# ────────────────────────────────────────────────────────────────────────────
# _http: the single source of the default
# ────────────────────────────────────────────────────────────────────────────

def test_default_timeout_is_the_documented_fallback():
    assert http_mod.get_default_timeout() == http_mod.FALLBACK_TIMEOUT == 30.0


def test_set_default_timeout_applies_and_returns_the_value():
    assert http_mod.set_default_timeout(600) == 600.0
    assert http_mod.get_default_timeout() == 600.0


@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_set_default_timeout_rejects_unusable_values(bad):
    """A bad value must not turn every request into an unbounded block."""
    http_mod.set_default_timeout(90)
    assert http_mod.set_default_timeout(bad) == 90.0
    assert http_mod.get_default_timeout() == 90.0


def test_env_var_seeds_the_default_at_import(monkeypatch):
    monkeypatch.setenv(http_mod.TIMEOUT_ENV_VAR, "450")
    try:
        reloaded = importlib.reload(http_mod)
        assert reloaded.get_default_timeout() == 450.0
    finally:
        monkeypatch.delenv(http_mod.TIMEOUT_ENV_VAR, raising=False)
        importlib.reload(http_mod)


def _capture_transport_timeout(monkeypatch):
    """Record the timeout ``_request_stdlib`` hands to the connection layer."""
    seen = {}

    def fake_acquire(scheme, host, port, proxy_url, timeout):
        seen["timeout"] = timeout
        return object(), True

    def fake_do_request(conn, was_reused, method, target, body, headers,
                        stream, url, pool_key_args):
        return http_mod.Response(200, "OK", {}, b"{}", url)

    monkeypatch.setattr(http_mod, "_acquire_conn", fake_acquire)
    monkeypatch.setattr(http_mod, "_do_request", fake_do_request)
    return seen


def test_request_without_timeout_inherits_the_configured_default(monkeypatch):
    seen = _capture_transport_timeout(monkeypatch)
    http_mod.set_default_timeout(240)

    http_mod._request_stdlib("GET", "https://api.example.com/files/")

    assert seen["timeout"] == 240.0


def test_explicit_timeout_still_wins_over_the_default(monkeypatch):
    seen = _capture_transport_timeout(monkeypatch)
    http_mod.set_default_timeout(240)

    http_mod._request_stdlib("GET", "https://api.example.com/files/", timeout=5)

    assert seen["timeout"] == 5


# ────────────────────────────────────────────────────────────────────────────
# APIClient: the flag reaches the transport
# ────────────────────────────────────────────────────────────────────────────

def test_client_timeout_reaches_every_verb(client):
    client.timeout = 300
    client.session.default = FakeResponse(200, {})

    client.get("files/")
    client.post("folders/", {"name": "x"})
    client.patch("files/1/", {"title": "x"})
    client.delete("files/1/")

    assert [c["timeout"] for c in client.session.calls] == [300, 300, 300, 300]


def test_client_without_a_timeout_defers_to_the_http_default(client):
    """No ``--timeout``: the client passes None so ``_http`` fills it in.

    Re-hard-coding 30 here is exactly what made the ceiling unmovable.
    """
    assert client.timeout is None
    client.session.default = FakeResponse(200, {})

    client.get("files/")

    assert client.session.calls[0]["timeout"] is None


def test_per_call_timeout_overrides_the_client_default(client):
    client.timeout = 30
    client.session.default = FakeResponse(200, {})

    client.get("files/", timeout=180)

    assert client.session.calls[0]["timeout"] == 180


def test_bulk_call_inherits_the_client_timeout(client):
    """#1454: bulk chunks had no way to ask for more than 30s."""
    client.timeout = 300
    client.session.default = FakeResponse(200, {"success_count": 1})

    client.bulk_call("files/bulk_delete/", "file_ids", [1, 2, 3],
                     chunk=2, method="DELETE")

    assert [c["timeout"] for c in client.session.calls] == [300, 300]


def test_bulk_call_accepts_an_explicit_timeout(client):
    client.timeout = 30
    client.session.default = FakeResponse(200, {"success_count": 1})

    client.bulk_call("files/bulk_delete/", "file_ids", [1, 2],
                     chunk=2, method="DELETE", timeout=900)

    assert client.session.calls[0]["timeout"] == 900


# ────────────────────────────────────────────────────────────────────────────
# CLI wiring
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["--timeout", "300", "files", "sync", "/tmp/x"],
    ["files", "sync", "--timeout", "300", "/tmp/x"],
    ["--timeout", "300", "files", "purge", "1", "2"],
    ["files", "purge", "--timeout", "300", "1", "2"],
])
def test_timeout_flag_works_before_or_after_the_subcommand(argv):
    """argparse copies subparser defaults over the parent namespace, so the
    subcommand alias has to use SUPPRESS or the global form gets clobbered."""
    args = build_parser().parse_args(argv)
    assert args.http_timeout == 300.0


def test_timeout_defaults_to_none_so_the_transport_decides():
    args = build_parser().parse_args(["files", "sync", "/tmp/x"])
    assert args.http_timeout is None


def test_login_timeout_is_a_separate_knob():
    """`login --timeout` is a browser wait, not an HTTP read timeout."""
    args = build_parser().parse_args(["login", "--timeout", "300"])
    assert args.timeout == 300
    assert args.http_timeout is None


def _run_main(monkeypatch, argv):
    """Drive ``_main.main`` with everything below the wiring stubbed out."""
    import pcxa._main as main_mod

    built = {}

    class StubClient:
        def __init__(self, profile, profile_name, config, timeout=None):
            built["timeout"] = timeout

    monkeypatch.setattr("sys.argv", ["pcxa"] + argv)
    monkeypatch.setattr(main_mod, "load_config", lambda: {"default_profile": "p"})
    monkeypatch.setattr(main_mod, "get_profile", lambda cfg, name: (name, {}))
    monkeypatch.setattr(main_mod, "APIClient", StubClient)
    monkeypatch.setattr(main_mod, "resolve_ids", lambda c: None)
    monkeypatch.setattr(main_mod, "_check_for_update", lambda: None)
    monkeypatch.setitem(main_mod.HANDLERS, "tree", lambda c, a: None)

    main_mod.main()
    return built


def test_main_pushes_the_flag_into_the_client_and_the_transport(monkeypatch):
    built = _run_main(monkeypatch, ["--timeout", "450", "tree"])

    assert built["timeout"] == 450.0
    # Also set process-wide: helpers that reach for `_http.requests` directly
    # (presign PUTs, downloads) never see the APIClient.
    assert http_mod.get_default_timeout() == 450.0


def test_main_leaves_the_default_alone_when_the_flag_is_absent(monkeypatch):
    built = _run_main(monkeypatch, ["tree"])

    assert built["timeout"] is None
    assert http_mod.get_default_timeout() == http_mod.FALLBACK_TIMEOUT
