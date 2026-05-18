"""Tier 1: APIClient auth/refresh and bulk_call.

These are the highest-value tests in the suite. Issues #550 (concurrent refresh
race) and #562 (bulk paths bypassing auto-refresh) both lived in _api.py.
"""

import json
import threading
import time

import pytest

from tests.conftest import FakeResponse, RecordingSession, make_jwt


# ────────────────────────────────────────────────────────────────────────────
# _decode_jwt_exp
# ────────────────────────────────────────────────────────────────────────────

def test_decode_jwt_exp_extracts_claim():
    from pcxa._api import _decode_jwt_exp
    token = make_jwt(exp=1234567890)
    assert _decode_jwt_exp(token) == 1234567890.0


def test_decode_jwt_exp_tolerates_garbage():
    from pcxa._api import _decode_jwt_exp
    assert _decode_jwt_exp(None) is None
    assert _decode_jwt_exp("") is None
    assert _decode_jwt_exp("not-a-jwt") is None
    assert _decode_jwt_exp("a.b.c") is None  # b64 garbage


# ────────────────────────────────────────────────────────────────────────────
# Proactive + reactive refresh
# ────────────────────────────────────────────────────────────────────────────

def test_proactive_refresh_fires_near_expiry(client, monkeypatch):
    # Token expires in 60s, leeway is 300s → must refresh proactively.
    client.profile["access_token"] = make_jwt(exp=time.time() + 60)
    refresh_calls = []
    monkeypatch.setattr(client, "_refresh_token",
                        lambda: refresh_calls.append(1) or True)

    client.session.default = FakeResponse(200, {"ok": True})
    client._request("GET", "https://api.example.com/anything")

    assert refresh_calls, "expected proactive refresh near expiry"


def test_proactive_refresh_skips_when_fresh(client, monkeypatch):
    # Token good for 1h, leeway 5min → no refresh.
    refresh_calls = []
    monkeypatch.setattr(client, "_refresh_token",
                        lambda: refresh_calls.append(1) or True)

    client.session.default = FakeResponse(200, {"ok": True})
    client._request("GET", "https://api.example.com/anything")

    assert not refresh_calls


def test_401_triggers_reactive_refresh_and_retry(client, monkeypatch):
    """First response is 401, refresh succeeds, retried request returns 200."""
    refresh_calls = []
    monkeypatch.setattr(client, "_refresh_token",
                        lambda: refresh_calls.append(1) or True)

    client.session.responses = [
        FakeResponse(401, {"code": "token_not_valid"}),
        FakeResponse(200, {"ok": True}),
    ]
    resp = client._request("GET", "https://api.example.com/files/")

    assert resp.status_code == 200
    assert len(refresh_calls) == 1
    assert len(client.session.calls) == 2  # original + retry


def test_401_without_refresh_token_raises(client, monkeypatch):
    """No refresh_token → 401 surfaces as HTTPError, no infinite loop."""
    client.profile["refresh_token"] = None
    client.session.responses = [FakeResponse(401, {"code": "token_not_valid"})]

    from pcxa._http import HTTPError
    with pytest.raises(HTTPError):
        client._request("GET", "https://api.example.com/files/")


# ────────────────────────────────────────────────────────────────────────────
# Single-flight refresh (issue #550)
# ────────────────────────────────────────────────────────────────────────────

def test_concurrent_refreshes_collapse_to_one_network_call(client, monkeypatch):
    """N workers hitting a near-expired token must trigger exactly one
    refresh round-trip, not N. This is the issue #550 race."""
    network_refresh_calls = []

    def fake_post(url, **kwargs):
        network_refresh_calls.append(url)
        time.sleep(0.05)  # widen the race window
        return FakeResponse(200, {"access": make_jwt(exp=time.time() + 3600)})

    monkeypatch.setattr("pcxa._api.requests.post", fake_post)
    # Force every worker to want a refresh.
    client.profile["access_token"] = make_jwt(exp=time.time() + 10)
    client.session.default = FakeResponse(200, {"ok": True})

    errors = []

    def worker():
        try:
            client._request("GET", "https://api.example.com/anything")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(network_refresh_calls) == 1, \
        f"expected single-flight, got {len(network_refresh_calls)} refresh calls"


