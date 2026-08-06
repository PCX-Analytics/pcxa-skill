"""Regression tests for PCX-Analytics/pcxa#1946.

`pcxa files upload` used to POST the file's bytes through the API origin for
anything <= 10 MB, which put opaque user documents in front of Cloudflare's
managed WAF. 62 of 110,864 files in one bulk load were rejected forever with
a 403 "Blocked" HTML page because some byte sequence inside a PDF/XLSX matched
an SQLi/XSS signature — renaming changed nothing, truncating the body fixed
it. Nothing >= 10 MB was affected, because that band already presigned.

These tests pin the property that actually matters: **no upload command ever
puts file bytes in a request to the API origin**, at any size. Asserting on
"which helper got called" would pass just as happily if someone reintroduced a
multipart POST under a new name, so we assert on the requests themselves.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import pcxa.commands.files as files


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class RecordingClient:
    """Records every request; `files=` is what we are policing."""

    def __init__(self, row=None):
        self.requests = []
        self._row = row or {"id": 1, "title": "t", "version_number": 1}

    def _url(self, path):
        return f"https://api.example.com/api/companies/3/projects/4/{path}"

    def _request(self, method, url, json=None, data=None, files=None, **kwargs):
        self.requests.append(
            {"method": method, "url": url, "json": json, "data": data, "files": files}
        )
        if url.endswith("presign-upload/"):
            return FakeResponse(
                {
                    "upload_url": "https://r2.example.com/put?sig=abc",
                    "storage_key": "companies/3/projects/4/files/x.pdf",
                    "upload_type": "presigned_put",
                }
            )
        return FakeResponse(self._row)


@pytest.fixture
def no_real_put(monkeypatch):
    """Stub the direct-to-storage PUT so nothing touches the network."""
    puts = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_put(url, data=None, headers=None, timeout=None):
        puts.append({"url": url, "headers": headers})
        return _Resp()

    monkeypatch.setattr(files.requests, "put", fake_put)
    return puts


def _write(tmp_path: Path, name: str, size: int) -> Path:
    fp = tmp_path / name
    fp.write_bytes(b"\x00" * size)
    return fp


def _upload_args(fp, **overrides):
    args = SimpleNamespace(
        paths=[str(fp)],
        title=None,
        folder=26141,
        tags=None,
        dry_run=False,
        format="text",
        multipart_threshold_mb=50,
        part_size_mb=16,
        concurrency=8,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# --------------------------------------------------------------------------
# The core invariant
# --------------------------------------------------------------------------

# The old cutoff was 10 MB, so the sizes that regressed are the ones under it.
# 2_745_048 is the exact size of the xlsx in the issue report.
@pytest.mark.parametrize("size", [1, 47 * 1024, 546 * 1024, 2_745_048, 8_700_000])
def test_upload_never_sends_bytes_to_the_api_origin(tmp_path, no_real_put, size):
    fp = _write(tmp_path, "probe.pdf", size)
    client = RecordingClient()

    files.cmd_files_upload(client, _upload_args(fp))

    assert client.requests, "expected the command to talk to the API"
    for req in client.requests:
        assert req["files"] is None, (
            f"{req['method']} {req['url']} carried a multipart file body — "
            "upload bytes must go to storage, never through the API origin (#1946)"
        )


@pytest.mark.parametrize("size", [1, 546 * 1024, 2_745_048])
def test_upload_version_never_sends_bytes_to_the_api_origin(tmp_path, no_real_put, size):
    fp = _write(tmp_path, "probe.xlsx", size)
    client = RecordingClient()
    args = SimpleNamespace(
        path=str(fp), file_id=77, notes="n", dry_run=False, format="text"
    )

    files.cmd_files_upload_version(client, args)

    assert client.requests
    for req in client.requests:
        assert req["files"] is None, (
            f"{req['method']} {req['url']} carried a multipart file body — "
            "new-version bytes must go to storage, never through the origin (#1946)"
        )


def test_small_upload_presigns_then_registers_with_json(tmp_path, no_real_put):
    """The replacement path is presign → PUT to storage → JSON metadata create."""
    fp = _write(tmp_path, "small.pdf", 4096)
    client = RecordingClient()

    files.cmd_files_upload(client, _upload_args(fp))

    urls = [r["url"] for r in client.requests]
    assert urls[0].endswith("files/presign-upload/")
    assert urls[-1].endswith("files/")
    assert len(no_real_put) == 1, "bytes should have been PUT straight to storage"
    assert no_real_put[0]["url"].startswith("https://r2.example.com/")

    create = client.requests[-1]
    assert create["json"]["storage_key"] == "companies/3/projects/4/files/x.pdf"
    assert create["json"]["folder"] == 26141


def test_byte_carrying_helpers_are_gone():
    """They were the only two call sites that fed bytes to the origin.

    Keeping this as a name check is deliberate: the helpers are trivial to
    resurrect by copy-paste, and a reviewer reading a diff that re-adds
    `_upload_via_multipart` should see a red test, not just a red comment.
    """
    assert not hasattr(files, "_upload_via_multipart")
    assert not hasattr(files, "_upload_version_via_multipart")


def test_dry_run_reports_presign_for_small_version_upload(tmp_path, capsys):
    fp = _write(tmp_path, "small.pdf", 4096)
    args = SimpleNamespace(
        path=str(fp), file_id=77, notes=None, dry_run=True, format="text"
    )

    files.cmd_files_upload_version(RecordingClient(), args)

    assert "presign" in capsys.readouterr().out
