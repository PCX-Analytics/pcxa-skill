"""`files sync` must report what the server actually persisted (PCX-Analytics/pcxa-skill#25).

pcxa#1450 acceptance criterion 4: *the sync summary, process exit status and
persisted rows agree*. Before #25 none of the three were guaranteed:

* the command returned nothing, so a run with failed files exited 0;
* a ``--max-failures`` abort counted uploads that never started as errors,
  and did not count the files it never reached at all, so the summary did
  not add up to ``to_upload`` and nothing said why;
* a final bulk-register 503 that had committed some rows before the server
  lost its database connection was booked as N errors, contradicting the
  database and leaving the committed rows out of the manifest.

Every test drives the real ``cmd_files_sync`` against a fake API origin and a
fake storage PUT, and asserts the SUMMARY INVARIANT the fix establishes::

    created + duplicate + error + unrecognized + not_attempted == to_upload
"""

import json as _json
import threading
import time
from types import SimpleNamespace

import pytest

import pcxa.commands.files as files
import pcxa.commands.sync as sync
from pcxa._http import HTTPError

# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.reason = "fake"
        self.url = "https://api.pcxa.test/fake"
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return _json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(self)


class FakeOrigin:
    """The API origin: presign, folder lookups, and a scriptable bulk-register.

    ``register`` is called with the batch's items and returns ``(status,
    payload)``; a status >= 400 is raised as an ``HTTPError`` carrying that
    payload, exactly as ``APIClient._request`` does.
    """

    timeout = 30
    default_timeout = 30

    def __init__(self, register=None):
        self.register = register or _all_created
        self.register_calls = []

    def _url(self, path):
        return f"https://api.pcxa.test/api/companies/3/projects/56/{path}"

    def _request(self, method, url, json=None, params=None, timeout=None, **kwargs):
        path = url.split("projects/56/")[-1]
        if path.endswith("bulk-presign-upload/"):
            items = (json or {}).get("items") or []
            return _Resp(200, {"results": [
                {"index": i, "status": "ok",
                 "upload_url": f"https://r2.example.test/put?i={i}",
                 "storage_key": f"files/uploads/3/56/2026/09/29/k{i}"}
                for i in range(len(items))
            ]})
        if path.endswith("bulk-register/"):
            items = (json or {}).get("items") or []
            self.register_calls.append(items)
            status, payload = self.register(items)
            resp = _Resp(status, payload)
            resp.raise_for_status()
            return resp
        if path.startswith("folders/"):
            return _Resp(200, {"id": 900, "name": "root", "results": [], "count": 0})
        if path.startswith("files/") and method == "GET":
            return _Resp(200, {"results": [], "count": 0, "next": None})
        raise AssertionError(f"unexpected origin call {method} {path}")

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._request("GET", self._url(path), params=params, timeout=timeout).json()

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._request("POST", self._url(path), json=json_data, timeout=timeout).json()


def _all_created(items):
    return 200, {
        "summary": {"created": len(items), "duplicate": 0, "error": 0, "unrecognized": 0,
                    "total": len(items)},
        "results": [{"index": i, "status": "created", "id": 1000 + i} for i in range(len(items))],
    }


