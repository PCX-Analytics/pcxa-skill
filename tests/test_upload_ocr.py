"""Tests for `pcxa files upload-ocr`.

Two classes of test matter more than usual here:

* **The union arms.** A fixture of each envelope shape, sent end-to-end, is what
  stops a refactor from quietly accepting only the one the author had in front
  of them.
* **Byte batching.** The request body cap is a 413 the server raises *before*
  parsing, so nothing in `results` names the file that caused it. A batcher that
  packs by file count alone passes every other test and fails in production.
"""

import json
from argparse import Namespace

from pcxa.commands import ocr as O
from tests.conftest import FakeResponse, RecordingSession


# ── fixtures: one envelope of each union arm ─────────────────────────────────


def layout_envelope(pages=1, words_per_line=1, producer="mxi-scanner/1.4"):
    """`pcxa.ocr_layout.v1` — text with geometry."""
    return {
        "schema": O.SCHEMA_LAYOUT,
        "provenance": {"producer": producer, "tool_version": "1.4.0"},
        "pages": [
            {
                "n": n, "w": 2550, "h": 3300,
                "lines": [{
                    "t": "REINFORCED CONCRETE SLAB",
                    "b": [0.08, 0.10, 0.92, 0.14],
                    "c": 0.97,
                    "words": [["REINFORCED", 0.08, 0.10, 0.24, 0.14, 0.99]] * words_per_line,
                }],
            }
            for n in range(1, pages + 1)
        ],
    }


def text_envelope(pages=1, text="REINFORCED CONCRETE SLAB"):
    """`pcxa.text.v1` — no geometry, fully supported."""
    return {
        "schema": O.SCHEMA_TEXT,
        "provenance": {"producer": "mxi-scanner/1.4"},
        "pages": [{"n": n, "text": text} for n in range(1, pages + 1)],
    }


def _args(paths, **over):
    base = dict(
        paths=[str(p) for p in paths],
        manifest=None, state=None,
        validate_only=False, overwrite=False,
        files_per_request=50, max_bytes=O.DEFAULT_MAX_BYTES,
        pages_per_hour=0,                 # no sleeping in tests
        limit=0, max_failures=100, error_log=None,
        format="json",
    )
    base.update(over)
    return Namespace(**base)


def _jsonl(tmp_path, *records, name="in.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def _applied(*file_ids, deduplicated=False, warnings=()):
    return FakeResponse(200, {"results": [
        {"file_id": f, "applied": True, "status": "ocr_complete", "pages": 1,
         "pages_applied": [1], "deduplicated": deduplicated, "warnings": list(warnings)}
        for f in file_ids
    ]})


def _refused(*pairs, **extra):
    """``_refused((123, "index_parked"), ...)`` → a response of refusals."""
    return FakeResponse(200, {"results": [
        {"file_id": f, "applied": False, "error_code": code,
         "error": f"refused: {code}", **extra}
        for f, code in pairs
    ]})


# ── both union arms go end-to-end ────────────────────────────────────────────


def test_layout_envelope_is_posted_verbatim(client, tmp_path, capsys):
    env = layout_envelope()
    src = _jsonl(tmp_path, {"file_id": 7, "envelope": env})
    client.session = RecordingSession(responses=[_applied(7)])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 0
    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/companies/1/projects/2/semantic-search/upload-ocr/")
    item = call["json"]["items"][0]
    assert item["file_id"] == 7
    # Full precision, unrounded: the stored copy is the master and everything
    # downstream is re-derived from it.
    assert item["envelope"] == env
    assert "validate_only" not in call["json"]

    out = json.loads(capsys.readouterr().out)
    assert out["files_applied"] == 1
    assert out["pages_sent"] == 1


def test_text_envelope_is_accepted(client, tmp_path, capsys):
    """No geometry is fully supported — and better than fabricated boxes."""
    src = _jsonl(tmp_path, {"file_id": 8, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(8)])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 0
    assert client.session.calls[0]["json"]["items"][0]["envelope"]["schema"] == O.SCHEMA_TEXT
    assert json.loads(capsys.readouterr().out)["files_applied"] == 1


def test_no_idempotency_key_is_sent(client, tmp_path):
    """Dedup is content-addressed server-side; a client-computed key is wrong."""
    src = _jsonl(tmp_path, {"file_id": 9, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(9)])

    O.cmd_files_upload_ocr(client, _args([src]))

    headers = client.session.calls[0].get("headers") or {}
    assert not any(h.lower() == "idempotency-key" for h in headers)


def test_deduplicated_replies_are_counted_not_treated_as_failure(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 10, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(10, deduplicated=True)])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["files_applied"] == 1
    assert out["files_deduplicated"] == 1


