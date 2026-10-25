"""`pcxa photos | drawings | threads` — every command hits the backend route it claims.

Each case parses a real argv through ``register_parser`` and dispatches through
``SUB_HANDLERS`` / ``SUB_COMMAND_KEYS`` (including the third-level groups such as
``photos layers``), then asserts the exact method, path, scope and body.
"""

import argparse

import pytest

import pcxa.commands.files as files
from pcxa._api import APIClient
from pcxa.commands.media import COMPANY_ONLY, SUB_COMMAND_KEYS, SUB_HANDLERS, register_parser


class FakeClient:
    company_id = 3
    project_id = 4
    paginate_params = staticmethod(APIClient.paginate_params)

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply if reply is not None else {"id": 9, "results": []}

    def _record(self, method, path, body, project_scoped):
        self.calls.append((method, path, body, project_scoped))
        return self.reply

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._record("GET", path, dict(params or {}), project_scoped)

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("POST", path, json_data, project_scoped)

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("PATCH", path, json_data, project_scoped)

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("DELETE", path, json_data, project_scoped)


def run(argv, client=None):
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    args = p.parse_args(argv)
    client = client or FakeClient()
    SUB_HANDLERS[args.command][getattr(args, SUB_COMMAND_KEYS[args.command])](client, args)
    return client


CASES = [
    # photos
    (["photos", "list", "--search", "crane", "--category", "Safety", "--tags", "a,b",
      "--tags-mode", "all", "--date", "2026-03", "--archived", "false", "--sort=-uploaded_at",
      "--limit", "10", "--offset", "20"],
     ("GET", "photos/", {"page_size": 10, "page": 3, "search": "crane", "category": "Safety",
                         "tags": "a,b", "tags_mode": "all", "photo_date": "2026-03",
                         "is_archived": "false", "ordering": "-uploaded_at"})),
    (["photos", "get", "5"], ("GET", "photos/5/", {})),
    (["photos", "update", "5", "--description", "north wall", "--tags", "x, y", "--date", "",
      "--lat", "1.5", "--data", '{"altitude": 3}'],
     ("PATCH", "photos/5/", {"description": "north wall", "tags": ["x", "y"], "latitude": 1.5,
                             "photo_date": None, "altitude": 3})),
    (["photos", "delete", "5"], ("DELETE", "photos/5/", None)),
    (["photos", "layers", "list", "5"], ("GET", "photos/5/layers/", {})),
    (["photos", "layers", "create", "5", "--name", "QA", "--order", "2"],
     ("POST", "photos/5/layers/", {"name": "QA", "order": 2})),
    (["photos", "layers", "update", "5", "7", "--visible", "false"],
     ("PATCH", "photos/5/layers/7/", {"is_visible": False})),
    (["photos", "layers", "delete", "5", "7"], ("DELETE", "photos/5/layers/7/", None)),
    (["photos", "markups", "list", "5", "--layer", "7", "--shape", "rect"],
     ("GET", "photos/5/markups/", {"layer": 7, "shape_type": "rect"})),
    (["photos", "markups", "create", "5", "--layer", "7", "--shape", "pin",
      "--coords", '{"x": 1, "y": 2}', "--text", "crack"],
     ("POST", "photos/5/markups/", {"layer": 7, "shape_type": "pin",
                                    "coords": {"x": 1, "y": 2}, "text": "crack"})),
    (["photos", "markups", "update", "5", "8", "--properties", '{"color": "red"}'],
     ("PATCH", "photos/5/markups/8/", {"properties": {"color": "red"}})),
    (["photos", "markups", "delete", "5", "8"], ("DELETE", "photos/5/markups/8/", None)),
    # drawings
    (["drawings", "list", "--discipline", "A,S", "--type", "plan", "--plot-plan", "true"],
     ("GET", "drawings/", {"page_size": 25, "discipline": "A,S", "drawing_type": "plan",
                           "is_plot_plan": "true"})),
    (["drawings", "get", "11"], ("GET", "drawings/11/", {})),
    (["drawings", "update", "11", "--identifier", "A-101", "--type", "section",
      "--tags", "issued"],
     ("PATCH", "drawings/11/", {"identifier": "A-101", "drawing_type": "section",
                                "tags": ["issued"]})),
    (["drawings", "delete", "11"], ("DELETE", "drawings/11/", None)),
    (["drawings", "status", "11"], ("GET", "drawings/11/processing-status/", {})),
    (["drawings", "set-current", "11", "--revision", "40"],
     ("POST", "drawings/11/set_current_revision/", {"revision_id": 40})),
    (["drawings", "revisions", "list", "11"], ("GET", "drawings/11/revisions/", {})),
    (["drawings", "revisions", "get", "11", "40"], ("GET", "drawings/11/revisions/40/", {})),
    (["drawings", "sources", "list"], ("GET", "source-documents/", {"page_size": 25})),
    (["drawings", "sources", "get", "2"], ("GET", "source-documents/2/", {})),
    (["drawings", "sources", "create", "--file-id", "100", "101"],
     ("POST", "files/convert_to_drawings/", {"file_ids": [100, 101]})),
    (["drawings", "sources", "delete", "2"], ("DELETE", "source-documents/2/", None)),
    (["drawings", "sources", "status", "2"],
     ("GET", "source-documents/2/processing-status/", {})),
    (["drawings", "sources", "retry", "2"],
     ("POST", "source-documents/2/retry-processing/", {})),
    (["drawings", "sources", "drawings", "2"],
     ("GET", "source-documents/2/extracted_drawings/", {})),
    # threads — one per target, so every parent path is pinned
    (["threads", "list", "--target", "file", "70"],
     ("GET", "files/70/comments/", {"page_size": 50})),
    (["threads", "add", "-t", "photo", "5", "--content", "see crack", "--reply-to", "3",
      "--mention", "12", "13"],
     ("POST", "photos/5/comments/", {"content": "see crack", "parent_comment": 3,
                                     "mentioned_users": [12, 13]})),
    (["threads", "update", "-t", "submission", "80", "6", "-c", "edited"],
     ("PATCH", "form-submissions/80/comments/6/", {"content": "edited"})),
    (["threads", "delete", "-t", "drawing", "11", "6"],
     ("DELETE", "drawings/11/comments/6/", None)),
]


