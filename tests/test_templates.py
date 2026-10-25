"""`pcxa activity-types|type-steps|type-fields|type-extensions|type-extension-fields|
field-definitions|field-sections|field-rules` and `resolve_field_labels`.

Each command is parsed from a real argv and dispatched through the module's
SUB_HANDLERS, so a wrong path, a wrong scope or a payload key the server does
not read fails here. The label resolver is the piece that writes values into
an activity: a label that silently matches nothing (or the wrong field), or a
"5" stored as a string in a number field, would corrupt data without an error.
"""

import argparse
import json

import pytest

from pcxa._api import APIClient
from pcxa.commands import templates
from pcxa.commands.templates import (
    COMPANY_ONLY,
    SUB_COMMAND_KEYS,
    SUB_HANDLERS,
    register_parser,
    resolve_field_labels,
)


class FakeClient:
    """Records every call; serves canned bodies by path."""

    company_id = 3
    project_id = 7
    paginate_params = staticmethod(APIClient.paginate_params)

    def __init__(self, bodies=None):
        self.bodies = bodies or {}
        self.calls = []

    def _rec(self, method, path, body=None, params=None, project_scoped=True):
        self.calls.append({"method": method, "path": path, "body": body,
                           "params": params, "project_scoped": project_scoped})
        return self.bodies.get(path, {"id": 1, "name": "x"})

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._rec("GET", path, params=params, project_scoped=project_scoped)

    def get_all_pages(self, path, params=None, max_pages=50, project_scoped=True):
        self.calls.append({"method": "GET*", "path": path, "body": None,
                           "params": params, "project_scoped": project_scoped})
        return self.bodies.get(path, [])

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._rec("POST", path, body=json_data, project_scoped=project_scoped)

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._rec("PATCH", path, body=json_data, project_scoped=project_scoped)

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._rec("DELETE", path, project_scoped=project_scoped)


def _parser():
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    return p


def run(argv, client=None):
    client = client or FakeClient()
    args = _parser().parse_args(argv)
    sub = getattr(args, SUB_COMMAND_KEYS[args.command])
    SUB_HANDLERS[args.command][sub](client, args)
    return client


def only(client):
    assert len(client.calls) == 1, client.calls
    c = client.calls[0]
    return c["method"], c["path"], c["project_scoped"], c["body"] if c["body"] is not None else c["params"]


# ── activity-types ───────────────────────────────────────────────────────────


def test_activity_types_list_project_default_with_filters(capsys):
    c = run(["activity-types", "list", "--search", "pour", "--category", "Civil", "--limit", "10"])
    assert only(c) == ("GET", "activity-types/", True,
                       {"page_size": 10, "search": "pour", "category": "Civil"})


def test_activity_types_list_company_scope_table(capsys):
    c = FakeClient({"activity-types/": {"count": 1, "results": [
        {"id": 5, "name": "Pour", "project": None, "template_steps": [{}, {}], "is_default": True}]}})
    run(["--format", "table", "activity-types", "list", "--scope", "company"], c)
    assert c.calls[0]["project_scoped"] is False
    out = capsys.readouterr().out
    assert "Pour" in out and "company" in out


def test_activity_types_get(capsys):
    assert only(run(["activity-types", "get", "5", "--scope", "company"]))[:3] == (
        "GET", "activity-types/5/", False)


def test_activity_types_create_sends_typed_flags_and_data_last(capsys):
    c = run(["activity-types", "create", "--name", "Pour", "--category", "Civil",
             "--default", "true", "--data", '{"description": "d", "name": "Pour 2"}',
             "--scope", "company"])
    assert only(c) == ("POST", "activity-types/", False,
                       {"name": "Pour 2", "category": "Civil", "is_default": True, "description": "d"})


def test_activity_types_update_and_empty_update_refused(capsys):
    c = run(["activity-types", "update", "5", "--category", ""])
    assert only(c) == ("PATCH", "activity-types/5/", True, {"category": None})
    with pytest.raises(SystemExit) as exc:
        run(["activity-types", "update", "5"])
    assert exc.value.code == 1


def test_activity_types_delete_and_dry_run(capsys):
    assert only(run(["activity-types", "delete", "5"]))[:3] == ("DELETE", "activity-types/5/", True)
    c = run(["--dry-run", "activity-types", "delete", "5"])
    assert c.calls == [] and "Would DELETE" in capsys.readouterr().out


def test_activity_types_actions(capsys):
    assert only(run(["activity-types", "categories"]))[:2] == ("GET", "activity-types/categories/")
    assert only(run(["activity-types", "schema", "5"]))[:2] == ("GET", "activity-types/5/schema/")
    assert only(run(["activity-types", "default"])) == (
        "GET", "activity-types/get_default_for_company/", False, {"company": 3})