def test_warnings_are_advisory_not_failures(client, tmp_path, capsys):
    """A word box outside its line box is legitimate for rotated text."""
    src = _jsonl(tmp_path, {"file_id": 11, "envelope": layout_envelope()})
    client.session = RecordingSession(
        responses=[_applied(11, warnings=["word box outside line box"])]
    )

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["warnings"] == 1
    assert out["files_applied"] == 1


# ── overwrite ────────────────────────────────────────────────────────────────


def test_overwrite_defaults_off(client, tmp_path):
    src = _jsonl(tmp_path, {"file_id": 12, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(12)])

    O.cmd_files_upload_ocr(client, _args([src]))

    assert "overwrite" not in client.session.calls[0]["json"]["items"][0]


def test_per_record_overwrite_wins_over_the_flag(client, tmp_path):
    src = _jsonl(
        tmp_path,
        {"file_id": 13, "envelope": text_envelope(), "overwrite": False},
        {"file_id": 14, "envelope": text_envelope()},
    )
    client.session = RecordingSession(responses=[_applied(13, 14)])

    O.cmd_files_upload_ocr(client, _args([src], overwrite=True))

    items = {i["file_id"]: i for i in client.session.calls[0]["json"]["items"]}
    assert "overwrite" not in items[13]        # record said no
    assert items[14]["overwrite"] is True      # record was silent, flag applied


def test_optional_per_record_fields_pass_through(client, tmp_path):
    src = _jsonl(tmp_path, {
        "file_id": 15, "file_version_id": 44,
        "provenance": {"scanner": "mxi-workstation-02"},
        "envelope": text_envelope(),
    })
    client.session = RecordingSession(responses=[_applied(15)])

    O.cmd_files_upload_ocr(client, _args([src]))

    item = client.session.calls[0]["json"]["items"][0]
    assert item["file_version_id"] == 44
    assert item["provenance"] == {"scanner": "mxi-workstation-02"}


# ── client-side envelope validation ──────────────────────────────────────────


def _rejects(client, tmp_path, record, fragment, capsys):
    src = _jsonl(tmp_path, record)
    rc = O.cmd_files_upload_ocr(client, _args([src]))
    assert rc == 1
    assert client.session.calls == []          # never hit the network
    assert fragment in capsys.readouterr().err


def test_unknown_schema_rejected(client, tmp_path, capsys):
    env = text_envelope()
    env["schema"] = "pcxa.ocr_layout.v2"
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env}, "closed union", capsys)


def test_page_numbers_are_one_indexed(client, tmp_path, capsys):
    env = text_envelope()
    env["pages"][0]["n"] = 0
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env}, "1-indexed", capsys)


def test_duplicate_page_number_rejected(client, tmp_path, capsys):
    env = text_envelope(pages=2)
    env["pages"][1]["n"] = 1
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env}, "duplicate page number", capsys)


def test_empty_pages_rejected_but_blank_page_is_legal(client, tmp_path, capsys):
    env = layout_envelope()
    env["pages"] = []
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env}, "non-empty list", capsys)


