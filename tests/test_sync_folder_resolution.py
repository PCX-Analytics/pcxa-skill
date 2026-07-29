"""Folder resolution in ``files sync``: retry, adoption, fail-fast, resume.

Regression cover for PCX-Analytics/pcxa#1689 — a single ``POST folders/``
that crossed the client read timeout aborted a 389,678-file / 1.11 TB run
before a byte moved, and left nothing resumable because folder resolution
happens before the manifest is written.

The fake client models the part of the server that matters here: folders are
identified by (name, parent), so a create that "failed" client-side may still
be visible on the next lookup.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa._http import ConnectionError as PcxaConnectionError
from pcxa.commands import sync as sync_mod
from pcxa.commands.sync import (
    FOLDER_OP_RETRIES,
    _create_folder_with_retry,
    _resolve_or_create_folders,
)
from tests.conftest import FakeResponse


TIMED_OUT = PcxaConnectionError("The read operation timed out")


def _http_error(status):
    from pcxa._http import HTTPError
    return HTTPError(FakeResponse(status, {"detail": "nope"}))


def _duplicate_error():
    from pcxa._http import HTTPError
    return HTTPError(FakeResponse(
        400, {"name": ["A folder with this name already exists here."]}))


class FakeFolderClient:
    """Stand-in for APIClient covering the calls folder resolution makes.

    ``tree`` maps parent id (None = project root) -> {lowercase name: id}.
    ``post_effects`` / ``subfolder_effects`` are consumed FIFO; each entry is
    None (behave normally), an exception instance to raise, or a callable
    ``fn(client, payload)`` for effects that need to touch the tree first —
    e.g. "the server committed, then the read timed out".
    """

    def __init__(self, tree=None, post_effects=None, subfolder_effects=None,
                 timeout=None):
        self.tree = {k: dict(v) for k, v in (tree or {}).items()}
        self.timeout = timeout
        self.post_effects = list(post_effects or [])
        self.subfolder_effects = list(subfolder_effects or [])
        self.posts = []
        self.subfolder_calls = []
        self.root_listings = 0
        self._next_id = 1000

    # -- APIClient surface ------------------------------------------------
    def _url(self, path, project_scoped=True):
        return f"https://api.test/{path}"

    def _request(self, method, url, params=None, timeout=None, **kwargs):
        parent = int(url.rstrip("/").split("/")[-2])
        self.subfolder_calls.append({"parent": parent, "timeout": timeout})
        effect = self.subfolder_effects.pop(0) if self.subfolder_effects else None
        if effect is not None:
            raise effect
        children = self.tree.get(parent, {})
        return FakeResponse(200, {
            "results": [{"id": fid, "name": name} for name, fid in children.items()],
            "next": None,
        })

    def get(self, path, params=None, project_scoped=True, timeout=None):
        if path == "folders/folder_tree/":
            self.root_listings += 1
            return [{"id": fid, "name": name}
                    for name, fid in self.tree.get(None, {}).items()]
        if path.startswith("folders/") and path.count("/") == 2:
            # Pre-flight "does the target folder exist" probe.
            return {"id": int(path.split("/")[1]), "name": "target"}
        raise AssertionError(f"unexpected GET {path}")

    def get_all_pages(self, path, params=None, max_pages=50, project_scoped=True):
        raise AssertionError("folder_tree should have answered")

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        assert path == "folders/"
        self.posts.append({"payload": json_data, "timeout": timeout})
        effect = self.post_effects.pop(0) if self.post_effects else None
        if callable(effect):
            effect(self, json_data)
        elif effect is not None:
            raise effect
        return {"id": self._commit(json_data)}

    # -- server-side helper ----------------------------------------------
    def _commit(self, payload):
        """Create the folder the way the server would (idempotent on name)."""
        parent = payload.get("parent")
        name = payload["name"].lower()
        children = self.tree.setdefault(parent, {})
        if name not in children:
            self._next_id += 1
            children[name] = self._next_id
        return children[name]


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    """Retry backoff is real time — skip it so the suite stays fast."""
    monkeypatch.setattr(sync_mod.time, "sleep", lambda *_a, **_k: None)


# ────────────────────────────────────────────────────────────────────────────
# Item 1 — a slow folder create must not abort the run
# ────────────────────────────────────────────────────────────────────────────

def test_create_retries_after_read_timeout_and_run_continues():
    """The #1689 traceback: one POST times out, the run keeps going."""
    c = FakeFolderClient(tree={5: {}}, post_effects=[TIMED_OUT])

    result = _resolve_or_create_folders(c, ["drawings"], 5)

    assert len(c.posts) == 2, "expected the timed-out create to be retried"
    assert result["drawings"] == c.tree[5]["drawings"]


