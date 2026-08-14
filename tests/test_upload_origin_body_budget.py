"""The API origin may only ever receive metadata-sized request bodies.

Sibling of ``test_upload_never_transits_origin.py``, which pins the same
property (PCX-Analytics/pcxa#1946) but with two blind spots that let a
76.8 GB byte-proxying regression run in production for a full day:

1. **It polices one encoding.** Its assertion is ``req["files"] is None``,
   so it only catches bytes sent as a multipart ``files=`` body. The CLI's
   HTTP layer will just as happily put a body on the wire from ``data=``
   (bytes, a str, or an open file handle) — see ``_request_stdlib``. A
   reintroduced byte-proxy that used ``data=`` would pass it.
2. **It only covers the two single-file commands.** ``files upload`` with
   more than one path (the ``_upload_batch`` route) and ``files sync`` --
   the highest-volume upload path in the CLI, and the one that moves
   terabytes -- had no coverage at all.

So this module asserts on the thing that actually costs money: the number
of bytes the CLI puts in a request to the API origin, measured with the
CLI's own wire encoder, whatever the parameter it arrived in. Anything
above a metadata ceiling is a failure, on every upload path.

Why a byte budget rather than "which helper was called": egress from the
origin is billed per GB, and the invariant we care about is bandwidth, not
call graph. A helper-name assertion passes happily when someone reinvents
the byte-proxy under a new name.
"""

import json as _json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

import pcxa.commands.files as files
import pcxa.commands.sync as sync
from pcxa._http import _encode_multipart

# Generous: the largest legitimate origin body is a bulk-register flush of
# 100 items, each ~200 bytes of JSON metadata (~20 KB). Every probe file
# below is an order of magnitude larger than this, so the ceiling
# distinguishes "metadata" from "file content" without being brittle.
ORIGIN_BODY_CEILING = 256 * 1024

# The exact size of the xlsx in the #1946 report, and a size in the band
# that regressed (under the old 10 MB multipart-POST cutoff).
ISSUE_XLSX_SIZE = 2_745_048
SUB_CUTOFF_SIZE = 546 * 1024

STORAGE_HOST = "https://r2.example.test"
# Realistic presigned key shape: `_build_storage_key` in the API emits
# files/uploads/{company}/{project}/{Y}/{m}/{d}/{uuid}-{slug}{ext}. Django's
# own FileField upload_to emits files/uploads/{Y}/{m}/{d}/ — that second
# shape in the DB is the fingerprint of bytes having gone through Django.
PRESIGNED_KEY = "files/uploads/3/56/2026/08/13/deadbeef-probe.pdf"


def wire_body_size(*, json=None, data=None, files=None):
    """Bytes this request would put on the wire, per ``_request_stdlib``.

    Mirrors the encoder-selection branches in ``pcxa._http._request_stdlib``
    exactly, so the measurement is what the socket would see rather than an
    assumption about which keyword argument carries the payload.
    """
    if json is not None:
        return len(_json.dumps(json).encode("utf-8"))
    if files is not None:
        body, _ = _encode_multipart(data or {}, files)
        return len(body)
    if isinstance(data, dict):
        return len(urlencode(data, doseq=True).encode())
    if isinstance(data, str):
        return len(data.encode())
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if hasattr(data, "read"):  # an open file handle streamed as the body
        pos = data.tell()
        try:
            return len(data.read())
        finally:
            data.seek(pos)
    return 0