def test_blank_page_is_legal(client, tmp_path):
    """`"lines": []` is how you say "nothing here" — it must not be rejected."""
    env = layout_envelope()
    env["pages"][0]["lines"] = []
    src = _jsonl(tmp_path, {"file_id": 16, "envelope": env})
    client.session = RecordingSession(responses=[_applied(16)])

    assert O.cmd_files_upload_ocr(client, _args([src])) == 0
    assert len(client.session.calls) == 1


def test_zero_width_bbox_rejected(client, tmp_path, capsys):
    env = layout_envelope()
    env["pages"][0]["lines"][0]["b"] = [0.5, 0.10, 0.5, 0.14]
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env},
             "x1 <= x0 (zero or negative width)", capsys)


def test_coordinate_outside_unit_range_rejected(client, tmp_path, capsys):
    env = layout_envelope()
    env["pages"][0]["lines"][0]["b"] = [0.08, 0.10, 1.4, 0.14]
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env},
             "coordinates must be in [0, 1]", capsys)


def test_word_box_is_validated_too(client, tmp_path, capsys):
    env = layout_envelope()
    env["pages"][0]["lines"][0]["words"] = [["REINFORCED", 0.5, 0.10, 0.4, 0.14]]
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env},
             "x1 <= x0", capsys)


def test_zero_confidence_rejected_with_the_fix_named(client, tmp_path, capsys):
    """`0` asserts the text is certainly wrong, and is a common exporter default."""
    env = layout_envelope()
    env["pages"][0]["lines"][0]["c"] = 0
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env},
             "Omit the field instead", capsys)


def test_confidence_is_optional(client, tmp_path):
    env = layout_envelope()
    del env["pages"][0]["lines"][0]["c"]
    env["pages"][0]["lines"][0]["words"] = [["REINFORCED", 0.08, 0.10, 0.24, 0.14]]
    src = _jsonl(tmp_path, {"file_id": 17, "envelope": env})
    client.session = RecordingSession(responses=[_applied(17)])

    assert O.cmd_files_upload_ocr(client, _args([src])) == 0


def test_layout_envelope_requires_a_producer(client, tmp_path, capsys):
    env = layout_envelope()
    del env["provenance"]
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env}, "producer", capsys)


def test_too_many_pages_rejected(client, tmp_path, capsys):
    env = text_envelope(pages=O.MAX_PAGES_PER_FILE + 1)
    _rejects(client, tmp_path, {"file_id": 1, "envelope": env},
             "per-file server limit", capsys)


def test_record_without_envelope_points_at_upload_chunks(client, tmp_path, capsys):
    """The whole point: make it hard to pick the wrong command."""
    _rejects(client, tmp_path,
             {"file_id": 1, "chunks": [{"chunk_index": 0, "content": "x"}]},
             "upload-chunks", capsys)


def test_one_bad_record_does_not_stop_the_good_ones(client, tmp_path, capsys):
    bad = text_envelope()
    bad["pages"][0]["n"] = -1
    src = _jsonl(
        tmp_path,
        {"file_id": 20, "envelope": text_envelope()},
        {"file_id": 21, "envelope": bad},
        {"file_id": 22, "envelope": text_envelope()},
    )
    client.session = RecordingSession(responses=[_applied(20, 22)])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 1
    sent = [i["file_id"] for i in client.session.calls[0]["json"]["items"]]
    assert sent == [20, 22]
    assert json.loads(capsys.readouterr().out)["files_rejected"] == 1


# ── batching: by bytes AND by file count ─────────────────────────────────────


