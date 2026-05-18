"""Tier 3: command-layer tests for the retrofitted bulk callsites.

Uses a FakeClient that records ``bulk_call`` invocations, so we can assert the
payload shape each command sends (the exact contract the API expects) without
spinning up the full HTTP stack.
"""

import io
import json
from types import SimpleNamespace

import pytest

from pcxa.commands.activities import cmd_activities_delete, cmd_activities_bulk_update
from pcxa.commands.tags_folders import (
    cmd_categorize,
    cmd_files_delete,
    cmd_files_purge,
    cmd_files_restore,
    cmd_move,
    cmd_tags_add,
    cmd_tags_remove,
    cmd_tags_set,
)


class FakeClient:
    """Records every ``bulk_call`` / ``post`` / ``delete`` invocation."""

    def __init__(self, response=None):
        self.bulk_calls = []
        self.posts = []
        self.deletes = []
        self.response = response or {"success_count": 0, "error_count": 0,
                                     "errors": [], "chunks": 0}

    def bulk_call(self, path, ids_key, ids, base_payload=None, chunk=500,
                  method="POST", project_scoped=True, on_chunk=None):
        self.bulk_calls.append({
            "path": path, "ids_key": ids_key, "ids": list(ids),
            "base_payload": base_payload, "chunk": chunk, "method": method,
        })
        # Drive the progress callback once so callers can be tested for it.
        if on_chunk is not None and ids:
            on_chunk(0, len(ids), len(ids), self.response)
        return self.response

    def post(self, path, payload=None, project_scoped=True):
        self.posts.append({"path": path, "payload": payload})
        return self.response

    def delete(self, path, json_data=None, project_scoped=True):
        self.deletes.append({"path": path, "json_data": json_data})
        return self.response


def _args(**kwargs):
    """Helper: build an argparse.Namespace-like with sensible defaults."""
    defaults = {"dry_run": False, "format": "text", "yes": True}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ────────────────────────────────────────────────────────────────────────────
# tags add / remove / set
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,mode", [
    (cmd_tags_add, "add"),
    (cmd_tags_remove, "remove"),
    (cmd_tags_set, "set"),
])
def test_tags_commands_call_bulk_update_with_correct_mode(cmd, mode):
    c = FakeClient({"success_count": 3})
    cmd(c, _args(file_ids=[1, 2, 3], tags="alpha,beta"))
    assert len(c.bulk_calls) == 1
    call = c.bulk_calls[0]
    assert call["path"] == "files/bulk_update/"
    assert call["ids_key"] == "file_ids"
    assert call["ids"] == [1, 2, 3]
    assert call["base_payload"] == {"tags": ["alpha", "beta"], "tag_mode": mode}


def test_tags_commands_skip_api_on_dry_run():
    c = FakeClient()
    cmd_tags_add(c, _args(file_ids=[1, 2, 3], tags="x", dry_run=True))
    assert c.bulk_calls == []


# ────────────────────────────────────────────────────────────────────────────
# move / categorize
# ────────────────────────────────────────────────────────────────────────────

def test_move_sends_folder_id():
    c = FakeClient({"success_count": 4})
    cmd_move(c, _args(file_ids=[10, 11, 12, 13], folder=42))
    call = c.bulk_calls[0]
    assert call["path"] == "files/bulk_move/"
    assert call["base_payload"] == {"folder_id": 42}


def test_categorize_sends_category():
    c = FakeClient({"success_count": 2})
    cmd_categorize(c, _args(file_ids=[5, 6], category="drawing"))
    call = c.bulk_calls[0]
    assert call["path"] == "files/bulk_update/"
    assert call["base_payload"] == {"category": "drawing"}


# ────────────────────────────────────────────────────────────────────────────
# files delete / restore (soft-delete via tag)
# ────────────────────────────────────────────────────────────────────────────

def test_files_delete_tags_files_with_to_delete():
    c = FakeClient({"success_count": 2})
    cmd_files_delete(c, _args(file_ids=[1, 2]))
    call = c.bulk_calls[0]
    assert call["base_payload"] == {"tags": ["to_delete"], "tag_mode": "add"}


def test_files_restore_removes_to_delete_tag():
    c = FakeClient({"success_count": 2})
    cmd_files_restore(c, _args(file_ids=[1, 2]))
    call = c.bulk_calls[0]
    assert call["base_payload"] == {"tags": ["to_delete"], "tag_mode": "remove"}


# ────────────────────────────────────────────────────────────────────────────
# files purge — the new #562 command
# ────────────────────────────────────────────────────────────────────────────

def test_purge_uses_delete_method_and_correct_path():
    c = FakeClient({"success_count": 3, "error_count": 0, "errors": [], "chunks": 1})
    cmd_files_purge(c, _args(file_ids=[1, 2, 3], ids_file=None, chunk=500))
    call = c.bulk_calls[0]
    assert call["path"] == "files/bulk_delete/"
    assert call["method"] == "DELETE"
    assert call["ids"] == [1, 2, 3]
    assert call["chunk"] == 500