class OriginRecorder:
    """Fake APIClient that records the wire size of every origin request.

    Only the API origin goes through here. Bytes bound for storage go via
    ``requests.put``, which the ``storage_puts`` fixture intercepts
    separately — that separation is the whole point of the invariant.
    """

    timeout = 30
    default_timeout = 30

    def __init__(self, *, bulk_presign_status=200, existing_titles=()):
        self.requests = []
        self._bulk_presign_status = bulk_presign_status
        self._existing_titles = list(existing_titles)

    # -- APIClient surface used by the upload paths ------------------------

    def _url(self, path):
        return f"https://api.pcxa.test/api/companies/3/projects/56/{path}"

    def _request(self, method, url, json=None, data=None, files=None,
                 params=None, timeout=None, **kwargs):
        path = url.split("projects/56/")[-1]
        self.requests.append({
            "method": method,
            "url": url,
            "path": path,
            "json": json,
            "data": data,
            "files": files,
            "body_bytes": wire_body_size(json=json, data=data, files=files),
        })
        return self._respond(method, path, json, params)

    # ``APIClient.get``/``post`` return parsed JSON, not a Response — the
    # folder-resolution path in sync.py subscripts the result directly.
    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._request("GET", self._url(path), params=params,
                             timeout=timeout).json()

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._request("POST", self._url(path), json=json_data,
                             timeout=timeout).json()

    # -- canned responses --------------------------------------------------

    def _respond(self, method, path, json, params):
        if path.endswith("presign-upload/") and not path.endswith("bulk-presign-upload/"):
            return _Resp(200, {
                "upload_url": f"{STORAGE_HOST}/put?sig=single",
                "storage_key": PRESIGNED_KEY,
                "upload_type": "presigned_put",
            })

        if path.endswith("bulk-presign-upload/"):
            if self._bulk_presign_status != 200:
                # The documented degradation: sync falls back to per-file
                # presign when the batch endpoint isn't deployed.
                raise _http_error(self._bulk_presign_status)
            items = (json or {}).get("items") or []
            return _Resp(200, {"results": [
                {"index": i, "status": "ok",
                 "upload_url": f"{STORAGE_HOST}/put?sig=bulk{i}",
                 "storage_key": f"files/uploads/3/56/2026/08/13/bulk{i}-probe.pdf"}
                for i in range(len(items))
            ]})

        if path.endswith("bulk-register/"):
            items = (json or {}).get("items") or []
            return _Resp(200, {
                "summary": {"created": len(items), "duplicate": 0, "error": 0},
                "results": [{"index": i, "status": "created", "id": 1000 + i}
                            for i in range(len(items))],
            })

        if path.startswith("folders/"):
            return _Resp(200, {"id": 900, "name": "root", "results": [], "count": 0})

        if path.startswith("files/") and method == "GET":
            # Pre-flight existing-name scan used by `files sync`.
            return _Resp(200, {
                "results": [{"title": t, "current_version": {}} for t in self._existing_titles],
                "count": len(self._existing_titles),
                "next": None,
            })

        # POST /files/ (single create) and POST /files/{id}/upload_new_version/
        return _Resp(201, {"id": 5, "title": "probe", "version_number": 2})

    # -- assertions --------------------------------------------------------

    def assert_within_budget(self):
        oversized = [r for r in self.requests if r["body_bytes"] > ORIGIN_BODY_CEILING]
        assert not oversized, (
            "file bytes reached the API origin — "
            + "; ".join(
                f"{r['method']} {r['path']} carried {r['body_bytes']:,} bytes "
                f"(ceiling {ORIGIN_BODY_CEILING:,})"
                for r in oversized
            )
            + ". Upload bytes must be PUT straight to storage and only "
              "metadata JSON POSTed to the origin (#1946)."
        )

    def assert_no_raw_byte_bodies(self):
        """Encoding-level complement to the byte budget.

        A small file could slip under the ceiling and still be travelling
        through the origin, which is the same defect at a smaller size.
        """
        for r in self.requests:
            assert r["files"] is None, (
                f"{r['method']} {r['path']} carried a multipart file body"
            )
            assert r["data"] is None or isinstance(r["data"], dict), (
                f"{r['method']} {r['path']} carried a raw {type(r['data']).__name__} "
                f"body — upload payloads must be JSON metadata only"
            )

    def paths(self):
        return [r["path"] for r in self.requests]


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return _json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _http_error(self.status_code)