def test_batches_split_on_file_count(client, tmp_path):
    records = [{"file_id": i, "envelope": text_envelope()} for i in range(5)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(
        responses=[_applied(0, 1), _applied(2, 3), _applied(4)]
    )

    O.cmd_files_upload_ocr(client, _args([src], files_per_request=2))

    posts = [c for c in client.session.calls if c["method"] == "POST"]
    assert [len(p["json"]["items"]) for p in posts] == [2, 2, 1]


def test_batches_split_on_the_byte_budget(client, tmp_path):
    """Packing by file count alone walks straight into a 413."""
    records = [{"file_id": i, "envelope": text_envelope(text="x" * 2000)}
               for i in range(3)]
    src = _jsonl(tmp_path, *records)
    one = O._item_bytes({"file_id": 0, "envelope": text_envelope(text="x" * 2000)})
    client.session = RecordingSession(
        responses=[_applied(0), _applied(1), _applied(2)]
    )

    # Room for exactly one item per request, while files_per_request says 50.
    O.cmd_files_upload_ocr(
        client, _args([src], max_bytes=one + 10, files_per_request=50)
    )

    posts = [c for c in client.session.calls if c["method"] == "POST"]
    assert [len(p["json"]["items"]) for p in posts] == [1, 1, 1]


def test_item_bytes_matches_the_transport_serialization(tmp_path):
    """`_http` sends json.dumps(payload) with DEFAULT separators, spaces included.

    Measuring compactly would under-count and produce the 413 this budget exists
    to prevent.
    """
    item = {"file_id": 1, "envelope": layout_envelope(pages=3)}
    assert O._item_bytes(item) == len(json.dumps(item).encode("utf-8"))
    # And is strictly larger than a compact measurement, which is the bug.
    compact = len(json.dumps(item, separators=(",", ":")).encode("utf-8"))
    assert O._item_bytes(item) > compact


def test_item_bytes_counts_utf8_bytes_not_characters():
    """Non-ASCII text must not under-count against the byte cap."""
    item = {"file_id": 1, "envelope": text_envelope(text="—" * 100)}
    assert O._item_bytes(item) == len(json.dumps(item).encode("utf-8"))


def test_single_oversized_file_is_rejected_before_send(client, tmp_path, capsys):
    """An envelope is the file's text master — there is no split across requests."""
    src = _jsonl(tmp_path, {"file_id": 30, "envelope": text_envelope(text="x" * 5000)})

    rc = O.cmd_files_upload_ocr(client, _args([src], max_bytes=1000))

    assert rc == 1
    assert client.session.calls == []
    err = capsys.readouterr().err
    assert "cannot be split across requests" in err


def test_request_caps_are_clamped_to_server_maxima(client, tmp_path):
    src = _jsonl(tmp_path, {"file_id": 31, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(31)])

    O.cmd_files_upload_ocr(
        client, _args([src], files_per_request=9999, max_bytes=999_999_999)
    )

    assert len(client.session.calls) == 1


# ── refusals ─────────────────────────────────────────────────────────────────


def test_non_retryable_refusal_is_reported_and_never_retried(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 40, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_refused((40, "index_parked"))])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 1
    # Exactly one request: retrying a parked file only burns the rate limit.
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["refusals"] == {"index_parked": 1}
    assert out["files_applied"] == 0


def test_refused_files_are_not_recorded_as_applied(client, tmp_path):
    """A refusal must not poison --state, or a re-run would skip the file."""
    state = tmp_path / "state.json"
    src = _jsonl(
        tmp_path,
        {"file_id": 41, "envelope": text_envelope()},
        {"file_id": 42, "envelope": text_envelope()},
    )
    client.session = RecordingSession(responses=[FakeResponse(200, {"results": [
        {"file_id": 41, "applied": True, "warnings": []},
        {"file_id": 42, "applied": False, "error_code": "ocr_batch_in_flight"},
    ]})])

    O.cmd_files_upload_ocr(client, _args([src], state=str(state)))

    assert json.loads(state.read_text())["applied"] == [41]


def test_transient_refusals_are_flagged_for_a_re_run(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 43, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_refused((43, "chunking_in_progress"))])

    O.cmd_files_upload_ocr(client, _args([src]))

    assert "Re-run with the same --state" in capsys.readouterr().err


