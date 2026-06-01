"""Regression test for #719.

`pcxa files upload` of any single file over the multipart threshold crashed
with `NameError: _upload_via_multipart_presign is not defined` — the
single-file branch called a wrapper that was never implemented. These tests
guard that the wrapper exists, drives the multipart primitive, and registers
the uploaded object through POST /files/ (same contract as the smaller-file
presign path) so the call returns the full File row.
"""

from types import SimpleNamespace

import pcxa.commands.files as files


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Records _request calls; _url echoes the path onto a base."""

    def __init__(self, row):
        self.requests = []
        self._row = row

    def _url(self, path):
        return f"https://api.example.com/{path}"

    def _request(self, method, url, json=None, **kwargs):
        self.requests.append({"method": method, "url": url, "json": json})
        return FakeResponse(self._row)


def test_wrapper_exists_and_is_callable():
    # The bug was a NameError — the symbol simply wasn't defined.
    assert callable(getattr(files, "_upload_via_multipart_presign", None))


def test_uploads_then_registers_via_files_endpoint(monkeypatch):
    captured = {}

    def fake_primitive(client, fp, *, folder, part_size, concurrency):
        captured["call"] = {"folder": folder, "part_size": part_size,
                            "concurrency": concurrency}
        return {
            "storage_key": "uploads/abc123",
            "original_filename": "big.mp4",
            "content_type": "video/mp4",
            "file_size": 1_870_211_585,
        }

    monkeypatch.setattr(files, "_multipart_presign_and_put", fake_primitive)
    client = FakeClient({"id": 9001, "title": "big"})

    result = files._upload_via_multipart_presign(
        client, SimpleNamespace(), "big", 465710, ["Tier 1", "Sealed"],
        part_size=16 * 1024 * 1024, concurrency=8,
    )

    # The primitive received the multipart knobs.
    assert captured["call"] == {"folder": 465710,
                                "part_size": 16 * 1024 * 1024, "concurrency": 8}

    # Exactly one register POST, to the create endpoint, with the storage_key
    # and metadata the smaller-file presign path also sends.
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://api.example.com/files/"
    assert req["json"] == {
        "title": "big",
        "original_filename": "big.mp4",
        "content_type": "video/mp4",
        "file_size_input": 1_870_211_585,
        "storage_key": "uploads/abc123",
        "folder": 465710,
        "tags": ["Tier 1", "Sealed"],
    }

    # Returns the full row the single-file path prints (id/title).
    assert result == {"id": 9001, "title": "big"}


def test_omits_optional_fields_when_absent(monkeypatch):
    monkeypatch.setattr(files, "_multipart_presign_and_put", lambda *a, **k: {
        "storage_key": "uploads/x", "original_filename": "f.bin",
        "content_type": "application/octet-stream", "file_size": 100,
    })
    client = FakeClient({"id": 1, "title": "f"})

    files._upload_via_multipart_presign(
        client, SimpleNamespace(), "f", None, [],
        part_size=5 * 1024 * 1024, concurrency=4,
    )

    payload = client.requests[0]["json"]
    assert "folder" not in payload
    assert "tags" not in payload
