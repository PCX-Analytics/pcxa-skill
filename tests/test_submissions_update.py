"""`submissions update --values` merge-vs-replace behavior.

Regression guard for the footgun where a partial ``--values`` silently wipes
every other field on a submission (the API replaces the ``values`` dict
wholesale on PATCH). ``--merge`` opts into a GET→merge→PATCH that only touches
the keys supplied; the default replace path warns on stderr.
"""

from types import SimpleNamespace

from pcxa._parser import build_parser
from pcxa.commands.forms import cmd_submissions_update


def _update_args(**kw):
    d = {
        "form_id": 1, "submission_id": 42, "values": None, "merge": False,
        "code": None, "owner": None, "assignees": None, "distribution": None,
        "private": None, "tags": None, "location_name": None,
        "dry_run": False, "format": "json", "no_fuzzy": True,
    }
    d.update(kw)
    return SimpleNamespace(**d)


class _RecClient:
    """Records the PATCH payload; returns a fixed submission on GET."""

    company_id = 1
    project_id = 2

    def __init__(self, current_values):
        self._current = {"id": 42, "values": current_values}
        self.patched = None

    def get(self, path, **kw):
        return self._current

    def patch(self, path, json_data=None, **kw):
        self.patched = {"path": path, "payload": json_data}
        return {"id": 42, "code": "NCR-0855", "status": "draft"}


def test_merge_preserves_untouched_fields():
    c = _RecClient({"1": "a", "2": "b", "3": "old"})
    cmd_submissions_update(c, _update_args(values='{"3": "new"}', merge=True))
    assert c.patched["payload"]["values"] == {"1": "a", "2": "b", "3": "new"}


def test_merge_on_empty_submission_just_sets_keys():
    c = _RecClient(None)  # API may return null/absent values
    cmd_submissions_update(c, _update_args(values='{"3": "new"}', merge=True))
    assert c.patched["payload"]["values"] == {"3": "new"}


def test_default_replace_clobbers_and_warns(capsys):
    c = _RecClient({"1": "a", "2": "b", "3": "old"})
    cmd_submissions_update(c, _update_args(values='{"3": "new"}', merge=False))
    # Replace path sends only the provided key — the documented footgun, now loud.
    assert c.patched["payload"]["values"] == {"3": "new"}
    err = capsys.readouterr().err
    assert "REPLACES" in err and "--merge" in err


def test_parser_wires_merge_and_patch_alias():
    parser = build_parser()
    a = parser.parse_args(["submissions", "update", "1", "42", "--values", "{}", "--merge"])
    assert a.merge is True
    b = parser.parse_args(["submissions", "update", "1", "42", "--values", "{}", "--patch"])
    assert b.merge is True
    c = parser.parse_args(["submissions", "update", "1", "42", "--values", "{}"])
    assert c.merge is False