def test_parked_reason_and_shape_errors_are_surfaced(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 44, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(200, {"results": [
        {"file_id": 44, "applied": False, "error_code": "invalid_envelope",
         "errors": ["pages[3].lines[17].words[2]: x1 <= x0 (zero or negative width)"]},
    ]})])

    O.cmd_files_upload_ocr(client, _args([src]))

    err = capsys.readouterr().err
    assert "pages[3].lines[17].words[2]" in err


def test_every_documented_error_code_is_classified():
    """A code in neither set would be silently treated as non-retryable."""
    documented = {
        "index_parked", "already_indexed", "external_chunk_source",
        "project_external_chunks_only", "index_excluded_by_policy",
        "file_version_mismatch", "incomplete_page_coverage", "has_server_text",
        "has_ocr_text", "derivation_pending", "not_found",
        "ocr_batch_in_flight", "chunking_in_progress",
    }
    assert documented <= (O.NON_RETRYABLE_CODES | O.RETRYABLE_CODES)
    assert not (O.NON_RETRYABLE_CODES & O.RETRYABLE_CODES)


def test_confusing_refusals_carry_help_text():
    """These two will confuse a caller; the issue asks for them to be explained."""
    assert "deliberate exclusion" in O.CODE_NOTES["index_parked"]
    assert "partially-OCR'd" in O.CODE_NOTES["incomplete_page_coverage"]


# ── --validate-only ──────────────────────────────────────────────────────────


def test_validate_only_sets_the_flag_and_writes_no_state(client, tmp_path):
    state = tmp_path / "state.json"
    src = _jsonl(tmp_path, {"file_id": 50, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(200, {
        "validate_only": True,
        "results": [{"file_id": 50, "applied": False, "warnings": []}],
    })])

    rc = O.cmd_files_upload_ocr(client, _args([src], state=str(state), validate_only=True))

    assert rc == 0
    assert client.session.calls[0]["json"]["validate_only"] is True
    assert not state.exists()          # a dry run must not advance resume state


def test_validate_only_prints_a_refusal_histogram(client, tmp_path, capsys):
    records = [{"file_id": i, "envelope": text_envelope()} for i in range(5)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(responses=[_refused(
        (0, "index_parked"), (1, "index_parked"), (2, "index_parked"),
        (3, "already_indexed"), (4, "not_found"),
    )])

    rc = O.cmd_files_upload_ocr(
        client, _args([src], validate_only=True, format="table")
    )

    out = capsys.readouterr().out
    assert "Refused: 5 file(s), by error_code" in out
    # Ordered by count, descending — the histogram is the answer, not a wall.
    assert out.index("index_parked") < out.index("already_indexed")
    assert "not retryable" in out
    # A parked corpus is a discovery, not a failed run.
    assert rc == 0


def test_validate_only_refusals_do_not_spend_the_failure_budget(client, tmp_path, capsys):
    """Aborting a dry run at the 100th parked file defeats its entire purpose."""
    records = [{"file_id": i, "envelope": text_envelope()} for i in range(30)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(responses=[
        _refused(*[(i, "index_parked") for i in range(30)])
    ])

    rc = O.cmd_files_upload_ocr(
        client, _args([src], validate_only=True, max_failures=5)
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["files_refused"] == 30
    assert out["refusals"]["index_parked"] == 30
    assert out["error_count"] == 0


def test_validate_only_still_fails_on_a_bad_envelope(client, tmp_path, capsys):
    env = text_envelope()
    env["pages"][0]["n"] = 0
    src = _jsonl(tmp_path, {"file_id": 51, "envelope": env})

    rc = O.cmd_files_upload_ocr(client, _args([src], validate_only=True))

    assert rc == 1
    assert client.session.calls == []


def test_validate_only_ignores_existing_state(client, tmp_path):
    """The dry run must show the whole target list, not the unfinished remainder."""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"version": 1, "applied": [52]}))
    src = _jsonl(tmp_path, {"file_id": 52, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(200, {"results": [
        {"file_id": 52, "applied": False, "warnings": []}]})])

    O.cmd_files_upload_ocr(client, _args([src], state=str(state), validate_only=True))

    assert len(client.session.calls) == 1
    assert client.session.calls[0]["json"]["items"][0]["file_id"] == 52


