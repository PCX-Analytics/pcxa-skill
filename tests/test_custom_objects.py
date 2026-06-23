"""Custom-object (field-choice) commands, fuzzy resolver, and form-value validation.

The resolver logic is exercised directly via ``options=`` (no client needed);
command payload shapes use a recording fake client; the form-submission
validator is tested against a fake that serves form fields + options, including
the project→company scope fallback and graceful degradation on API errors.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa._resolve import (
    field_choice_ref,
    resolve_field_choice_option,
    validate_choice_field_values,
)
from pcxa.commands.activities import (
    _CUSTOM_FIELDS_KEY,
    _validate_activity_choice_values,
    cmd_activities_create,
)
from pcxa.commands.custom_objects import (
    _normalize_property_schema,
    cmd_co_options_bulk_create,
    cmd_co_options_create,
    cmd_custom_objects_create,
    cmd_custom_objects_extend,
    cmd_custom_objects_update,
)
from pcxa.commands.forms import _validate_choice_values


OPTS = [
    {"id": 10, "label": "Acme Corp", "is_active": True},
    {"id": 11, "label": "BuildCo", "is_active": True},
    {"id": 12, "label": "Concrete Supplies Inc", "is_active": True},
    {"id": 13, "label": "Retired Vendor", "is_active": False},
]


# ────────────────────────────────────────────────────────────────────────────
# resolve_field_choice_option
# ────────────────────────────────────────────────────────────────────────────

def test_resolve_exact_label_case_insensitive():
    opt, msg = resolve_field_choice_option(None, 1, "acme corp", options=OPTS)
    assert opt["id"] == 10
    assert "Exact match" in msg


def test_resolve_by_option_id():
    opt, _ = resolve_field_choice_option(None, 1, "11", options=OPTS)
    assert opt["id"] == 11


def test_resolve_single_substring_autoresolves():
    opt, _ = resolve_field_choice_option(None, 1, "concrete", options=OPTS)
    assert opt["id"] == 12


def test_resolve_multiple_substring_blocks_and_lists():
    opts = OPTS + [{"id": 14, "label": "Acme West", "is_active": True}]
    opt, msg = resolve_field_choice_option(None, 1, "acme", options=opts)
    assert opt is None
    assert "Multiple matches" in msg
    assert "10" in msg and "14" in msg


def test_resolve_fuzzy_typo_suggests_did_you_mean():
    opt, msg = resolve_field_choice_option(None, 1, "Acme Crp", options=OPTS)
    assert opt is None
    assert "Did you mean" in msg and "Acme Corp" in msg


def test_resolve_ignores_inactive_options():
    # exact text of an inactive option must NOT resolve
    opt, _ = resolve_field_choice_option(None, 1, "Retired Vendor", options=OPTS)
    assert opt is None


def test_resolve_empty_options():
    opt, msg = resolve_field_choice_option(None, 1, "anything", options=[])
    assert opt is None
    assert "no options" in msg


# ────────────────────────────────────────────────────────────────────────────
# field_choice_ref — how a form field binds to a custom object
# ────────────────────────────────────────────────────────────────────────────

def test_field_choice_ref_top_level_int():
    assert field_choice_ref({"id": 1, "field_choice": 7}) == 7


def test_field_choice_ref_nested_dict():
    assert field_choice_ref({"choice_set": {"id": 9}}) == 9


def test_field_choice_ref_numeric_string_in_options_blob():
    assert field_choice_ref({"options": {"field_choice": "5"}}) == 5


def test_field_choice_ref_plain_field_is_none():
    assert field_choice_ref({"id": 2, "field_type": "text",
                             "options": {"choices": ["A", "B"]}}) is None


def test_field_choice_ref_ignores_bool():
    assert field_choice_ref({"field_choice": True}) is None


# ────────────────────────────────────────────────────────────────────────────
# command payload shapes
# ────────────────────────────────────────────────────────────────────────────

class RecClient:
    """Records post/patch/delete with the project_scoped flag."""

    def __init__(self, resp=None):
        self.company_id = 3
        self.project_id = 4
        self.posts = []
        self.resp = resp if resp is not None else {"id": 99, "name": "X", "label": "X"}

    def post(self, path, json_data=None, project_scoped=True):
        self.posts.append({"path": path, "payload": json_data, "project_scoped": project_scoped})
        return self.resp

    def patch(self, path, json_data=None, project_scoped=True):
        self.posts.append({"path": path, "payload": json_data, "project_scoped": project_scoped})
        return self.resp


def _args(**kw):
    d = {"dry_run": False, "format": "json", "scope": "project",
         "name": None, "description": None, "schema": None, "extensible": None}
    d.update(kw)
    return SimpleNamespace(**d)


def test_create_project_scope_includes_project():
    c = RecClient()
    cmd_custom_objects_create(c, _args(name="Vendors"))
    call = c.posts[0]
    assert call["path"] == "field-choices/"
    assert call["project_scoped"] is True
    assert call["payload"]["name"] == "Vendors"
    assert call["payload"]["company"] == 3
    assert call["payload"]["project"] == 4


def test_create_company_scope_omits_project():
    c = RecClient()
    cmd_custom_objects_create(c, _args(name="Trades", scope="company"))
    call = c.posts[0]
    assert call["project_scoped"] is False
    assert "project" not in call["payload"]
    assert call["payload"]["company"] == 3


# property_schema is sent in the backend's canonical list shape (issue #5)

def test_create_normalizes_object_form_schema_to_list():
    c = RecClient()
    cmd_custom_objects_create(
        c, _args(name="Vendors", schema='{"properties":{"code":{"type":"text"}}}')
    )
    assert c.posts[0]["payload"]["property_schema"] == [{"name": "code", "type": "text"}]


def test_create_passes_list_form_schema_through():
    c = RecClient()
    cmd_custom_objects_create(
        c, _args(name="Vendors", schema='[{"name":"code","type":"text"}]')
    )
    assert c.posts[0]["payload"]["property_schema"] == [{"name": "code", "type": "text"}]


def test_update_clear_schema_sends_empty_list():
    c = RecClient()
    cmd_custom_objects_update(c, _args(object_id=5, schema=""))
    assert c.posts[0]["payload"]["property_schema"] == []


def test_update_normalizes_object_form_schema_to_list():
    c = RecClient()
    cmd_custom_objects_update(
        c, _args(object_id=5, schema='{"properties":{"tier":{"type":"text"}}}')
    )
    assert c.posts[0]["payload"]["property_schema"] == [{"name": "tier", "type": "text"}]


def test_normalize_property_schema_helper():
    assert _normalize_property_schema([{"name": "a", "type": "text"}]) == [
        {"name": "a", "type": "text"}
    ]
    assert _normalize_property_schema({"properties": {"a": {"type": "number"}}}) == [
        {"name": "a", "type": "number"}
    ]
    assert _normalize_property_schema({}) == []
    # missing inner type defaults to text
    assert _normalize_property_schema({"properties": {"a": {}}}) == [
        {"name": "a", "type": "text"}
    ]


def test_extend_uses_company_scope_and_project_payload():
    c = RecClient()
    cmd_custom_objects_extend(c, _args(object_id=5))
    call = c.posts[0]
    assert call["path"] == "field-choices/5/extend_to_project/"
    assert call["project_scoped"] is False
    assert call["payload"] == {"project": 4}


def test_option_create_payload():
    c = RecClient()
    cmd_co_options_create(c, _args(object_id=5, label="Acme", order=2,
                                   properties='{"code":"A1"}', active=None))
    call = c.posts[0]
    assert call["path"] == "field-choices/5/options/"
    assert call["payload"]["label"] == "Acme"
    assert call["payload"]["order"] == 2
    assert call["payload"]["properties"] == {"code": "A1"}


def test_option_bulk_create_sends_options_list(tmp_path):
    f = tmp_path / "o.json"
    f.write_text(json.dumps([{"label": "A"}, {"label": "B", "order": 2}]))
    c = RecClient({"success_count": 2})
    cmd_co_options_bulk_create(c, _args(object_id=5, file=str(f)))
    call = c.posts[0]
    assert call["path"] == "field-choices/5/options/bulk_create/"
    assert call["payload"] == {"options": [{"label": "A"}, {"label": "B", "order": 2}]}


def test_option_bulk_create_rejects_missing_label(tmp_path):
    f = tmp_path / "o.json"
    f.write_text(json.dumps([{"order": 1}]))
    c = RecClient()
    with pytest.raises(SystemExit):
        cmd_co_options_bulk_create(c, _args(object_id=5, file=str(f)))
    assert c.posts == []


# ────────────────────────────────────────────────────────────────────────────
# form-submission value validation
# ────────────────────────────────────────────────────────────────────────────

class FormClient:
    def __init__(self, fields, options, fail_project=False):
        self.company_id = 3
        self.project_id = 4
        self._fields = fields
        self._options = options
        self.fail_project = fail_project

    def get(self, path, params=None, project_scoped=True):
        return {"results": self._fields}

    def get_all_pages(self, path, params=None, max_pages=50, project_scoped=True):
        if self.fail_project and project_scoped:
            raise RuntimeError("404 at project scope")
        return self._options


VENDOR_FIELD = [{"id": 1, "label": "Vendor", "field_choice": 7}]
VENDOR_OPTS = [{"id": 10, "label": "Acme Corp", "is_active": True}]


def test_submission_validation_blocks_on_no_match():
    c = FormClient(VENDOR_FIELD, VENDOR_OPTS)
    with pytest.raises(SystemExit) as e:
        _validate_choice_values(c, 1, {"1": "Acmee Crp"}, SimpleNamespace(no_fuzzy=False))
    assert e.value.code == 1


def test_submission_validation_passes_on_match():
    c = FormClient(VENDOR_FIELD, VENDOR_OPTS)
    _validate_choice_values(c, 1, {"1": "Acme Corp"}, SimpleNamespace(no_fuzzy=False))


def test_submission_validation_skipped_with_no_fuzzy():
    c = FormClient(VENDOR_FIELD, [])
    _validate_choice_values(c, 1, {"1": "whatever"}, SimpleNamespace(no_fuzzy=True))


def test_submission_validation_ignores_non_choice_fields():
    c = FormClient([{"id": 1, "label": "Notes", "field_type": "text"}], [])
    _validate_choice_values(c, 1, {"1": "free text"}, SimpleNamespace(no_fuzzy=False))


def test_submission_validation_company_scope_fallback():
    c = FormClient(VENDOR_FIELD, VENDOR_OPTS, fail_project=True)
    # project-scope options 404; company scope resolves the match → no raise
    _validate_choice_values(c, 1, {"1": "Acme Corp"}, SimpleNamespace(no_fuzzy=False))


def test_submission_validation_degrades_when_fields_unreadable():
    class Boom:
        company_id = 3
        project_id = 4

        def get(self, *a, **k):
            raise RuntimeError("403 forbidden")

    # field load fails → warn + proceed, never block
    _validate_choice_values(Boom(), 1, {"1": "x"}, SimpleNamespace(no_fuzzy=False))


# ────────────────────────────────────────────────────────────────────────────
# shared validation core
# ────────────────────────────────────────────────────────────────────────────

def test_shared_core_flags_unreadable_object_as_note_not_problem():
    class Boom:
        def get_all_pages(self, *a, **k):
            raise RuntimeError("403")

    fields = [{"id": 1, "label": "Vendor", "field_choice": 7}]
    problems, notes = validate_choice_field_values(Boom(), fields, {"1": "x"})
    assert problems == []
    assert len(notes) == 1


def test_shared_core_ignores_values_for_unbound_fields():
    fields = [{"id": 1, "label": "Notes", "field_type": "text"}]
    problems, notes = validate_choice_field_values(None, fields, {"1": "free text"})
    assert problems == [] and notes == []


# ────────────────────────────────────────────────────────────────────────────
# activity custom-field validation (mirrors form submissions)
# ────────────────────────────────────────────────────────────────────────────

ACTIVITY_CF = [{"id": 3, "label": "Vendor", "field_choice": 7}]


def test_activity_validation_blocks_on_no_match():
    c = FormClient(ACTIVITY_CF, VENDOR_OPTS)
    with pytest.raises(SystemExit) as e:
        _validate_activity_choice_values(c, {"3": "Acmee Crp"}, SimpleNamespace(no_fuzzy=False))
    assert e.value.code == 1


def test_activity_validation_passes_on_match():
    c = FormClient(ACTIVITY_CF, VENDOR_OPTS)
    _validate_activity_choice_values(c, {"3": "Acme Corp"}, SimpleNamespace(no_fuzzy=False))


def test_activity_validation_skipped_with_no_fuzzy():
    c = FormClient(ACTIVITY_CF, [])
    _validate_activity_choice_values(c, {"3": "whatever"}, SimpleNamespace(no_fuzzy=True))


def test_activity_create_puts_custom_fields_under_key(capsys):
    c = RecClient()
    args = SimpleNamespace(
        dry_run=True, format="json", title="T", description=None, status=None,
        priority=None, due_date=None, planned_start=None, planned_finish=None,
        owner=None, assignees=None, type=None, parent=None, tags=None, wbs=None,
        custom_fields='{"3":"Acme Corp"}', no_fuzzy=False,
    )
    cmd_activities_create(c, args)
    out = capsys.readouterr().out
    assert _CUSTOM_FIELDS_KEY in out
    assert "Acme Corp" in out
    assert c.posts == []  # dry-run makes no write
