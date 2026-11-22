"""`files sync` pre-flight name match: full filenames only (#26).

The server derives a file's ``title`` from its stem, so an existing
``report.pdf`` has title ``report``. Matching a local file's stem against that
skipped ``report.docx`` forever: never uploaded, recorded in the manifest as
``already_in_target_folder``, counted only as a name match.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa.commands import sync as sync_mod
from tests.conftest import FakeResponse


class FakeNamesClient:
    """Target folder 5 already holds the files in ``existing`` (server rows)."""

    timeout = None

    def __init__(self, existing):
        self.existing = existing

    def _url(self, path, project_scoped=True):
        return f"https://api.test/{path}"

    def get(self, path, params=None, project_scoped=True, timeout=None):
        assert path == "folders/5/", path          # pre-flight probe only
        return {"id": 5, "name": "target"}

    def _request(self, method, url, params=None, timeout=None, **kwargs):
        assert url.endswith("/files/") and params["folder"] == 5, url
        return FakeResponse(200, {"results": self.existing, "next": None})


def _row(title, original_filename):
    return {"title": title,
            "current_version": {"file_metadata": {"original_filename": original_filename}}}


@pytest.fixture
def uploads(monkeypatch):
    seen = SimpleNamespace(names=None)

    def fake_run_uploads(**kwargs):
        seen.names = sorted(e["name"] for e in kwargs["work_items"])

    monkeypatch.setattr(sync_mod, "_run_uploads", fake_run_uploads)
    return seen


def _args(src, manifest=None):
    return SimpleNamespace(
        input_dir=str(src), folder=5, manifest=str(manifest) if manifest else None,
        include=None, exclude=None, include_hidden=False, tags=None, format="json",
        dry_run=False, concurrency=1, max_concurrency=1, min_concurrency=1,
        part_concurrency=1, no_auto_tune=True, max_failures=10, batch_size=10,
        no_bulk_presign=True, limit=0, trust_manifest=False,
        multipart_threshold_mb=50, part_size_mb=16, error_log=None, stats_interval=0.0,
    )


def _src(tmp_path, *names):
    src = tmp_path / "src"
    src.mkdir()
    for n in names:
        (src / n).write_bytes(b"x")
    return src


def test_a_different_format_with_the_same_stem_is_not_skipped(tmp_path, uploads, capsys):
    src = _src(tmp_path, "report.docx", "report.pdf")
    client = FakeNamesClient([_row("report", "report.pdf")])

    sync_mod.cmd_files_sync(client, _args(src))

    assert uploads.names == ["report.docx"], (
        "report.docx was skipped because report.pdf (title 'report') exists"
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped_name_match"] == 1          # the true duplicate, report.pdf


def test_a_file_renamed_in_the_ui_is_still_recognised_by_title_and_extension(tmp_path, uploads,
                                                                            capsys):
    src = _src(tmp_path, "Site Plan.pdf", "Site Plan.dwg")
    client = FakeNamesClient([_row("Site Plan", "scan_0042.pdf")])

    sync_mod.cmd_files_sync(client, _args(src))

    assert uploads.names == ["Site Plan.dwg"]


def test_a_stale_stem_match_in_the_manifest_is_re_checked(tmp_path, uploads, capsys):
    """Manifests written before the fix hide report.docx on every later run."""
    src = _src(tmp_path, "report.docx")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"version": 1, "output_folder_id": 5, "files": {
        "report.docx": {"size": 1, "name": "report.docx", "folder_id": 5,
                        "skipped_reason": "already_in_target_folder"},
    }}))
    client = FakeNamesClient([_row("report", "report.pdf")])

    sync_mod.cmd_files_sync(client, _args(src, manifest))

    assert uploads.names == ["report.docx"]
