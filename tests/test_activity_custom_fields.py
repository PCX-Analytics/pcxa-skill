"""Activity custom fields: the key the server reads, and an update that keeps the fields it does not name.

The server reconciles an activity's custom fields to exactly the dict under ``custom_field_data``: a key it does not
read (the CLI once sent ``custom_field_values``) sets nothing, and a partial dict clears every field it leaves out.
"""

from types import SimpleNamespace

from pcxa.commands.activities import cmd_activities_create, cmd_activities_update

CURRENT = {"11": "PCX-Analytics/pcxa#1", "12": {"id": 5, "label": "Bug"}, "13": "S"}


class _RecClient:
    company_id = 1
    project_id = 2

    def __init__(self):
        self.gets, self.posted, self.patched = [], None, None

    def get(self, path, **kw):
        self.gets.append(path)
        return {"id": 7, "title": "T", "custom_field_data": dict(CURRENT)}

    def post(self, path, json_data=None, **kw):
        self.posted = json_data
        return {"id": 7, **(json_data or {})}

    def patch(self, path, json_data=None, **kw):
        self.patched = json_data
        return {"id": 7, **(json_data or {})}


def _args(**kw):
    d = {"activity_id": 7, "title": None, "description": None, "outcome": None, "status": None, "priority": None,
         "percent": None, "due_date": None, "planned_start": None, "planned_finish": None, "actual_start": None,
         "actual_finish": None, "owner": None, "assignees": None, "type": None, "parent": None, "tags": None,
         "wbs": None, "custom_fields": None, "no_fuzzy": True, "dry_run": False, "format": "json"}
    d.update(kw)
    return SimpleNamespace(**d)


def test_create_sends_the_key_the_server_reads():
    c = _RecClient()
    cmd_activities_create(c, _args(title="T", custom_fields='{"13": "M"}'))
    assert c.posted["custom_field_data"] == {"13": "M"}


def test_update_keeps_every_custom_field_it_does_not_name():
    c = _RecClient()
    cmd_activities_update(c, _args(custom_fields='{"13": "M"}'))
    assert c.patched["custom_field_data"] == {**CURRENT, "13": "M"}


def test_update_clears_a_field_set_to_null():
    c = _RecClient()
    cmd_activities_update(c, _args(custom_fields='{"13": null}'))
    assert c.patched["custom_field_data"] == {**CURRENT, "13": None}


def test_update_without_custom_fields_sends_none_and_reads_nothing():
    c = _RecClient()
    cmd_activities_update(c, _args(status="completed"))
    assert "custom_field_data" not in c.patched and c.gets == []
