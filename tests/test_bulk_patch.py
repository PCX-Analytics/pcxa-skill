"""Tests for `files bulk-patch` and `tags bulk`, which route through the
server-side POST .../files/bulk_patch/ endpoint (pmapp2 PR #1283 / issue #1265).

Unlike files/bulk_update/ (one tag set applied to every id), each plan row
carries its own values, so different files can get different tags/metadata in
a single request.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa.commands.tags_folders import (
    cmd_files_bulk_patch,
    cmd_tags_bulk,
    MAX_BULK_PATCH,
)
from tests.conftest import FakeResponse

BULK_PATCH_URL = "https://api.example.com/api/companies/1/projects/2/files/bulk_patch/"

OK_RESP = {
    "success_count": 0,
    "error_count": 0,
    "patched_file_ids": [],
    "modified_file_ids": [],
    "errors": [],
}


def _args(**kwargs):
    defaults = {"dry_run": False, "format": "text", "file": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _write_plan(tmp_path, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return str(p)


# ── files bulk-patch ────────────────────────────────────────────────────────

def test_bulk_patch_bare_array_posts_changes(client, tmp_path):
    plan = [
        {"file_id": 123, "tags": ["urgent"], "tag_mode": "add"},
        {"file_id": 124, "title": "ACME-0001.pdf", "category": "Contracts"},
    ]
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 2,
                                                "patched_file_ids": [123, 124]})
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))

    assert len(client.session.calls) == 1
    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == BULK_PATCH_URL
    assert call["timeout"] == 180
    assert call["json"] == {
        "changes": [
            {"file_id": 123, "tags": ["urgent"], "tag_mode": "add"},
            {"file_id": 124, "title": "ACME-0001.pdf", "category": "Contracts"},
        ]
    }


def test_bulk_patch_accepts_changes_wrapper(client, tmp_path):
    plan = {"changes": [{"file_id": 1, "tags": ["a"]}]}
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 1})
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls[0]["json"] == {
        "changes": [{"file_id": 1, "tags": ["a"], "tag_mode": "set"}]
    }


def test_bulk_patch_reports_modified_and_server_errors(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"]}, {"file_id": 999, "tags": ["b"]}]
    client.session.default = FakeResponse(200, {
        "success_count": 1,
        "error_count": 1,
        "patched_file_ids": [1],
        "modified_file_ids": [1],
        "errors": [{"file_id": 999, "error": "File with id 999 not found in this project"}],
    })
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))
    out = capsys.readouterr().out
    assert "1 patched, 1 modified, 1 failed" in out
    assert "file 999: File with id 999 not found in this project" in out


def test_bulk_patch_json_format_emits_aggregate(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"]}]
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 1,
                                                "patched_file_ids": [1],
                                                "modified_file_ids": [1]})
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan), format="json"))
    data = json.loads(capsys.readouterr().out)
    assert data["success_count"] == 1
    assert data["modified_file_ids"] == [1]
    assert data["skipped"] == []


def test_bulk_patch_chunks_at_max(client, tmp_path):
    plan = [{"file_id": i, "tags": ["t"]} for i in range(1, MAX_BULK_PATCH + 11)]
    client.session.default = FakeResponse(200, OK_RESP)
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))

    assert len(client.session.calls) == 2
    assert len(client.session.calls[0]["json"]["changes"]) == MAX_BULK_PATCH
    assert len(client.session.calls[1]["json"]["changes"]) == 10


def test_bulk_patch_dry_run_makes_no_request(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"]}]
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan), dry_run=True))
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "Dry run: 1 files would be patched" in out
    assert "tags set=['a']" in out


def test_bulk_patch_invalid_rows_isolated(client, tmp_path, capsys):
    plan = [
        {"file_id": 1, "tags": ["a"]},          # valid
        {"tags": ["b"]},                         # missing file_id
        {"file_id": 3},                          # sets nothing
        {"file_id": 4, "tags": [], "tag_mode": "set"},  # empty set-wipe
    ]
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 1,
                                                "patched_file_ids": [1],
                                                "modified_file_ids": [1]})
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))
    # Only the one valid row is sent.
    assert client.session.calls[0]["json"] == {
        "changes": [{"file_id": 1, "tags": ["a"], "tag_mode": "set"}]
    }
    out = capsys.readouterr().out
    assert "missing or non-integer 'file_id'" in out
    assert "row sets nothing" in out
    assert "refusing to clear all tags" in out


def test_bulk_patch_duplicate_file_id_flagged(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"]}, {"file_id": 1, "tags": ["b"]}]
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 1})
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))
    # Second (duplicate) row is dropped before the request.
    assert len(client.session.calls[0]["json"]["changes"]) == 1
    assert "duplicate file_id 1" in capsys.readouterr().out


def test_bulk_patch_all_invalid_makes_no_request(client, tmp_path, capsys):
    plan = [{"file_id": 1}]  # sets nothing
    cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "0 patched" in out
    assert "row sets nothing" in out


def test_bulk_patch_missing_file_exits(client, tmp_path):
    with pytest.raises(SystemExit):
        cmd_files_bulk_patch(client, _args(file=str(tmp_path / "nope.json")))


def test_bulk_patch_empty_list_exits(client, tmp_path):
    with pytest.raises(SystemExit):
        cmd_files_bulk_patch(client, _args(file=_write_plan(tmp_path, [])))


# ── tags bulk (tag-only view) ───────────────────────────────────────────────

def test_tags_bulk_posts_tag_only_changes(client, tmp_path):
    plan = [
        {"file_id": 1, "tags": ["reviewed", "urgent"], "tag_mode": "add"},
        {"file_id": 2, "tags": ["legal"]},
    ]
    client.session.default = FakeResponse(200, {**OK_RESP, "success_count": 2})
    cmd_tags_bulk(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls[0]["url"] == BULK_PATCH_URL
    assert client.session.calls[0]["json"] == {
        "changes": [
            {"file_id": 1, "tags": ["reviewed", "urgent"], "tag_mode": "add"},
            {"file_id": 2, "tags": ["legal"], "tag_mode": "set"},
        ]
    }


def test_tags_bulk_rejects_scalar_fields(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"], "title": "no.pdf"}]
    cmd_tags_bulk(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls == []
    out = capsys.readouterr().out
    assert "only sets tags" in out
    assert "files bulk-patch" in out


def test_tags_bulk_requires_tags_each_row(client, tmp_path, capsys):
    plan = [{"file_id": 1}]
    cmd_tags_bulk(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls == []
    assert "requires a 'tags' list" in capsys.readouterr().out


def test_tags_bulk_invalid_tag_mode_flagged(client, tmp_path, capsys):
    plan = [{"file_id": 1, "tags": ["a"], "tag_mode": "replace"}]
    cmd_tags_bulk(client, _args(file=_write_plan(tmp_path, plan)))
    assert client.session.calls == []
    assert "invalid tag_mode 'replace'" in capsys.readouterr().out