def test_blacklisted_refresh_reloads_creds_from_disk(client, monkeypatch):
    """If the refresh endpoint says our token is blacklisted, we re-read
    credentials.json (a sibling process probably won the rotation race)."""
    fresh_token = make_jwt(exp=time.time() + 3600)
    fresh_config = {
        "profiles": {
            "test": {
                **client.profile,
                "access_token": fresh_token,
                "refresh_token": "refresh-2-from-disk",
            }
        }
    }
    client._creds_path.write_text(json.dumps(fresh_config))

    def fake_post(url, **kwargs):
        return FakeResponse(400, {"code": "token_not_valid"})

    monkeypatch.setattr("pcxa._api.requests.post", fake_post)
    ok = client._refresh_token()

    assert ok is True
    assert client.profile["access_token"] == fresh_token
    assert client.session.headers["Authorization"] == f"Bearer {fresh_token}"


# ────────────────────────────────────────────────────────────────────────────
# bulk_call (issue #562)
# ────────────────────────────────────────────────────────────────────────────

def test_bulk_call_chunks_ids(client):
    client.session.default = FakeResponse(200, {"success_count": 1, "error_count": 0})
    ids = list(range(1, 1251))  # 1250 items → 3 chunks of 500/500/250

    result = client.bulk_call("files/bulk_delete/", "file_ids", ids,
                              chunk=500, method="DELETE")

    assert result["chunks"] == 3
    sizes = [len(c["json"]["file_ids"]) for c in client.session.calls]
    assert sizes == [500, 500, 250]
    # Every chunk hits the project-scoped URL.
    assert all(c["url"].endswith("/projects/2/files/bulk_delete/") for c in client.session.calls)
    assert all(c["method"] == "DELETE" for c in client.session.calls)


def test_bulk_call_aggregates_counts_and_errors(client):
    client.session.responses = [
        FakeResponse(200, {"success_count": 10, "error_count": 1, "errors": [{"id": 5, "msg": "nope"}]}),
        FakeResponse(200, {"success_count": 8, "error_count": 2, "errors": [{"id": 22}, {"id": 23}]}),
    ]

    result = client.bulk_call("files/bulk_delete/", "file_ids", list(range(20)),
                              chunk=10, method="DELETE")

    assert result["success_count"] == 18
    assert result["error_count"] == 3
    assert len(result["errors"]) == 3
    assert result["chunks"] == 2


def test_bulk_call_merges_base_payload(client):
    client.session.default = FakeResponse(200, {"success_count": 3})

    client.bulk_call("files/bulk_update/", "file_ids", [1, 2, 3],
                     base_payload={"tags": ["x"], "tag_mode": "add"})

    payload = client.session.calls[0]["json"]
    assert payload == {"file_ids": [1, 2, 3], "tags": ["x"], "tag_mode": "add"}


def test_bulk_call_handles_204_no_body(client):
    client.session.default = FakeResponse(204, body=None)
    # Override raise_for_status no-op on FakeResponse for 204
    result = client.bulk_call("files/bulk_delete/", "file_ids", [1, 2], chunk=10)
    assert result["chunks"] == 1
    assert result["success_count"] == 0  # 204 → empty body, can't aggregate


def test_bulk_call_routes_through_request_so_401_retries(client, monkeypatch):
    """bulk_call must inherit the auto-refresh semantics of _request.
    This is the headline guarantee for issue #562."""
    refresh_calls = []
    monkeypatch.setattr(client, "_refresh_token",
                        lambda: refresh_calls.append(1) or True)
    client.session.responses = [
        FakeResponse(401, {"code": "token_not_valid"}),  # chunk 1 first attempt
        FakeResponse(200, {"success_count": 5}),          # chunk 1 retry
        FakeResponse(200, {"success_count": 5}),          # chunk 2
    ]

    result = client.bulk_call("files/bulk_delete/", "file_ids", list(range(10)),
                              chunk=5, method="DELETE")

    assert result["success_count"] == 10
    assert len(refresh_calls) == 1


def test_bulk_call_on_chunk_callback_fires_per_chunk(client):
    client.session.default = FakeResponse(200, {"success_count": 5})
    seen = []

    def cb(start, size, total, data):
        seen.append((start, size, total))

    client.bulk_call("files/bulk_delete/", "file_ids", list(range(12)),
                     chunk=5, on_chunk=cb)

    assert seen == [(0, 5, 12), (5, 5, 12), (10, 2, 12)]


def test_bulk_call_empty_ids_makes_no_request(client):
    result = client.bulk_call("files/bulk_delete/", "file_ids", [], chunk=500)
    assert result == {"success_count": 0, "error_count": 0, "errors": [], "chunks": 0}
    assert client.session.calls == []