def test_bad_json_in_data_exits_cleanly_before_any_request(capsys):
    c = FakeClient()
    with pytest.raises(SystemExit) as exc:
        run(["activity-types", "create", "--name", "x", "--data", "{nope"], c)
    assert exc.value.code == 1
    assert "Invalid JSON for --data" in capsys.readouterr().err
    assert c.calls == []


def test_data_that_is_not_an_object_is_refused(capsys):
    with pytest.raises(SystemExit):
        run(["activity-types", "create", "--name", "x", "--data", "[1]"])
    assert "JSON object" in capsys.readouterr().err


# ── type-steps ───────────────────────────────────────────────────────────────


def test_type_steps_list_reads_every_page(capsys):
    assert only(run(["type-steps", "list", "5", "--scope", "company"]))[:3] == (
        "GET*", "activity-types/5/steps/", False)


def test_type_steps_create_names_its_activity_type(capsys):
    c = run(["type-steps", "create", "5", "--name", "Formwork", "--weight", "25", "--order", "2",
             "--form", "9"])
    assert only(c) == ("POST", "activity-types/5/steps/", True, {
        "name": "Formwork", "progress_weight": 25.0, "order": 2,
        "required_form_template": 9, "activity_type": 5})


def test_type_steps_update_delete(capsys):
    assert only(run(["type-steps", "update", "5", "11", "--form", "0"])) == (
        "PATCH", "activity-types/5/steps/11/", True, {"required_form_template": None})
    assert only(run(["type-steps", "delete", "5", "11"]))[:2] == (
        "DELETE", "activity-types/5/steps/11/")


def test_type_steps_reorder_is_one_based_like_the_web_ui(capsys):
    c = run(["type-steps", "reorder", "5", "--order", "12,10,11"])
    assert only(c) == ("POST", "activity-types/5/steps/reorder/", True, {"step_orders": [
        {"id": 12, "order": 1}, {"id": 10, "order": 2}, {"id": 11, "order": 3}]})


def test_type_steps_weights(capsys):
    c = run(["type-steps", "weights", "5", "--set", "12=30,13=70"])
    assert only(c) == ("POST", "activity-types/5/adjust_template_weights/", True, {
        "step_weights": [{"step_id": 12, "weight": 30.0}, {"step_id": 13, "weight": 70.0}]})
    with pytest.raises(SystemExit):
        run(["type-steps", "weights", "5", "--set", "12:30"])


# ── type-fields / type-extension-fields ──────────────────────────────────────


def test_type_fields_list_get(capsys):
    assert only(run(["type-fields", "list", "5"]))[:3] == ("GET*", "activity-types/5/fields/", True)
    assert only(run(["type-fields", "get", "5", "40", "--scope", "company"]))[:3] == (
        "GET", "activity-types/5/fields/40/", False)


def test_type_fields_create_builds_dropdown_options(capsys):
    c = run(["type-fields", "create", "5", "--label", "Size", "--type", "select",
             "--options", "S, M,L", "--required", "true", "--order", "3", "--section-id", "8",
             "--help-text", "T-shirt", "--scope", "company"])
    assert only(c) == ("POST", "activity-types/5/fields/", False, {
        "label": "Size", "field_type": "select", "is_required": True,
        "options": {"dropdown": [{"value": "S", "label": "S"}, {"value": "M", "label": "M"},
                                 {"value": "L", "label": "L"}]},
        "order": 3, "help_text": "T-shirt", "section_id": 8})


def test_select_without_options_and_choice_without_list_are_refused(capsys):
    c = FakeClient()
    with pytest.raises(SystemExit):
        run(["type-fields", "create", "5", "--label", "Size", "--type", "select"], c)
    with pytest.raises(SystemExit):
        run(["type-fields", "create", "5", "--label", "Area", "--type", "choice"], c)
    assert c.calls == []
    c = run(["type-fields", "create", "5", "--label", "Area", "--type", "choice", "--choice-id", "4"])
    assert only(c)[3]["choice_id"] == 4


def test_type_fields_update_merges_options_into_the_current_ones(capsys):
    c = FakeClient({"activity-types/5/fields/40/": {
        "id": 40, "options": {"dropdown": [{"value": "A", "label": "A"}], "placeholder": "p"}}})
    run(["type-fields", "update", "5", "40", "--options", "B,C"], c)
    get, patch = c.calls
    assert get["method"] == "GET" and patch["method"] == "PATCH"
    assert patch["body"]["options"] == {
        "placeholder": "p", "dropdown": [{"value": "B", "label": "B"}, {"value": "C", "label": "C"}]}