def test_create_adopts_the_folder_a_timed_out_post_actually_made():
    """A read timeout doesn't mean the write failed — don't make a duplicate.

    The first POST times out *after* the server committed. The retry must
    re-resolve by name, find it, and adopt it rather than POSTing again.
    """
    def commit_then_time_out(client, payload):
        client._commit(payload)  # server wrote it...
        raise TIMED_OUT          # ...then we stopped waiting for the response

    c = FakeFolderClient(tree={5: {}}, post_effects=[commit_then_time_out])

    result = _resolve_or_create_folders(c, ["drawings"], 5)

    assert result["drawings"] == c.tree[5]["drawings"]
    assert len(c.posts) == 1, "must not POST a second time once the folder exists"


def test_create_adopts_existing_folder_on_duplicate_name_response():
    """A 400 'already exists' is the same situation, reported differently."""
    def someone_else_won_the_race(client, payload):
        client._commit(payload)
        raise _duplicate_error()

    c = FakeFolderClient(tree={5: {}}, post_effects=[someone_else_won_the_race])

    result = _resolve_or_create_folders(c, ["drawings"], 5)

    assert result["drawings"] == c.tree[5]["drawings"]
    assert len(c.posts) == 1


def test_create_gives_up_after_the_retry_budget():
    c = FakeFolderClient(tree={5: {}},
                         post_effects=[TIMED_OUT] * FOLDER_OP_RETRIES)

    with pytest.raises(PcxaConnectionError):
        _resolve_or_create_folders(c, ["drawings"], 5)

    assert len(c.posts) == FOLDER_OP_RETRIES


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_create_fails_fast_on_non_transient_error(status):
    """Auth/permission/validation errors must not burn the retry budget."""
    from pcxa._http import HTTPError

    c = FakeFolderClient(tree={5: {}},
                         post_effects=[_http_error(status)] * FOLDER_OP_RETRIES)

    with pytest.raises(HTTPError):
        _resolve_or_create_folders(c, ["drawings"], 5)

    assert len(c.posts) == 1, f"{status} should not be retried"


def test_create_reraises_400_that_is_not_a_name_collision():
    """A 400 with no matching folder on re-lookup is a real validation error."""
    from pcxa._http import HTTPError

    c = FakeFolderClient(tree={5: {}}, post_effects=[_duplicate_error()])

    with pytest.raises(HTTPError):
        _resolve_or_create_folders(c, ["drawings"], 5)

    assert len(c.posts) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_create_retries_transient_http_statuses(status):
    c = FakeFolderClient(tree={5: {}}, post_effects=[_http_error(status)])

    result = _resolve_or_create_folders(c, ["drawings"], 5)

    assert len(c.posts) == 2
    assert result["drawings"] == c.tree[5]["drawings"]


def test_subfolder_listing_retries_transient_failure():
    """The lookup half of resolution gets the same treatment as the create."""
    c = FakeFolderClient(tree={5: {"drawings": 42}},
                         subfolder_effects=[TIMED_OUT])

    result = _resolve_or_create_folders(c, ["drawings"], 5)

    assert result["drawings"] == 42
    assert len(c.subfolder_calls) == 2
    assert c.posts == [], "folder already existed — nothing to create"


def test_create_helper_returns_adopted_id_without_posting_again():
    """Unit-level: relookup wins over a second create attempt."""
    c = FakeFolderClient(tree={5: {}}, post_effects=[TIMED_OUT])

    assert _create_folder_with_retry(c, "drawings", 5, lambda: 999) == 999
    assert len(c.posts) == 1


def test_a_failing_relookup_does_not_abort_the_create():
    """The re-lookup is an optimization; it must not become a new abort path."""
    c = FakeFolderClient(tree={5: {}}, post_effects=[TIMED_OUT])

    def relookup():
        raise TIMED_OUT

    folder_id = _create_folder_with_retry(c, "drawings", 5, relookup)

    assert folder_id == c.tree[5]["drawings"]
    assert len(c.posts) == 2


def test_folder_calls_use_the_long_timeout_not_the_default():
    c = FakeFolderClient(tree={5: {}})

    _resolve_or_create_folders(c, ["drawings"], 5)

    assert c.subfolder_calls[0]["timeout"] == sync_mod.FOLDER_OP_TIMEOUT
    assert c.posts[0]["timeout"] == sync_mod.FOLDER_OP_TIMEOUT


def test_run_timeout_raises_the_folder_ceiling_but_never_lowers_it():
    slow = FakeFolderClient(tree={5: {}}, timeout=600)
    _resolve_or_create_folders(slow, ["drawings"], 5)
    assert slow.posts[0]["timeout"] == 600

    fast = FakeFolderClient(tree={5: {}}, timeout=10)
    _resolve_or_create_folders(fast, ["drawings"], 5)
    assert fast.posts[0]["timeout"] == sync_mod.FOLDER_OP_TIMEOUT


def test_nested_dirs_create_parents_before_children():
    c = FakeFolderClient(tree={5: {}})

    result = _resolve_or_create_folders(c, ["a", "a/b"], 5)

    assert [p["payload"] for p in c.posts] == [
        {"name": "a", "parent": 5},
        {"name": "b", "parent": result["a"]},
    ]
    assert result["a/b"] == c.tree[result["a"]]["b"]