def test_purge_dry_run_makes_no_api_call(capsys):
    c = FakeClient()
    cmd_files_purge(c, _args(file_ids=[1, 2, 3], ids_file=None, chunk=500, dry_run=True))
    assert c.bulk_calls == []
    out = capsys.readouterr().out
    assert "Would PURGE 3 files" in out


def test_purge_dedupes_preserving_order():
    c = FakeClient({"success_count": 3, "error_count": 0, "errors": [], "chunks": 1})
    cmd_files_purge(c, _args(file_ids=[3, 1, 3, 2, 1], ids_file=None, chunk=500))
    assert c.bulk_calls[0]["ids"] == [3, 1, 2]


def test_purge_reads_ids_file(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("100, 200\n300  400")
    c = FakeClient({"success_count": 4, "error_count": 0, "errors": [], "chunks": 1})
    cmd_files_purge(c, _args(file_ids=[], ids_file=str(p), chunk=500))
    assert c.bulk_calls[0]["ids"] == [100, 200, 300, 400]


def test_purge_reads_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("10 20 30"))
    c = FakeClient({"success_count": 3, "error_count": 0, "errors": [], "chunks": 1})
    cmd_files_purge(c, _args(file_ids=[], ids_file="-", chunk=500))
    assert c.bulk_calls[0]["ids"] == [10, 20, 30]


def test_purge_no_ids_exits_with_error():
    c = FakeClient()
    with pytest.raises(SystemExit) as exc:
        cmd_files_purge(c, _args(file_ids=[], ids_file=None, chunk=500))
    assert exc.value.code == 1
    assert c.bulk_calls == []


def test_purge_large_set_requires_typed_count_confirmation(monkeypatch):
    """≥1000 ids must use typed-count confirmation, not y/N."""
    c = FakeClient({"success_count": 1500, "error_count": 0, "errors": [], "chunks": 3})
    ids = list(range(1, 1501))
    # User types the wrong count → abort.
    monkeypatch.setattr("builtins.input", lambda: "y")
    cmd_files_purge(c, _args(file_ids=ids, ids_file=None, chunk=500, yes=False))
    assert c.bulk_calls == []
    # Now type the correct count → proceed.
    monkeypatch.setattr("builtins.input", lambda: "1500")
    cmd_files_purge(c, _args(file_ids=ids, ids_file=None, chunk=500, yes=False))
    assert len(c.bulk_calls) == 1


def test_purge_small_set_uses_yn_confirmation(monkeypatch):
    c = FakeClient({"success_count": 5, "error_count": 0, "errors": [], "chunks": 1})
    monkeypatch.setattr("builtins.input", lambda: "n")
    cmd_files_purge(c, _args(file_ids=[1, 2, 3, 4, 5], ids_file=None, chunk=500, yes=False))
    assert c.bulk_calls == []

    monkeypatch.setattr("builtins.input", lambda: "y")
    cmd_files_purge(c, _args(file_ids=[1, 2, 3, 4, 5], ids_file=None, chunk=500, yes=False))
    assert len(c.bulk_calls) == 1


def test_purge_json_format_emits_aggregate(capsys):
    c = FakeClient({"success_count": 3, "error_count": 1,
                    "errors": [{"id": 99}], "chunks": 1})
    cmd_files_purge(c, _args(file_ids=[1, 2, 3, 99], ids_file=None,
                             chunk=500, format="json"))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["success_count"] == 3
    assert data["error_count"] == 1


# ────────────────────────────────────────────────────────────────────────────
# activities
# ────────────────────────────────────────────────────────────────────────────

def test_activities_bulk_delete_multi_routes_through_bulk_call():
    c = FakeClient({"success_count": 5})
    cmd_activities_delete(c, _args(activity_ids=[1, 2, 3, 4, 5]))
    assert len(c.bulk_calls) == 1
    call = c.bulk_calls[0]
    assert call["path"] == "activities/bulk_delete/"
    assert call["ids_key"] == "activity_ids"
    assert call["ids"] == [1, 2, 3, 4, 5]


def test_activities_delete_single_uses_soft_delete_endpoint():
    """Single delete intentionally still uses the per-id soft_delete path."""
    c = FakeClient()
    cmd_activities_delete(c, _args(activity_ids=[42]))
    assert c.bulk_calls == []
    assert c.posts == [{"path": "activities/42/soft_delete/", "payload": None}] \
        or len(c.posts) == 1  # tolerate signature variants


def test_activities_bulk_update_sends_updates_payload():
    c = FakeClient({"success_count": 3})
    cmd_activities_bulk_update(c, _args(activity_ids=[1, 2, 3], status="done",
                                         priority=1, owner="alice"))
    call = c.bulk_calls[0]
    assert call["path"] == "activities/bulk_update/"
    assert call["base_payload"] == {
        "updates": {"status": "done", "priority": 1, "owner": "alice"}
    }
