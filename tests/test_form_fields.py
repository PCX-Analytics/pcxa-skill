"""Form-field create/update payload shapes — table fields, choice binding, display.

Covers the table-field gap fixed in this change: ``--table-schema`` /
``--min-rows`` / ``--max-rows`` map to the right keys (columns belong in
``table_schema``, NOT ``options``), ``--choice-id`` binds a field to a custom
object, and the read-side options summary renders columns/bindings. Also locks
in that ``field_choice_ref`` recognizes the live API's ``choice_id`` key.
"""

from types import SimpleNamespace

import pytest

from pcxa._resolve import field_choice_ref
from pcxa.commands.forms import (
    _field_options_summary,
    cmd_fields_create,
    cmd_fields_update,
)


class RecClient:
    """Records post/patch with the path + payload."""

    def __init__(self, resp=None):
        self.company_id = 3
        self.project_id = 4
        self.calls = []
        self.resp = resp if resp is not None else {"id": 99, "label": "X", "field_type": "table"}

    def post(self, path, json_data=None, project_scoped=True):
        self.calls.append(("POST", path, json_data))
        return self.resp

    def patch(self, path, json_data=None, project_scoped=True):
        self.calls.append(("PATCH", path, json_data))
        return self.resp


def _create_args(**kw):
    d = {
        "dry_run": False, "format": "json", "form_id": 1,
        "label": "F", "field_type": "text", "required": False, "order": None,
        "placeholder": None, "help_text": None, "options": None,
        "choice_id": None, "table_schema": None, "min_rows": None, "max_rows": None,
        "column_span": None, "section": None,
    }
    d.update(kw)
    return SimpleNamespace(**d)


def _update_args(**kw):
    d = {
        "dry_run": False, "format": "json", "form_id": 1, "field_id": 5,
        "label": None, "field_type": None, "required": None, "order": None,
        "placeholder": None, "help_text": None, "options": None,
        "choice_id": None, "table_schema": None, "min_rows": None, "max_rows": None,
        "column_span": None,
    }
    d.update(kw)
    return SimpleNamespace(**d)


# ── create: table field puts columns in table_schema (not options) ──

def test_create_table_field_uses_table_schema():
    c = RecClient()
    cmd_fields_create(c, _create_args(
        label="Line Items", field_type="table",
        table_schema='[{"name":"item","field_type":"text","label":"Item"}]',
        min_rows=1, max_rows=10,
    ))
    _, path, payload = c.calls[0]
    assert path == "forms/1/fields/"
    assert payload["field_type"] == "table"
    assert payload["table_schema"] == [{"name": "item", "field_type": "text", "label": "Item"}]
    assert payload["min_rows"] == 1
    assert payload["max_rows"] == 10
    assert "options" not in payload


def test_create_table_without_schema_warns(capsys):
    c = RecClient()
    cmd_fields_create(c, _create_args(label="T", field_type="table"))
    err = capsys.readouterr().err
    assert "--table-schema" in err
    # still creates (columns may be set in a follow-up update)
    assert c.calls and c.calls[0][1] == "forms/1/fields/"


def test_create_choice_field_binds_choice_id():
    c = RecClient()
    cmd_fields_create(c, _create_args(label="Vendor", field_type="choice", choice_id=21))
    _, _, payload = c.calls[0]
    assert payload["choice_id"] == 21


# ── update: table_schema / rows / choice_id round-trip ──

def test_update_table_schema_and_rows():
    c = RecClient()
    cmd_fields_update(c, _update_args(
        table_schema='[{"name":"qty","field_type":"number","label":"Qty"}]',
        min_rows=0, max_rows=5,
    ))
    method, path, payload = c.calls[0]
    assert method == "PATCH"
    assert path == "forms/1/fields/5/"
    assert payload["table_schema"][0]["name"] == "qty"
    assert payload["min_rows"] == 0 and payload["max_rows"] == 5


def test_update_no_fields_exits():
    c = RecClient()
    with pytest.raises(SystemExit):
        cmd_fields_update(c, _update_args())
    assert c.calls == []


# ── read-side display summary ──

def test_summary_table_lists_columns():
    f = {"field_type": "table", "table_schema": [
        {"name": "item", "label": "Item"}, {"name": "qty", "label": "Qty"}]}
    s = _field_options_summary(f)
    assert s.startswith("cols:")
    assert "item" in s and "qty" in s


def test_summary_choice_binding():
    assert _field_options_summary({"field_type": "choice", "choice_id": 21}) == "custom-object 21"


def test_summary_select_choices_dict_and_list():
    assert "Low" in _field_options_summary({"options": {"choices": ["Low", "High"]}})
    assert "Low" in _field_options_summary({"options": ["Low", "High"]})


def test_summary_plain_text_empty():
    assert _field_options_summary({"field_type": "text"}) == ""


# ── field_choice_ref recognizes the live API's choice_id key ──

def test_field_choice_ref_choice_id():
    assert field_choice_ref({"id": 1, "field_type": "choice", "choice_id": 21}) == 21


def test_field_choice_ref_null_choice_id_is_none():
    assert field_choice_ref({"id": 1, "field_type": "table", "choice_id": None}) is None