def test_type_fields_delete_reorder_dry_run(capsys):
    assert only(run(["type-fields", "delete", "5", "40"]))[:2] == (
        "DELETE", "activity-types/5/fields/40/")
    assert only(run(["type-fields", "reorder", "5", "--order", "41,40"])) == (
        "POST", "activity-types/5/fields/reorder/", True, {"field_ids": [41, 40]})
    c = run(["--dry-run", "type-fields", "create", "5", "--label", "N", "--type", "number"])
    assert c.calls == [] and "Would CREATE field" in capsys.readouterr().out


def test_extension_fields_use_the_extension_path_and_target_section(capsys):
    c = run(["type-extension-fields", "create", "9", "--label", "Crew", "--type", "text",
             "--section-id", "8"])
    assert only(c) == ("POST", "activity-type-extensions/9/fields/", True, {
        "label": "Crew", "field_type": "text", "target_section_id": 8})
    assert only(run(["type-extension-fields", "list", "9"]))[:2] == (
        "GET*", "activity-type-extensions/9/fields/")
    assert only(run(["type-extension-fields", "reorder", "9", "--order", "2,1"]))[:2] == (
        "POST", "activity-type-extensions/9/fields/reorder/")


# ── type-extensions ──────────────────────────────────────────────────────────


def test_type_extensions(capsys):
    assert only(run(["type-extensions", "list"]))[:3] == ("GET*", "activity-type-extensions/", True)
    assert only(run(["type-extensions", "get", "9"]))[:2] == ("GET", "activity-type-extensions/9/")
    assert only(run(["type-extensions", "create", "--base-type", "5"])) == (
        "POST", "activity-type-extensions/", True, {"project": 7, "base_type": 5})
    assert only(run(["type-extensions", "delete", "9"]))[:2] == (
        "DELETE", "activity-type-extensions/9/")


# ── field-definitions / sections / rules ─────────────────────────────────────


def test_field_definitions(capsys):
    c = run(["field-definitions", "list", "--type", "number", "--section", "8", "--lean",
             "--scope", "company"])
    assert only(c) == ("GET", "field-definitions/", False,
                       {"page_size": 100, "field_type": "number", "section": 8, "lean": "true"})
    assert only(run(["field-definitions", "get", "40"]))[:2] == ("GET", "field-definitions/40/")
    c = run(["field-definitions", "create", "--label", "Area", "--type", "choice",
             "--choice-id", "4", "--section-id", "8"])
    assert only(c) == ("POST", "field-definitions/", True, {
        "label": "Area", "field_type": "choice", "choice_set_id": 4, "section_id": 8})
    assert only(run(["field-definitions", "update", "40", "--required", "false"])) == (
        "PATCH", "field-definitions/40/", True, {"is_required": False})
    assert only(run(["field-definitions", "delete", "40"]))[:2] == ("DELETE", "field-definitions/40/")


def test_field_sections(capsys):
    assert only(run(["field-sections", "list", "--scope", "company"]))[:3] == (
        "GET", "field-sections/", False)
    assert only(run(["field-sections", "get", "8"]))[:2] == ("GET", "field-sections/8/")
    assert only(run(["field-sections", "create", "--name", "QA", "--order", "1"])) == (
        "POST", "field-sections/", True, {"name": "QA", "order": 1})
    assert only(run(["field-sections", "update", "8", "--description", "d"])) == (
        "PATCH", "field-sections/8/", True, {"description": "d"})
    assert only(run(["field-sections", "delete", "8"]))[:2] == ("DELETE", "field-sections/8/")


def test_field_rules(capsys):
    base = "field-definitions/40/validation-rules/"
    assert only(run(["field-rules", "list", "40"]))[:2] == ("GET*", base)
    assert only(run(["field-rules", "get", "40", "2"]))[:2] == ("GET", base + "2/")
    c = run(["field-rules", "create", "40", "--rule-type", "min_value", "--value", '{"min": 5}',
             "--message", "too small", "--scope", "company"])
    assert only(c) == ("POST", base, False,
                       {"rule_type": "min_value", "value": {"min": 5}, "error_message": "too small"})
    assert only(run(["field-rules", "update", "40", "2", "--active", "false"])) == (
        "PATCH", base + "2/", True, {"is_active": False})
    assert only(run(["field-rules", "delete", "40", "2"]))[:2] == ("DELETE", base + "2/")
    with pytest.raises(SystemExit):
        run(["field-rules", "create", "40", "--rule-type", "min_value", "--value", "{bad"])


# ── resolve_field_labels ─────────────────────────────────────────────────────