def test_refusal_echo_is_capped(client, tmp_path, capsys):
    n = O.MAX_ECHOED_REFUSALS + 10
    records = [{"file_id": i, "envelope": text_envelope()} for i in range(n)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(responses=[
        _refused(*[(i, "index_parked") for i in range(n)])
    ])

    O.cmd_files_upload_ocr(client, _args([src], validate_only=True))

    err = capsys.readouterr().err
    echoed = [l for l in err.splitlines() if l.startswith("  ! file ")]
    assert len(echoed) == O.MAX_ECHOED_REFUSALS
    assert "further refusals are counted in the histogram" in err


# ── resume, manifest ─────────────────────────────────────────────────────────


def test_state_file_makes_reruns_skip_applied_files(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 60, "envelope": text_envelope()})
    state = tmp_path / "state.json"
    client.session = RecordingSession(responses=[_applied(60)])

    O.cmd_files_upload_ocr(client, _args([src], state=str(state)))
    capsys.readouterr()
    assert json.loads(state.read_text())["applied"] == [60]

    client.session = RecordingSession(responses=[_applied(60)])
    O.cmd_files_upload_ocr(client, _args([src], state=str(state)))

    assert client.session.calls == []
    assert json.loads(capsys.readouterr().out)["files_skipped_resume"] == 1


def test_manifest_resolves_path_to_file_id(client, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "files": {"Sources/RFI-142.pdf": {"file_id": 61, "name": "RFI-142.pdf"}},
    }))
    src = _jsonl(tmp_path, {"path": "Sources/RFI-142.pdf", "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_applied(61)])

    O.cmd_files_upload_ocr(client, _args([src], manifest=str(manifest)))

    assert client.session.calls[0]["json"]["items"][0]["file_id"] == 61


def test_ambiguous_manifest_name_is_not_guessed(client, tmp_path, capsys):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"version": 1, "files": {
        "x/dup.pdf": {"file_id": 1, "name": "dup.pdf"},
        "y/dup.pdf": {"file_id": 2, "name": "dup.pdf"},
    }}))
    src = _jsonl(tmp_path, {"name": "dup.pdf", "envelope": text_envelope()})

    rc = O.cmd_files_upload_ocr(client, _args([src], manifest=str(manifest)))

    assert rc == 1
    assert client.session.calls == []
    assert "not in the manifest" in capsys.readouterr().err