def _http_error(status):
    from pcxa._http import HTTPError
    return HTTPError(_Resp(status, {"detail": "nope"}))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_puts(monkeypatch):
    """Intercept the direct-to-storage PUT; record URL and byte count."""
    puts = []

    class _PutResp:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_put(url, data=None, headers=None, timeout=None, **kwargs):
        if hasattr(data, "read"):
            payload = data.read()
        else:
            payload = data or b""
        puts.append({"url": url, "bytes": len(payload), "headers": headers or {}})
        return _PutResp()

    monkeypatch.setattr(files.requests, "put", fake_put)
    monkeypatch.setattr(sync._requests, "put", fake_put)
    return puts


def _write(tmp_path, name, size):
    fp = tmp_path / name
    fp.write_bytes(b"\x5a" * size)
    return fp


def _version_args(fp, **overrides):
    args = SimpleNamespace(
        path=str(fp), file_id=77, notes=None, dry_run=False, format="json"
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _upload_args(paths, **overrides):
    args = SimpleNamespace(
        paths=[str(p) for p in paths],
        title=None, folder=900, tags=None, dry_run=False, format="json",
        multipart_threshold_mb=50, part_size_mb=16, concurrency=4,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _sync_args(input_dir, **overrides):
    args = SimpleNamespace(
        input_dir=str(input_dir), folder=900, manifest=None,
        include=None, exclude=None, include_hidden=False, tags=None,
        format="json", concurrency=2, max_concurrency=2, min_concurrency=1,
        no_auto_tune=True, max_failures=100, part_concurrency=2,
        multipart_threshold_mb=50, part_size_mb=16, batch_size=200,
        no_bulk_presign=False, limit=0, dry_run=False, trust_manifest=True,
        error_log=None, stats_interval=0,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# the new-version path — this is the one that regressed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [SUB_CUTOFF_SIZE, ISSUE_XLSX_SIZE, 8_700_000])
def test_upload_version_body_stays_metadata_sized(tmp_path, storage_puts, size):
    """`files upload-version` must not push version bytes through the origin.

    Before #1946 this command POSTed a multipart body to
    ``files/{id}/upload_new_version/`` for anything at or under 10 MB, so
    every version created under that size was written to R2 by Django
    itself — visible in the DB as a ``files/uploads/YYYY/MM/DD/`` key
    instead of the presigned ``files/uploads/{company}/{project}/…`` shape,
    and on the bill as origin egress.
    """
    fp = _write(tmp_path, "probe.xlsx", size)
    client = OriginRecorder()

    files.cmd_files_upload_version(client, _version_args(fp))

    assert client.requests, "expected the command to talk to the API"
    client.assert_within_budget()
    client.assert_no_raw_byte_bodies()

    assert len(storage_puts) == 1, "the bytes must be PUT straight to storage"
    assert storage_puts[0]["url"].startswith(STORAGE_HOST)
    assert storage_puts[0]["bytes"] == size


def test_upload_version_presigns_then_posts_only_storage_key(tmp_path, storage_puts):
    """The positive half: presign → PUT to storage → JSON with storage_key."""
    fp = _write(tmp_path, "probe.xlsx", ISSUE_XLSX_SIZE)
    client = OriginRecorder()

    files.cmd_files_upload_version(client, _version_args(fp, notes="rev B"))

    assert client.paths() == [
        "files/presign-upload/",
        "files/77/upload_new_version/",
    ]

    create = client.requests[-1]
    assert create["method"] == "POST"
    assert create["json"]["storage_key"] == PRESIGNED_KEY
    assert create["json"]["file_size_input"] == ISSUE_XLSX_SIZE
    assert create["json"]["original_filename"] == "probe.xlsx"
    assert create["json"]["version_notes"] == "rev B"
    # The server's FileVersionSerializer rejects a key outside this prefix.
    assert create["json"]["storage_key"].startswith("files/uploads/")


# ---------------------------------------------------------------------------
# the paths that had no coverage at all
# ---------------------------------------------------------------------------


def test_batch_upload_body_stays_metadata_sized(tmp_path, storage_puts):
    """`files upload` with >1 path takes ``_upload_batch`` — previously untested."""
    paths = [_write(tmp_path, f"batch{i}.pdf", ISSUE_XLSX_SIZE) for i in range(3)]
    client = OriginRecorder()

    files.cmd_files_upload(client, _upload_args(paths))

    client.assert_within_budget()
    client.assert_no_raw_byte_bodies()
    assert len(storage_puts) == 3
    assert sum(p["bytes"] for p in storage_puts) == 3 * ISSUE_XLSX_SIZE
    assert "files/bulk-register/" in client.paths()


def test_sync_body_stays_metadata_sized(tmp_path, storage_puts):
    """`files sync` — the terabyte path, and the one named in the incident."""
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    _write(src, "a.pdf", ISSUE_XLSX_SIZE)
    _write(src / "sub", "b.pdf", SUB_CUTOFF_SIZE)
    client = OriginRecorder()

    sync.cmd_files_sync(client, _sync_args(src))

    client.assert_within_budget()
    client.assert_no_raw_byte_bodies()
    assert len(storage_puts) == 2
    assert "files/bulk-presign-upload/" in client.paths()
    assert "files/bulk-register/" in client.paths()


def test_sync_per_file_presign_fallback_stays_metadata_sized(tmp_path, storage_puts):
    """A 404 from bulk-presign degrades to per-file presign — not to a byte POST.

    This is the fallback branch the incident review flagged as a candidate
    cause. It is clean today; the test keeps it that way.
    """
    src = tmp_path / "tree"
    src.mkdir()
    _write(src, "a.pdf", ISSUE_XLSX_SIZE)
    client = OriginRecorder(bulk_presign_status=404)

    sync.cmd_files_sync(client, _sync_args(src))

    client.assert_within_budget()
    client.assert_no_raw_byte_bodies()
    assert len(storage_puts) == 1
    assert "files/presign-upload/" in client.paths()


def test_sync_never_calls_upload_new_version(tmp_path, storage_puts):
    """Sync skips a name that already exists rather than versioning it.

    Pinned because the obvious "sync should update changed files" feature is
    exactly where a byte-POST would be reintroduced: ``upload_new_version``
    is the one endpoint whose serializer still accepts a raw ``file_upload``.
    Any future changed-file branch must presign, and this test's failure is
    the prompt to extend the budget assertions to it.
    """
    src = tmp_path / "tree"
    src.mkdir()
    _write(src, "already-there.pdf", ISSUE_XLSX_SIZE)
    client = OriginRecorder(existing_titles=["already-there.pdf"])

    sync.cmd_files_sync(client, _sync_args(src, trust_manifest=False))

    assert not any("upload_new_version" in p for p in client.paths())
    assert storage_puts == []
    client.assert_within_budget()


# ---------------------------------------------------------------------------
# structural guard
# ---------------------------------------------------------------------------


def test_no_upload_helper_posts_a_file_body_to_the_origin():
    """No module in the upload path may pass ``files=`` at a call site.

    ``_encode_multipart`` still exists in the HTTP layer for genuine form
    posts; what must not exist is an upload helper reaching for it. Parsing
    catches a reintroduction on a path no test happens to drive — and,
    unlike a substring scan, is not tripped by a comment or docstring that
    merely mentions the keyword.
    """
    import ast
    import inspect

    for module in (files, sync):
        tree = ast.parse(inspect.getsource(module))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "files"
        ]
        assert not offenders, (
            f"{module.__name__} passes a multipart file body to a request at "
            f"line(s) {offenders} — upload bytes must go straight to storage "
            "(#1946)"
        )