FIELDS = [
    {"id": 40, "label": "Size", "field_type": "select",
     "dropdown_options": [{"value": "s", "label": "Small"}, {"value": "M", "label": "Medium"}]},
    {"id": 41, "label": "Crew Count", "field_type": "number"},
    {"id": 42, "label": "Inspected", "field_type": "checkbox"},
    {"id": 43, "label": "Notes", "field_type": "text"},
]


def resolver_client(fields=FIELDS):
    return FakeClient({"activity-types/5/fields/": fields})


def test_resolve_reads_the_types_fields_on_the_project_route():
    c = resolver_client()
    assert resolve_field_labels(c, 5, ["notes=hello = world"]) == {"43": "hello = world"}
    assert c.calls == [{"method": "GET*", "path": "activity-types/5/fields/", "body": None,
                        "params": None, "project_scoped": True}]


def test_resolve_matches_labels_case_insensitively_and_trims():
    assert resolve_field_labels(resolver_client(), 5, [" crew COUNT =3"]) == {"41": 3}


def test_resolve_empty_value_clears():
    assert resolve_field_labels(resolver_client(), 5, ["Size=", "Crew Count="]) == {
        "40": None, "41": None}


def test_resolve_coerces_numbers():
    assert resolve_field_labels(resolver_client(), 5, ["Crew Count=2.5"]) == {"41": 2.5}
    assert resolve_field_labels(resolver_client(), 5, ["Crew Count=4.0"]) == {"41": 4}


def test_resolve_refuses_a_non_number(capsys):
    with pytest.raises(SystemExit) as exc:
        resolve_field_labels(resolver_client(), 5, ["Crew Count=many"])
    assert exc.value.code == 1
    assert "is a number" in capsys.readouterr().err


def test_resolve_coerces_checkbox():
    assert resolve_field_labels(resolver_client(), 5, ["Inspected=Yes"]) == {"42": True}
    assert resolve_field_labels(resolver_client(), 5, ["Inspected=0"]) == {"42": False}
    with pytest.raises(SystemExit):
        resolve_field_labels(resolver_client(), 5, ["Inspected=maybe"])


def test_resolve_maps_a_select_value_or_label_to_the_stored_value(capsys):
    assert resolve_field_labels(resolver_client(), 5, ["Size=medium"]) == {"40": "M"}
    assert resolve_field_labels(resolver_client(), 5, ["Size=S"]) == {"40": "s"}
    with pytest.raises(SystemExit):
        resolve_field_labels(resolver_client(), 5, ["Size=XL"])
    assert "one of: s, M" in capsys.readouterr().err


def test_resolve_select_options_from_the_options_dict():
    fields = [{"id": 50, "label": "Grade", "field_type": "radio",
               "options": {"dropdown": [{"value": "A", "label": "A"}]}}]
    assert resolve_field_labels(resolver_client(fields), 5, ["grade=a"]) == {"50": "A"}


def test_resolve_miss_lists_the_available_labels(capsys):
    with pytest.raises(SystemExit) as exc:
        resolve_field_labels(resolver_client(), 5, ["Colour=red"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no field 'Colour'" in err
    assert "Crew Count, Inspected, Notes, Size" in err


def test_resolve_ambiguous_label_names_both_ids(capsys):
    fields = FIELDS + [{"id": 44, "label": "notes ", "field_type": "text"}]
    with pytest.raises(SystemExit) as exc:
        resolve_field_labels(resolver_client(fields), 5, ["Notes=x"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err and "43, 44" in err


def test_resolve_pair_without_equals_is_refused(capsys):
    with pytest.raises(SystemExit):
        resolve_field_labels(resolver_client(), 5, ["Notes"])
    assert "Label=value" in capsys.readouterr().err


# ── wiring ───────────────────────────────────────────────────────────────────


def test_every_parser_subcommand_has_a_handler_and_keys_match():
    p = _parser()
    groups = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction)).choices
    for group, gp in groups.items():
        sp = next(a for a in gp._actions if isinstance(a, argparse._SubParsersAction))
        assert sp.dest == SUB_COMMAND_KEYS[group]
        assert set(sp.choices) == set(SUB_HANDLERS[group]), group
    assert set(groups) == set(SUB_HANDLERS)
    assert COMPANY_ONLY == set()


def test_group_names_do_not_collide_with_existing_commands():
    existing = set("login projects setup whoami set-project update project files tags folders "
                   "move categorize activities steps progress deps tag-filters gantt tree forms "
                   "fields submissions custom-objects resources rates assignments cost-codes "
                   "budgets timesheets entries cost-entries locks links comments chat audit "
                   "api".split())
    assert not existing & set(SUB_HANDLERS)