def test_directory_input_is_walked_for_jsonl(client, tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    _jsonl(d, {"file_id": 70, "envelope": text_envelope()}, name="a.jsonl")
    _jsonl(d, {"file_id": 71, "envelope": layout_envelope()}, name="b.jsonl")
    client.session = RecordingSession(responses=[_applied(70, 71)])

    O.cmd_files_upload_ocr(client, _args([d]))

    sent = [i["file_id"] for i in client.session.calls[0]["json"]["items"]]
    assert sorted(sent) == [70, 71]


def test_limit_stops_after_n_files(client, tmp_path):
    records = [{"file_id": i, "envelope": text_envelope()} for i in range(10)]
    src = _jsonl(tmp_path, *records)
    client.session = RecordingSession(responses=[_applied(0, 1, 2)])

    O.cmd_files_upload_ocr(client, _args([src], limit=3))

    assert len(client.session.calls[0]["json"]["items"]) == 3


def test_error_log_records_one_json_line_per_failure(client, tmp_path):
    log = tmp_path / "errors.jsonl"
    src = _jsonl(tmp_path, {"file_id": 80, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_refused((80, "not_found"))])

    O.cmd_files_upload_ocr(client, _args([src], error_log=str(log)))

    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert rows[0]["file_id"] == 80
    assert rows[0]["error_code"] == "not_found"


# ── transport ────────────────────────────────────────────────────────────────


def test_429_is_retried_honouring_retry_after(client, tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(O.time, "sleep", lambda s: slept.append(s))

    throttled = FakeResponse(429, {"detail": "throttled"})
    throttled.headers = {"Retry-After": "7"}
    src = _jsonl(tmp_path, {"file_id": 90, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[throttled, _applied(90)])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 0
    assert 7.0 in slept
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 2


def test_413_names_the_fix_and_is_not_retried(client, tmp_path, capsys):
    """The body cap fires before parsing, so no per-item result explains it."""
    src = _jsonl(tmp_path, {"file_id": 91, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(413, {"detail": "too large"})])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 1
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 1
    assert "--max-bytes" in capsys.readouterr().err


def test_4xx_other_than_429_is_not_retried(client, tmp_path, capsys):
    src = _jsonl(tmp_path, {"file_id": 92, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(403, {"error": "nope"})])

    rc = O.cmd_files_upload_ocr(client, _args([src]))

    assert rc == 1
    assert len([c for c in client.session.calls if c["method"] == "POST"]) == 1
    assert "failed" in capsys.readouterr().err


# ── contract constants ───────────────────────────────────────────────────────


def test_server_bounds_match_the_documented_contract():
    """These are mirrored client-side; a 400 naming a different bound means drift."""
    assert O.MAX_FILES_PER_REQUEST == 50
    assert O.MAX_PAGES_PER_FILE == 1_000
    assert O.MAX_REQUEST_BYTES == 10 * 1024 * 1024
    assert O.DEFAULT_MAX_BYTES < O.MAX_REQUEST_BYTES     # headroom for the wrapper
    assert O.SCHEMAS == (O.SCHEMA_LAYOUT, O.SCHEMA_TEXT)


def test_pacing_default_is_documented():
    """README/SKILL.md/--help all state this number."""
    assert O.DEFAULT_PAGES_PER_HOUR == 250_000


# ── --dry-run must never mean "upload for real" ──────────────────────────────


def test_dry_run_flag_is_an_alias_for_validate_only(client, tmp_path):
    """The parser propagates --dry-run onto every subcommand.

    Without an explicit binding it would parse here and be ignored, so a caller
    who typed the flag every other command understands would get a real upload.
    """
    src = _jsonl(tmp_path, {"file_id": 100, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[FakeResponse(200, {"results": [
        {"file_id": 100, "applied": False, "warnings": []}]})])

    args = _args([src], validate_only=False)
    args.dry_run = True
    rc = O.cmd_files_upload_ocr(client, args)

    assert rc == 0
    assert client.session.calls[0]["json"]["validate_only"] is True


def test_dry_run_is_bound_on_the_parser_not_propagated_inert():
    from pcxa._parser import build_parser

    parsed = build_parser().parse_args(["files", "upload-ocr", "x.jsonl", "--dry-run"])
    assert getattr(parsed, "dry_run", False) is True
    # And the command reads it as validate_only — the binding above is the
    # contract; this asserts the flag actually reaches the namespace.
    assert parsed.files_command == "upload-ocr"


def test_unknown_error_code_is_labelled_not_silently_filed(client, tmp_path, capsys):
    """A code the server grew and we don't know must be visible, not absorbed."""
    src = _jsonl(tmp_path, {"file_id": 110, "envelope": text_envelope()})
    client.session = RecordingSession(responses=[_refused((110, "brand_new_code"))])

    O.cmd_files_upload_ocr(client, _args([src], format="table"))

    assert "unrecognised" in capsys.readouterr().out
