"""A 403 TERMS_ACCEPTANCE_REQUIRED (api/accounts/legal.py) prints where to accept.

Before this, the gate surfaced as a bare `403 Forbidden` plus a JSON dump, and
the obvious misreading is "I lost access to this project". Accepting is a legal
act, so the CLI never accepts on the user's behalf — it only points.
"""

import sys

import pytest

from pcxa import _main
from pcxa._http import HTTPError
from tests.conftest import FakeResponse


class _Client:
    def __init__(self, *a, **k):
        self.profile = {"frontend_url": "https://preprod.pcxa.app/"}
        self.company_id = 1
        self.project_id = 2


def _run(monkeypatch, body):
    resp = FakeResponse(403, body, reason="Forbidden")
    monkeypatch.setattr(_main, "APIClient", _Client)
    monkeypatch.setattr(_main, "load_config", lambda: {})
    monkeypatch.setattr(_main, "get_profile", lambda c, n: (n, {}))
    monkeypatch.setattr(_main, "_check_for_update", lambda: None)

    def gate(client, need_project=True):
        raise HTTPError(resp)

    monkeypatch.setattr(_main, "resolve_ids", gate)
    monkeypatch.setattr(sys, "argv", ["pcxa", "activities", "list"])
    with pytest.raises(SystemExit) as exc:
        _main.main()
    return exc.value.code


def test_terms_gate_prints_the_accept_url(monkeypatch, capsys):
    code = _run(monkeypatch, {
        "error": "You must accept the Terms of Service and Privacy Policy to continue.",
        "code": "TERMS_ACCEPTANCE_REQUIRED", "current_version": "2026-10",
        "accept_url": "/auth/accept-terms",
    })
    assert code == 1
    err = capsys.readouterr().err
    assert "https://preprod.pcxa.app/auth/accept-terms" in err
    assert "2026-10" in err


def test_other_403s_still_dump_the_body(monkeypatch, capsys):
    _run(monkeypatch, {"detail": "You do not have permission to perform this action."})
    err = capsys.readouterr().err
    assert "accept-terms" not in err
    assert "do not have permission" in err
