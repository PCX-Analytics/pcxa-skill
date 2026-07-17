"""Form-field create/update payload shapes — table fields, choice binding, display.

Covers the table-field gap fixed in this change: ``--table-schema`` /
``--min-rows`` / ``--max-rows`` map to the right keys (columns belong in
``table_schema``, NOT ``options``), ``--choice-id`` binds a field to a custom
object, and the read-side options summary renders columns/bindings. Also locks
in that ``field_choice_ref`` recognizes the live API's ``choice_id`` key.
"""

import json
from types import SimpleNamespace

import pytest

from pcxa._resolve import field_choice_ref
from pcxa.commands.forms import (
    _field_options_summary,
    cmd_fields_create,
    cmd_fields_list,
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


# --- fields list pagination (issue #1173) ---------------------------------


class PageClient:
    """Mock APIClient for a page-number-paginated fields endpoint with `total`
    fields; records each call so tests can assert what was fetched."""

    def __init__(self, total=49, page_size=25):
        self.company_id = 3
        self.project_id = 4
        self.total = total
        self.default_page_size = page_size
        self.calls = []
        self._fields = [
            {"id": i, "order": i, "label": f"F{i}", "field_type": "text",
             "is_required": False}
            for i in range(1, total + 1)
        ]

    @staticmethod
    def paginate_params(limit, offset=0):
        params = {"page_size": limit}
        if offset > 0:
            params["page"] = (offset // limit) + 1
        return params

    def get(self, path, params=None, project_scoped=True):
        self.calls.append(("GET", path, params))
        params = params or {}
        page_size = int(params.get("page_size", self.default_page_size))
        page = int(params.get("page", 1))
        start = (page - 1) * page_size
        page_results = self._fields[start:start + page_size]
        next_url = None
        if start + page_size < self.total:
            next_url = f"https://api.example/fields/?page={page + 1}"
        return {"count": self.total, "next": next_url, "previous": None,
                "results": page_results}

    def get_all_pages(self, path, params=None, max_pages=50, project_scoped=True):
        self.calls.append(("GET_ALL", path, params))
        return list(self._fields)


def _list_args(**kw):
    d = {"format": "table", "form_id": 15, "limit": 25, "offset": 0, "all": False}
    d.update(kw)
    return SimpleNamespace(**d)


def test_fields_list_warns_on_truncation(capsys):
    c = PageClient(total=49)
    cmd_fields_list(c, _list_args())
    out = capsys.readouterr()
    assert "of 49" in out.out                       # header shows the total
    assert "showing 25 of 49" in out.err            # stderr truncation notice
    assert "--all" in out.err
    assert c.calls == [("GET", "forms/15/fields/", {"page_size": 25})]  # page 1 only


def test_fields_list_no_warning_when_complete(capsys):
    c = PageClient(total=10)
    cmd_fields_list(c, _list_args())
    out = capsys.readouterr()
    assert out.err == ""                            # nothing was truncated
    assert "Fields for form 15: 10" in out.out


def test_fields_list_all_fetches_every_field(capsys):
    c = PageClient(total=49)
    cmd_fields_list(c, _list_args(all=True))
    out = capsys.readouterr()
    assert out.err == ""                            # --all => never truncated
    assert c.calls == [("GET_ALL", "forms/15/fields/", None)]  # auto-paginated
    assert "Fields for form 15: 49" in out.out


def test_fields_list_offset_limit_pages(capsys):
    c = PageClient(total=49)
    cmd_fields_list(c, _list_args(offset=25, limit=25))
    out = capsys.readouterr()
    assert c.calls == [("GET", "forms/15/fields/", {"page_size": 25, "page": 2})]
    assert out.err == ""                            # page 2 completes the set


def test_fields_list_json_all_returns_full_set(capsys):
    c = PageClient(total=30)
    cmd_fields_list(c, _list_args(format="json", all=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 30
    assert len(payload["results"]) == 30