@pytest.mark.parametrize("argv,expected", CASES, ids=[" ".join(c[0][:3]) for c in CASES])
def test_command_hits_backend_route(argv, expected, capsys):
    client = run(argv)
    method, path, body = expected
    assert client.calls == [(method, path, body, True)]


def test_every_subcommand_is_covered():
    covered = {(c[0][0], c[0][1]) for c in CASES} | {("photos", "upload")}
    assert covered == {(g, s) for g, subs in SUB_HANDLERS.items() for s in subs}
    assert COMPANY_ONLY == set()


def test_dry_run_write_sends_nothing(capsys):
    client = run(["--dry-run", "photos", "markups", "create", "5", "--layer", "7",
                  "--shape", "rect", "--coords", "[1,2,3,4]"])
    assert client.calls == []
    assert "Would CREATE markup on photo 5" in capsys.readouterr().out


def test_dry_run_threads_delete_sends_nothing(capsys):
    client = run(["--dry-run", "threads", "delete", "-t", "file", "70", "6"])
    assert client.calls == []


@pytest.mark.parametrize("argv,flag", [
    (["photos", "markups", "create", "5", "--layer", "7", "--shape", "rect", "--coords", "{bad"],
     "--coords"),
    (["photos", "update", "5", "--data", "not json"], "--data"),
    (["drawings", "update", "11", "--data", "[1, 2]"], "--data"),
])
def test_bad_json_exits_cleanly(argv, flag, capsys):
    with pytest.raises(SystemExit) as exc:
        run(argv)
    assert exc.value.code == 1
    assert flag in capsys.readouterr().err


def test_update_with_no_fields_refuses():
    with pytest.raises(SystemExit):
        run(["photos", "update", "5"])


def test_nested_group_without_subcommand_prints_usage(capsys):
    with pytest.raises(SystemExit):
        run(["photos", "layers"])
    assert "photos layers {list, create, update, delete}" in capsys.readouterr().err


def test_target_is_required():
    with pytest.raises(SystemExit):
        run(["threads", "list", "70"])


# ── photos upload: real files._upload_via_presign against a recording client ──


class _Resp:
    def __init__(self, payload=None):
        self._payload = payload or {}
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class UploadClient(FakeClient):
    def __init__(self):
        super().__init__(reply={"id": 55, "tags": ["roof"]})
        self.requests = []

    def _url(self, path, project_scoped=True):
        return f"https://api/x/{path}"

    def _request(self, method, url, json=None, **kwargs):
        self.requests.append((method, url, json))
        if url.endswith("presign-upload/"):
            return _Resp({"upload_url": "https://r2/put", "storage_key": "photos/3/4/k.jpg",
                          "upload_type": "presigned_put"})
        return _Resp({"id": 55})


def test_photo_upload_presigns_puts_creates_then_patches(tmp_path, monkeypatch):
    puts = []
    monkeypatch.setattr(files.requests, "put",
                        lambda url, data=None, headers=None, timeout=None: puts.append(url) or _Resp())
    img = tmp_path / "roof.jpg"
    img.write_bytes(b"\xff\xd8" * 10)
    client = UploadClient()
    run(["photos", "upload", str(img), "--tags", "roof", "--description", "east"], client)

    (m1, u1, b1), (m2, u2, b2) = client.requests
    assert (m1, u1) == ("POST", "https://api/x/photos/presign-upload/")
    assert b1["filename"] == "roof.jpg" and b1["content_type"] == "image/jpeg"
    assert puts == ["https://r2/put"]
    assert (m2, u2) == ("POST", "https://api/x/photos/")
    assert b2["storage_key"] == "photos/3/4/k.jpg" and b2["original_filename"] == "roof.jpg"
    # PhotoSerializer.create does not pop tags; they must go in the follow-up PATCH.
    assert "tags" not in b2
    assert client.calls == [("PATCH", "photos/55/", {"description": "east", "tags": ["roof"]}, True)]


def test_photo_upload_without_metadata_skips_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(files.requests, "put", lambda *a, **k: _Resp())
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    client = UploadClient()
    run(["photos", "upload", str(img)], client)
    assert len(client.requests) == 2 and client.calls == []


def test_photo_upload_missing_file_exits(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(["photos", "upload", str(tmp_path / "nope.jpg")])
    assert "File not found" in capsys.readouterr().err
