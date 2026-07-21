"""Shared fixtures: JWT builder, mock session, pre-wired APIClient."""

import base64
import json
import time

import pytest

from pcxa._api import APIClient


def make_jwt(exp=None, payload=None):
    """Build an unsigned JWT with the given ``exp`` (epoch seconds)."""
    claims = dict(payload or {})
    if exp is not None:
        claims["exp"] = exp
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


class FakeResponse:
    """Mimics the subset of ``pcxa._http.Response`` that ``APIClient`` uses."""

    def __init__(self, status_code=200, body=None, reason="OK"):
        self.status_code = status_code
        self.reason = reason
        self.url = "http://test/"
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            from pcxa._http import HTTPError
            raise HTTPError(self)


class RecordingSession:
    """Stand-in for ``requests.Session``: records calls, returns canned responses.

    ``responses`` is consumed FIFO; once exhausted, returns ``default``. Each
    entry may be a ``FakeResponse`` or a zero-arg callable returning one (useful
    for "fail then succeed" sequences).
    """

    def __init__(self, responses=None, default=None):
        self.headers = {}
        self.calls = []
        self.responses = list(responses or [])
        self.default = default if default is not None else FakeResponse(200, {})

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.responses:
            r = self.responses.pop(0)
            return r() if callable(r) else r
        return self.default


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch, tmp_path):
    """Keep the developer's real PCXA_* credentials out of every test.

    Without this, a shell (or a ``.env`` anywhere above the repo) that exports
    PCXA_EMAIL/PCXA_PASSWORD would silently enable the auto-login path, making
    auth tests pass or fail depending on whose machine ran them. Pointing
    PCXA_ENV_FILE at a nonexistent path also short-circuits the CWD walk-up.
    """
    for key in ("PCXA_EMAIL", "PCXA_USERNAME", "PCXA_PASSWORD", "PCXA_AUTO_LOGIN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PCXA_ENV_FILE", str(tmp_path / "absent.env"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    """An APIClient wired to a RecordingSession with a 1-hour-fresh access token."""
    creds_path = tmp_path / "credentials.json"

    def fake_load_config():
        if creds_path.exists():
            return json.loads(creds_path.read_text())
        return {"profiles": {}}

    def fake_save_config(cfg):
        creds_path.write_text(json.dumps(cfg))

    monkeypatch.setattr("pcxa._api.load_config", fake_load_config)
    monkeypatch.setattr("pcxa._api.save_config", fake_save_config)

    profile = {
        "url": "https://api.example.com",
        "company": 1,
        "project": 2,
        "access_token": make_jwt(exp=time.time() + 3600),
        "refresh_token": "refresh-1",
        "auth": "jwt",
    }
    config = {"profiles": {"test": profile}}
    c = APIClient(profile, "test", config)
    c.session = RecordingSession()
    c._creds_path = creds_path  # for tests that simulate sibling-process rotation
    return c
