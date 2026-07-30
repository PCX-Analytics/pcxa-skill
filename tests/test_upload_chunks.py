"""Tests for `pcxa files upload-chunks` / `files set-index-mode`.

The validation tests matter more than usual here: every rejection they cover is
one that would otherwise cost a 5,000-chunk request, or — worse — silently
succeed and produce a corpus that retrieves badly.
"""

import json
from argparse import Namespace

import pytest

from pcxa.commands import chunks as C
from tests.conftest import FakeResponse, RecordingSession


EMB = [0.0] * C.EMBEDDING_DIMENSIONS


def _args(paths, **over):
    base = dict(
        paths=[str(p) for p in paths],
        embedding_model=C.DEFAULT_EMBEDDING_MODEL,
        manifest=None, state=None,
        chunks_per_hour=0,                # no sleeping in tests
        files_per_request=50, chunks_per_request=5000,
        limit=0, max_failures=100, error_log=None,
        dry_run=False, format="json",
    )
    base.update(over)
    return Namespace(**base)


def _jsonl(tmp_path, *records, name="in.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def _ok(file_ids):
    return FakeResponse(200, {"results": [{"file_id": f, "chunk_count": 1} for f in file_ids]})


# ── happy path ───────────────────────────────────────────────────────────────


def test_posts_chunks_with_embeddings(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {
        "file_id": 7,
        "chunks": [{"chunk_index": 0, "content": "hello", "embedding": EMB}],
    })
    client.session = RecordingSession(responses=[_ok([7])])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 0
    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/companies/1/projects/2/semantic-search/upload-chunks/")
    body = call["json"]
    assert body["embedding_model"] == C.DEFAULT_EMBEDDING_MODEL
    assert body["items"][0]["file_id"] == 7
    assert len(body["items"][0]["chunks"][0]["embedding"]) == C.EMBEDDING_DIMENSIONS

    out = json.loads(capsys.readouterr().out)
    assert out["files_applied"] == 1
    assert out["chunks_sent"] == 1
    assert out["with_embeddings"] == 1


def test_chunks_without_embeddings_are_allowed(client, tmp_path, capsys):
    """Chunk-only upload is valid — our embedder picks the file up."""
    src = _jsonl(tmp_path, {"file_id": 8, "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[_ok([8])])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 0
    assert "embedding" not in client.session.calls[0]["json"]["items"][0]["chunks"][0]
    assert json.loads(capsys.readouterr().out)["without_embeddings"] == 1


def test_optional_per_file_fields_pass_through(client, tmp_path):
    src = _jsonl(tmp_path, {
        "file_id": 9, "file_version_id": 44, "document_summary": "s",
        "indexed_content_hash": "a" * 64,
        "chunks": [{"chunk_index": 0, "content": "x", "page_number": 3,
                    "content_hash": "b" * 64, "metadata": {"k": "v"}}],
    })
    client.session = RecordingSession(responses=[_ok([9])])

    C.cmd_files_upload_chunks(client, _args([src]))

    item = client.session.calls[0]["json"]["items"][0]
    assert item["file_version_id"] == 44
    assert item["document_summary"] == "s"
    assert item["chunks"][0]["page_number"] == 3
    assert item["chunks"][0]["metadata"] == {"k": "v"}


# ── validation: the expensive mistakes ───────────────────────────────────────


def test_wrong_embedding_dimensions_rejected_before_sending(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {
        "file_id": 1,
        "chunks": [{"chunk_index": 0, "content": "x", "embedding": [0.0] * 512}],
    })
    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 1
    assert client.session.calls == []          # never hit the network
    assert "512 dimensions" in capsys.readouterr().err


def test_partial_embeddings_rejected(client, tmp_path, capsys):
    """One missing vector demotes the whole file to CHUNKED and bills us."""
    src = _jsonl(tmp_path, {"file_id": 2, "chunks": [
        {"chunk_index": 0, "content": "a", "embedding": EMB},
        {"chunk_index": 1, "content": "b"},
    ]})
    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 1
    assert client.session.calls == []
    assert "1/2 chunks carry an embedding" in capsys.readouterr().err


def test_duplicate_chunk_index_rejected(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 3, "chunks": [
        {"chunk_index": 0, "content": "a"}, {"chunk_index": 0, "content": "b"},
    ]})
    assert C.cmd_files_upload_chunks(client, _args([src])) == 1
    assert "duplicate chunk_index 0" in capsys.readouterr().err


def test_oversized_content_rejected(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {
        "file_id": 4,
        "chunks": [{"chunk_index": 0, "content": "x" * (C.MAX_CONTENT_CHARS + 1)}],
    })
    assert C.cmd_files_upload_chunks(client, _args([src])) == 1
    assert "over the 16000 limit" in capsys.readouterr().err


def test_too_many_chunks_per_file_rejected(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {
        "file_id": 5,
        "chunks": [{"chunk_index": i, "content": "x"}
                   for i in range(C.MAX_CHUNKS_PER_FILE + 1)],
    })
    assert C.cmd_files_upload_chunks(client, _args([src])) == 1
    assert "per-file server limit" in capsys.readouterr().err


def test_one_bad_record_does_not_stop_the_good_ones(client, tmp_path, capsys):
    src = _jsonl(
        tmp_path,
        {"file_id": 10, "chunks": [{"chunk_index": 0, "content": "ok"}]},
        {"file_id": 11, "chunks": []},                                  # bad
        {"file_id": 12, "chunks": [{"chunk_index": 0, "content": "ok"}]},
    )
    client.session = RecordingSession(responses=[_ok([10, 12])])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 1                                    # a rejection happened
    sent = [i["file_id"] for i in client.session.calls[0]["json"]["items"]]
    assert sent == [10, 12]                           # the good ones still went
    assert json.loads(capsys.readouterr().out)["files_rejected"] == 1


# ── batching ─────────────────────────────────────────────────────────────────


def test_batches_split_on_file_count(client, tmp_path):
    records = [{"file_id": i, "chunks": [{"chunk_index": 0, "content": "x"}]}
               for i in range(5)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(
        responses=[_ok([0, 1]), _ok([2, 3]), _ok([4])]
    )

    C.cmd_files_upload_chunks(client, _args([src], files_per_request=2))

    posts = [c for c in client.session.calls if c["method"] == "POST"]
    assert [len(p["json"]["items"]) for p in posts] == [2, 2, 1]


def test_batches_split_on_chunk_budget(client, tmp_path):
    records = [{"file_id": i, "chunks": [{"chunk_index": j, "content": "x"}
                                         for j in range(3)]} for i in range(3)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(responses=[_ok([0]), _ok([1]), _ok([2])])

    C.cmd_files_upload_chunks(client, _args([src], chunks_per_request=4))

    posts = [c for c in client.session.calls if c["method"] == "POST"]
    assert [len(p["json"]["items"]) for p in posts] == [1, 1, 1]


def test_request_caps_are_clamped_to_server_maxima(client, tmp_path):
    src = _jsonl(tmp_path, {"file_id": 1, "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[_ok([1])])

    # Asking for more than the server allows must not produce a rejected request.
    C.cmd_files_upload_chunks(
        client, _args([src], files_per_request=9999, chunks_per_request=999999)
    )
    assert len(client.session.calls) == 1


# ── resume, manifest, dry-run ────────────────────────────────────────────────


def test_state_file_makes_reruns_skip_applied_files(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 21, "chunks": [{"chunk_index": 0, "content": "x"}]})
    state = tmp_path / "state.json"
    client.session = RecordingSession(responses=[_ok([21])])

    C.cmd_files_upload_chunks(client, _args([src], state=str(state)))
    capsys.readouterr()
    assert json.loads(state.read_text())["applied"] == [21]

    client.session = RecordingSession(responses=[_ok([21])])
    C.cmd_files_upload_chunks(client, _args([src], state=str(state)))

    assert client.session.calls == []                 # nothing re-sent
    assert json.loads(capsys.readouterr().out)["files_skipped_resume"] == 1


def test_manifest_resolves_path_to_file_id(client, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "files": {"docs/a.pdf": {"file_id": 55, "name": "a.pdf"}},
    }))
    src = _jsonl(tmp_path, {"path": "docs/a.pdf",
                            "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[_ok([55])])

    C.cmd_files_upload_chunks(client, _args([src], manifest=str(manifest)))

    assert client.session.calls[0]["json"]["items"][0]["file_id"] == 55


def test_ambiguous_manifest_name_is_not_guessed(client, tmp_path, capsys):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"version": 1, "files": {
        "x/dup.pdf": {"file_id": 1, "name": "dup.pdf"},
        "y/dup.pdf": {"file_id": 2, "name": "dup.pdf"},
    }}))
    src = _jsonl(tmp_path, {"name": "dup.pdf",
                            "chunks": [{"chunk_index": 0, "content": "x"}]})

    rc = C.cmd_files_upload_chunks(client, _args([src], manifest=str(manifest)))

    assert rc == 1
    assert client.session.calls == []
    assert "not in the manifest" in capsys.readouterr().err


def test_dry_run_sends_nothing(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 31,
                            "chunks": [{"chunk_index": 0, "content": "x", "embedding": EMB}]})

    rc = C.cmd_files_upload_chunks(client, _args([src], dry_run=True))

    assert rc == 0
    assert client.session.calls == []
    assert json.loads(capsys.readouterr().out)["chunks_sent"] == 1


def test_directory_input_is_walked_for_jsonl(client, tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    _jsonl(d, {"file_id": 41, "chunks": [{"chunk_index": 0, "content": "x"}]}, name="a.jsonl")
    _jsonl(d, {"file_id": 42, "chunks": [{"chunk_index": 0, "content": "x"}]}, name="b.jsonl")
    client.session = RecordingSession(responses=[_ok([41, 42])])

    C.cmd_files_upload_chunks(client, _args([d]))

    sent = [i["file_id"] for i in client.session.calls[0]["json"]["items"]]
    assert sorted(sent) == [41, 42]


# ── transport ────────────────────────────────────────────────────────────────


def test_429_is_retried_honouring_retry_after(client, tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(C.time, "sleep", lambda s: slept.append(s))

    throttled = FakeResponse(429, {"detail": "throttled"})
    throttled.headers = {"Retry-After": "7"}
    src = _jsonl(tmp_path, {"file_id": 61, "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[throttled, _ok([61])])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 0
    assert 7.0 in slept
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 2


def test_4xx_other_than_429_is_not_retried(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 62, "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[FakeResponse(403, {"error": "nope"})])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 1
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 1
    assert "failed" in capsys.readouterr().err


def test_per_item_server_error_is_reported_not_swallowed(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 63, "chunks": [{"chunk_index": 0, "content": "x"}]})
    client.session = RecordingSession(responses=[
        FakeResponse(200, {"results": [{"file_id": 63, "error": "File 63 not found."}]})
    ])

    rc = C.cmd_files_upload_chunks(client, _args([src]))

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["files_applied"] == 0


# ── pacing ───────────────────────────────────────────────────────────────────


def test_pacer_defaults_to_the_lake_drain_rate():
    """Guards the number, because it is the reason the default exists."""
    assert C.LAKE_DRAIN_CHUNKS_PER_HOUR == 60_000


def test_pacer_sleeps_when_ahead_of_the_budget(monkeypatch):
    slept = []
    monkeypatch.setattr(C.time, "sleep", lambda s: slept.append(s))
    clock = {"t": 1000.0}
    monkeypatch.setattr(C.time, "monotonic", lambda: clock["t"])

    pacer = C.Pacer(3600)          # 1 chunk/second
    pacer.record_and_wait(10)      # 10s of budget, 0s elapsed

    assert slept and abs(slept[0] - 10.0) < 0.01


def test_pacer_disabled_never_sleeps(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: pytest.fail("should not sleep"))
    assert C.Pacer(0).record_and_wait(1_000_000) == 0.0


# ── set-index-mode ───────────────────────────────────────────────────────────


def _mode_args(**over):
    base = dict(file_ids=[], ids_file=None, from_manifest=None, mode="none",
                index_text_source=None, format="json")
    base.update(over)
    return Namespace(**base)


def test_set_index_mode_none(client, capsys):
    client.session = RecordingSession(responses=[FakeResponse(200, {"updated": 3, "skipped": []})])
    args = _mode_args(file_ids=["1", "2,3"])

    rc = C.cmd_files_set_index_mode(client, args)

    assert rc == 0
    body = client.session.calls[0]["json"]
    assert body == {"file_ids": [1, 2, 3], "index_mode": "none"}
    assert json.loads(capsys.readouterr().out)["updated"] == 3


def test_set_index_mode_rejects_non_integer_ids(client, capsys):
    assert C.cmd_files_set_index_mode(client, _mode_args(file_ids=["abc"])) == 1
    assert client.session.calls == []
    assert "not an integer" in capsys.readouterr().err


def test_set_index_mode_chunks_beyond_the_server_cap(client):
    ids = [str(i) for i in range(10_001)]
    client.session = RecordingSession(responses=[
        FakeResponse(200, {"updated": 10_000, "skipped": []}),
        FakeResponse(200, {"updated": 1, "skipped": []}),
    ])

    C.cmd_files_set_index_mode(client, _mode_args(file_ids=ids))

    posts = [c for c in client.session.calls if c["method"] == "POST"]
    assert [len(p["json"]["file_ids"]) for p in posts] == [10_000, 1]


def test_set_index_mode_reads_ids_file(client, tmp_path):
    """A corpus's worth of ids does not fit in argv."""
    f = tmp_path / "ids.txt"
    f.write_text("1 2,3\n4\n")
    client.session = RecordingSession(responses=[FakeResponse(200, {"updated": 4, "skipped": []})])

    C.cmd_files_set_index_mode(client, _mode_args(ids_file=str(f)))

    assert client.session.calls[0]["json"]["file_ids"] == [1, 2, 3, 4]


def test_set_index_mode_from_sync_manifest(client, tmp_path):
    """'The corpus I just uploaded' without a jq incantation."""
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"version": 1, "files": {
        "a.pdf": {"file_id": 11, "name": "a.pdf"},
        "b.pdf": {"file_id": 12, "name": "b.pdf"},
        "skipped.pdf": {"skipped_reason": "already_in_target_folder"},   # no id
    }}))
    client.session = RecordingSession(responses=[FakeResponse(200, {"updated": 2, "skipped": []})])

    C.cmd_files_set_index_mode(client, _mode_args(from_manifest=str(m)))

    assert sorted(client.session.calls[0]["json"]["file_ids"]) == [11, 12]


def test_set_index_mode_dedupes_ids(client):
    client.session = RecordingSession(responses=[FakeResponse(200, {"updated": 2, "skipped": []})])

    C.cmd_files_set_index_mode(client, _mode_args(file_ids=["7", "7", "8"]))

    assert client.session.calls[0]["json"]["file_ids"] == [7, 8]


def test_set_index_mode_with_no_ids_at_all_errors(client, capsys):
    assert C.cmd_files_set_index_mode(client, _mode_args()) == 1
    assert client.session.calls == []
    assert "--from-manifest" in capsys.readouterr().err