@pytest.fixture
def storage(monkeypatch):
    """Direct-to-storage PUT. ``storage.fail`` makes every PUT raise."""
    state = SimpleNamespace(attempted=[], fail=False, delay=0.0)

    class _PutResp:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_put(url, data=None, headers=None, timeout=None, **kwargs):
        state.attempted.append(url)
        if state.delay:
            threading.Event().wait(state.delay)  # time.sleep is shortened by no_backoff
        if state.fail:
            raise HTTPError(_Resp(403, {"detail": "storage says no"}))
        return _PutResp()

    monkeypatch.setattr(files.requests, "put", fake_put)
    monkeypatch.setattr(sync._requests, "put", fake_put)
    return state


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Retries back off with time.sleep; none of these tests is about the delay.

    `sync.time` is the global module, so this also shortens the sleep in
    `files._with_retry`, which imports time locally.
    """
    real_sleep = time.sleep

    def short_sleep(seconds):
        real_sleep(min(seconds, 0.01))

    monkeypatch.setattr(sync.time, "sleep", short_sleep)


def _tree(tmp_path, n, prefix="f"):
    src = tmp_path / "tree"
    src.mkdir(exist_ok=True)
    for i in range(n):
        (src / f"{prefix}{i:03d}.pdf").write_bytes(b"x" * 64)
    return src


def _args(src, **overrides):
    args = SimpleNamespace(
        input_dir=str(src), folder=900, manifest=None,
        include=None, exclude=None, include_hidden=False, tags=None,
        format="json", concurrency=1, max_concurrency=1, min_concurrency=1,
        no_auto_tune=True, max_failures=0, part_concurrency=1,
        multipart_threshold_mb=50, part_size_mb=16, batch_size=200,
        no_bulk_presign=False, limit=0, dry_run=False, trust_manifest=True,
        error_log=None, stats_interval=0,
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _run(client, args, capsys):
    rc = sync.cmd_files_sync(client, args)
    out = capsys.readouterr().out
    return rc, _json.loads(out)


def _assert_invariant(summary):
    accounted = (summary["created"] + summary["duplicate"] + summary["error"]
                 + summary["unrecognized"] + summary["not_attempted"])
    assert accounted == summary["to_upload"], (
        f"summary does not add up: created={summary['created']} duplicate={summary['duplicate']} "
        f"error={summary['error']} unrecognized={summary['unrecognized']} "
        f"not_attempted={summary['not_attempted']} -> {accounted} != to_upload={summary['to_upload']}"
    )


# ---------------------------------------------------------------------------
# exit status
# ---------------------------------------------------------------------------


def test_a_clean_run_exits_zero(tmp_path, storage, capsys):
    rc, summary = _run(FakeOrigin(), _args(_tree(tmp_path, 3)), capsys)

    assert rc == 0
    assert summary["created"] == 3
    _assert_invariant(summary)


def test_a_run_with_a_rejected_row_exits_partial(tmp_path, storage, capsys):
    """One row rejected by the server: the files that landed are real, but the
    run is not complete, and a script must be able to tell."""

    def one_rejected(items):
        results = [{"index": i, "status": "created", "id": 1000 + i} for i in range(len(items))]
        results[1] = {"index": 1, "status": "error", "error": "Invalid storage key."}
        return 200, {"summary": {"created": len(items) - 1, "duplicate": 0, "error": 1,
                                 "unrecognized": 0, "total": len(items)},
                     "results": results}

    rc, summary = _run(FakeOrigin(register=one_rejected), _args(_tree(tmp_path, 3)), capsys)

    assert rc == sync.EXIT_PARTIAL
    assert summary["error"] == 1
    _assert_invariant(summary)


def test_a_failed_upload_exits_partial(tmp_path, storage, capsys):
    storage.fail = True

    rc, summary = _run(FakeOrigin(), _args(_tree(tmp_path, 2)), capsys)

    assert rc == sync.EXIT_PARTIAL
    assert summary["error"] == 2
    _assert_invariant(summary)


def test_an_unrecognized_row_status_is_counted_and_exits_partial(tmp_path, storage, capsys):
    """A status the CLI does not know is neither success nor silence."""

    def mystery(items):
        results = [{"index": i, "status": "created", "id": 1000 + i} for i in range(len(items))]
        results[0] = {"index": 0, "status": "quarantined"}
        return 200, {"summary": {"created": len(items) - 1, "duplicate": 0, "error": 0,
                                 "unrecognized": 1, "total": len(items)},
                     "results": results}

    manifest = tmp_path / "m.json"
    rc, summary = _run(FakeOrigin(register=mystery),
                       _args(_tree(tmp_path, 2), manifest=str(manifest)), capsys)

    assert rc == sync.EXIT_PARTIAL
    assert summary["unrecognized"] == 1
    _assert_invariant(summary)
    recorded = _json.loads(manifest.read_text())["files"]
    assert "f000.pdf" not in recorded, "an unrecognized row must not be recorded as done"


def test_a_failure_budget_abort_exits_aborted(tmp_path, storage, capsys):
    storage.fail = True

    rc, summary = _run(FakeOrigin(), _args(_tree(tmp_path, 5), max_failures=1), capsys)

    assert rc == sync.EXIT_ABORTED
    assert summary["aborted_max_failures"] is True
    _assert_invariant(summary)


def test_a_dry_run_and_an_empty_tree_exit_zero(tmp_path, storage, capsys):
    src = _tree(tmp_path, 2)
    assert sync.cmd_files_sync(FakeOrigin(), _args(src, dry_run=True, format="text")) in (0, None)

    empty = tmp_path / "empty"
    empty.mkdir()
    assert sync.cmd_files_sync(FakeOrigin(), _args(empty)) in (0, None)


def test_the_exit_codes_do_not_collide_with_existing_ones():
    """1 = input/startup and 2 = fatal/auth are already taken; 130 is SIGINT."""
    assert {sync.EXIT_PARTIAL, sync.EXIT_ABORTED}.isdisjoint({0, 1, 2, 130})
    assert sync.EXIT_PARTIAL != sync.EXIT_ABORTED


def test_an_interrupted_run_exits_partial_and_accounts_for_the_rest(tmp_path, storage, capsys, monkeypatch):
    """Ctrl-C is caught inside the upload loop so partial progress is flushed;
    it must still not report success, and the files it never reached are
    not_attempted -- the summary still adds up."""
    real_wait = sync.wait
    calls = {"n": 0}

    def interrupt_on_second_wait(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_wait(*a, **kw)

    monkeypatch.setattr(sync, "wait", interrupt_on_second_wait)
    storage.delay = 0.02

    rc, summary = _run(FakeOrigin(), _args(_tree(tmp_path, 30)), capsys)

    assert calls["n"] >= 2, "the interrupt was never injected"
    assert rc == sync.EXIT_PARTIAL
    assert summary["not_attempted"] > 0, "the interrupt stopped nothing, so this test proves nothing"
    _assert_invariant(summary)


# ---------------------------------------------------------------------------
# accounting after an abort
# ---------------------------------------------------------------------------


def test_an_abort_accounts_for_every_file_and_counts_only_real_failures(tmp_path, storage, capsys):
    """The files the abort never reached are ``not_attempted``, not errors.

    40 files and one worker, so after the first failure trips a budget of 1
    most files are either queued behind the worker or never presigned. Before
    #25 the queued ones were booked as ``error`` ("interrupted") and the rest
    were not counted at all.
    """
    storage.fail = True
    storage.delay = 0.02  # let the main loop see the first failure before the worker races ahead

    rc, summary = _run(FakeOrigin(), _args(_tree(tmp_path, 40), max_failures=1), capsys)

    assert rc == sync.EXIT_ABORTED
    _assert_invariant(summary)
    assert summary["error"] == len(storage.attempted), (
        f"{summary['error']} errors reported but only {len(storage.attempted)} uploads were attempted "
        "-- files that never started are being booked as failures"
    )
    assert summary["not_attempted"] == 40 - len(storage.attempted)
    assert summary["not_attempted"] > 0, "the abort stopped nothing, so this test proves nothing"


# ---------------------------------------------------------------------------
# a final 503 that committed some rows
# ---------------------------------------------------------------------------


def _connection_lost_after(committed):
    """The server's pcxa#1730 abort: the first ``committed`` rows landed, the
    rest were never attempted, and the batch answers 503 every time."""
    attempts = {"n": 0}

    def register(items):
        attempts["n"] += 1
        # First attempt creates them; every retry finds them already there.
        row_status = "created" if attempts["n"] == 1 else "duplicate"
        results = [{"index": i, "status": row_status, "id": 1000 + i} for i in range(committed)]
        return 503, {
            "results": results,
            "summary": {"created": committed if row_status == "created" else 0,
                        "duplicate": committed if row_status == "duplicate" else 0,
                        "error": 0, "unrecognized": 0, "total": committed},
            "aborted": "connection_lost",
            "detail": "Database connection lost mid-batch.",
            "requested": len(items),
        }

    return register


def test_a_final_connection_lost_503_keeps_the_rows_the_server_committed(tmp_path, storage, capsys):
    manifest = tmp_path / "m.json"
    client = FakeOrigin(register=_connection_lost_after(2))

    rc, summary = _run(client, _args(_tree(tmp_path, 5), manifest=str(manifest)), capsys)

    assert len(client.register_calls) == sync.BULK_REGISTER_RETRIES, "the 503 must still be retried"
    assert summary["created"] + summary["duplicate"] == 2, "the two committed rows are real files"
    assert summary["error"] == 3, "only the rows the server never reached failed"
    assert summary["failure_events"] == 1, "one outage is one failure event"
    _assert_invariant(summary)
    assert rc == sync.EXIT_PARTIAL

    recorded = _json.loads(manifest.read_text())["files"]
    assert set(recorded) == {"f000.pdf", "f001.pdf"}, (
        "the committed rows must be in the manifest so a re-run does not re-upload them"
    )


def test_a_503_without_committed_rows_still_fails_the_whole_batch(tmp_path, storage, capsys):
    """Guard for the branch above: a plain 503 carries no results to trust."""

    def plain_503(items):
        return 503, {"detail": "Service Unavailable"}

    rc, summary = _run(FakeOrigin(register=plain_503), _args(_tree(tmp_path, 3)), capsys)

    assert summary["error"] == 3
    assert summary["created"] == 0
    _assert_invariant(summary)
    assert rc == sync.EXIT_PARTIAL


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------


def test_a_rejected_row_never_enters_the_manifest_and_is_retried(tmp_path, storage, capsys):
    """``--trust-manifest`` skips whatever the manifest says is done, so a failed
    row in it would be skipped forever. It must not be there."""

    def reject_first(items):
        results = [{"index": i, "status": "created", "id": 1000 + i} for i in range(len(items))]
        results[0] = {"index": 0, "status": "error", "error": "deadlock"}
        return 200, {"summary": {"created": len(items) - 1, "duplicate": 0, "error": 1,
                                 "unrecognized": 0, "total": len(items)},
                     "results": results}

    src = _tree(tmp_path, 3)
    manifest = tmp_path / "m.json"
    _run(FakeOrigin(register=reject_first), _args(src, manifest=str(manifest)), capsys)

    recorded = _json.loads(manifest.read_text())["files"]
    assert "f000.pdf" not in recorded
    assert {"f001.pdf", "f002.pdf"} <= set(recorded)

    rerun = FakeOrigin()
    rc, summary = _run(rerun, _args(src, manifest=str(manifest), trust_manifest=True), capsys)
    assert summary["to_upload"] == 1, "the re-run must retry exactly the rejected file"
    assert [i["original_filename"] for i in rerun.register_calls[0]] == ["f000.pdf"]
    assert rc == 0