def test_project_root_uses_folder_tree_listing():
    c = FakeFolderClient(tree={None: {"existing": 7}})

    result = _resolve_or_create_folders(c, ["existing"], None)

    assert result["existing"] == 7
    assert c.root_listings == 1
    assert c.posts == []


# ────────────────────────────────────────────────────────────────────────────
# Item 3 (partial) — resolved folders are checkpointed and reused
# ────────────────────────────────────────────────────────────────────────────

def test_known_folders_skip_the_api_entirely():
    c = FakeFolderClient(tree={5: {}})

    result = _resolve_or_create_folders(
        c, ["drawings"], 5, known={"drawings": 314})

    assert result["drawings"] == 314
    assert c.posts == []
    assert c.subfolder_calls == []


def test_on_resolved_fires_once_per_newly_resolved_dir():
    c = FakeFolderClient(tree={5: {"a": 11}})
    seen = []

    _resolve_or_create_folders(
        c, ["a", "a/b"], 5,
        known={}, on_resolved=lambda rel, fid: seen.append((rel, fid)))

    assert [rel for rel, _ in seen] == ["a", "a/b"]
    assert seen[0][1] == 11


def test_sync_checkpoints_resolved_folders_when_resolution_blows_up(tmp_path,
                                                                    capsys):
    """A failure mid-resolution must leave a resumable manifest.

    Before this, folder resolution ran entirely before the manifest existed,
    so an abort here threw away the whole run's setup (#1689).
    """
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "b").mkdir(parents=True)
    (tmp_path / "src" / "a" / "one.txt").write_text("1")
    (tmp_path / "src" / "b" / "two.txt").write_text("2")
    manifest_path = tmp_path / "sync.json"

    # "a" resolves; every attempt at "b" times out.
    c = FakeFolderClient(
        tree={5: {}},
        post_effects=[None] + [TIMED_OUT] * FOLDER_OP_RETRIES,
    )

    with pytest.raises(SystemExit) as exc:
        sync_mod.cmd_files_sync(c, _sync_args(tmp_path / "src", manifest_path))
    assert exc.value.code == 1

    saved = json.loads(manifest_path.read_text())
    assert saved["folders"] == {"a": c.tree[5]["a"]}
    assert saved["output_folder_id"] == 5
    assert "re-run to continue" in capsys.readouterr().err


def test_sync_reuses_checkpointed_folders_on_the_next_run(tmp_path, no_uploads):
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "a" / "one.txt").write_text("1")
    manifest_path = tmp_path / "sync.json"
    manifest_path.write_text(json.dumps({
        "version": 1, "files": {}, "folders": {"a": 4242},
        "output_folder_id": 5,
    }))

    c = FakeFolderClient(tree={5: {}})
    sync_mod.cmd_files_sync(c, _sync_args(tmp_path / "src", manifest_path))

    assert c.posts == []
    assert c.subfolder_calls == []
    assert no_uploads.work_folder_ids == [4242]


def test_sync_ignores_checkpointed_folders_from_a_different_root(tmp_path,
                                                                 no_uploads):
    """Same relative dir under a different --folder is a different folder."""
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "a" / "one.txt").write_text("1")
    manifest_path = tmp_path / "sync.json"
    manifest_path.write_text(json.dumps({
        "version": 1, "files": {}, "folders": {"a": 4242},
        "output_folder_id": 99,
    }))

    c = FakeFolderClient(tree={5: {}})
    sync_mod.cmd_files_sync(c, _sync_args(tmp_path / "src", manifest_path))

    assert len(c.posts) == 1, "stale mapping must not be trusted"
    assert no_uploads.work_folder_ids == [c.tree[5]["a"]]


def _sync_args(input_dir, manifest_path, **overrides):
    defaults = {
        "input_dir": str(input_dir),
        "folder": 5,
        "manifest": str(manifest_path),
        "include": None,
        "exclude": None,
        "include_hidden": False,
        "tags": None,
        "format": "text",
        "dry_run": False,
        "concurrency": 4,
        "max_concurrency": 8,
        "min_concurrency": 1,
        "part_concurrency": 2,
        "no_auto_tune": True,
        "max_failures": 10,
        "batch_size": 10,
        "no_bulk_presign": True,
        "limit": 0,
        "trust_manifest": True,
        "multipart_threshold_mb": 50,
        "part_size_mb": 16,
        "error_log": None,
        "stats_interval": 0.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def no_uploads(monkeypatch):
    """Stub the upload phase and record which folder ids the work was bound to.

    These tests are about resolution; driving the real presign/PUT stack would
    add a lot of surface without testing anything folder-related.
    """
    recorder = SimpleNamespace(work_folder_ids=None)

    def fake_run_uploads(**kwargs):
        recorder.work_folder_ids = [e["folder_id"] for e in kwargs["work_items"]]

    monkeypatch.setattr(sync_mod, "_run_uploads", fake_run_uploads)
    return recorder
