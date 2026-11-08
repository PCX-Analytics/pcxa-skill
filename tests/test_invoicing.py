"""`pcxa contracts|sov|pay-apps|change-orders` — project invoicing.

Each command is parsed from a real argv and dispatched through the module's
SUB_HANDLERS, so a wrong path, a dropped --contract, a float where the
serializer wants a decimal string, or a write that ignores --dry-run fails here.
"""

import argparse
import json

import pytest

from pcxa.commands.invoicing import SUB_COMMAND_KEYS, SUB_HANDLERS, register_parser


class FakeClient:
    """Records every call; returns a canned body."""

    company_id = 3
    project_id = 7

    def __init__(self, body=None):
        self.body = body if body is not None else {"id": 1, "status": "ok", "results": [], "count": 0}
        self.calls = []

    def _record(self, method, path, payload, project_scoped):
        self.calls.append((method, path, payload, project_scoped))
        return self.body

    def get(self, path, params=None, project_scoped=True, timeout=None):
        return self._record("GET", path, params, project_scoped)

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("POST", path, json_data, project_scoped)

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("PATCH", path, json_data, project_scoped)

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._record("DELETE", path, json_data, project_scoped)

    @staticmethod
    def paginate_params(limit, offset=0):
        params = {"page_size": limit}
        if offset > 0:
            params["page"] = (offset // limit) + 1
        return params


def parse(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--format", default="json")
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="command")
    register_parser(sub)
    return p.parse_args(argv)


def run(argv, client=None):
    client = client or FakeClient()
    args = parse(argv)
    sub_cmd = getattr(args, SUB_COMMAND_KEYS[args.command])
    SUB_HANDLERS[args.command][sub_cmd](client, args)
    return client


CASES = [
    # contracts
    (["contracts", "list", "--status", "active", "--type", "gmp", "--name", "tower", "--limit", "10", "--offset", "10"],
     ("GET", "contracts/", {"page_size": 10, "page": 2, "status": "active", "contract_type": "gmp", "name": "tower"})),
    (["contracts", "get", "5"], ("GET", "contracts/5/", None)),
    (["contracts", "create", "--name", "Tower A", "--type", "fixed_price", "--original-amount", "125000.50",
      "--retainage", "10.00", "--number", "C-1", "--client-name", "Acme", "--start", "2026-01-01",
      "--end", "2026-12-31"],
     ("POST", "contracts/", {"name": "Tower A", "contract_type": "fixed_price", "original_amount": "125000.50",
                             "retainage_percent": "10.00", "contract_number": "C-1", "client_name": "Acme",
                             "start_date": "2026-01-01", "end_date": "2026-12-31"})),
    (["contracts", "update", "5", "--original-amount", "130000.00", "--data", '{"client_contact": "x@y.z"}'],
     ("PATCH", "contracts/5/", {"original_amount": "130000.00", "client_contact": "x@y.z"})),
    (["contracts", "delete", "5"], ("DELETE", "contracts/5/", None)),
    (["contracts", "restore", "5"], ("POST", "contracts/5/restore/", None)),
    # sov
    (["sov", "list", "--contract", "5"], ("GET", "contracts/5/sov-items/", {"page_size": 100})),
    (["sov", "create", "--contract", "5", "--item-number", "001", "--description", "Concrete",
      "--value", "25000.00", "--activity", "11", "--cost-code", "12", "--sort-order", "1"],
     ("POST", "contracts/5/sov-items/", {"item_number": "001", "description": "Concrete",
                                         "scheduled_value": "25000.00", "activity": 11, "cost_code": 12,
                                         "sort_order": 1})),
    (["sov", "update", "9", "--contract", "5", "--value", "26000"],
     ("PATCH", "contracts/5/sov-items/9/", {"scheduled_value": "26000"})),
    (["sov", "delete", "9", "--contract", "5"], ("DELETE", "contracts/5/sov-items/9/", None)),
    # pay-apps
    (["pay-apps", "list", "--contract", "5", "--status", "draft"],
     ("GET", "contracts/5/pay-applications/", {"page_size": 25, "status": "draft"})),
    (["pay-apps", "get", "8", "--contract", "5"], ("GET", "contracts/5/pay-applications/8/", None)),
    (["pay-apps", "create", "--contract", "5", "--period-start", "2026-09-01", "--period-end", "2026-09-30"],
     ("POST", "contracts/5/pay-applications/", {"period_start": "2026-09-01", "period_end": "2026-09-30"})),
    (["pay-apps", "update", "8", "--contract", "5", "--notes", "Sept"],
     ("PATCH", "contracts/5/pay-applications/8/", {"notes": "Sept"})),
    (["pay-apps", "delete", "8", "--contract", "5"], ("DELETE", "contracts/5/pay-applications/8/", None)),
    (["pay-apps", "submit", "8", "--contract", "5"], ("POST", "contracts/5/pay-applications/8/submit/", None)),
    (["pay-apps", "approve", "8", "--contract", "5"], ("POST", "contracts/5/pay-applications/8/approve/", None)),
    (["pay-apps", "reject", "8", "--contract", "5", "--reason", "Wrong period"],
     ("POST", "contracts/5/pay-applications/8/reject/", {"reason": "Wrong period"})),
    (["pay-apps", "reopen", "8", "--contract", "5"], ("POST", "contracts/5/pay-applications/8/reopen/", None)),
    (["pay-apps", "mark-paid", "8", "--contract", "5"],
     ("POST", "contracts/5/pay-applications/8/mark-as-paid/", None)),
    (["pay-apps", "lines", "list", "8", "--contract", "5"],
     ("GET", "contracts/5/pay-applications/8/line-items/", {"page_size": 100})),
    (["pay-apps", "lines", "update", "8", "40", "--contract", "5", "--current", "5000.00", "--stored", "0.00"],
     ("PATCH", "contracts/5/pay-applications/8/line-items/40/",
      {"current_completed": "5000.00", "stored_materials": "0.00"})),
    # change-orders
    (["change-orders", "list", "--contract", "5", "--status", "proposed"],
     ("GET", "contracts/5/change-orders/", {"page_size": 25, "status": "proposed"})),
    (["change-orders", "get", "3", "--contract", "5"], ("GET", "contracts/5/change-orders/3/", None)),
    (["change-orders", "create", "--contract", "5", "--description", "Credit", "--amount", "-1500.00"],
     ("POST", "contracts/5/change-orders/", {"description": "Credit", "amount": "-1500.00"})),
    (["change-orders", "update", "3", "--contract", "5", "--amount", "2000"],
     ("PATCH", "contracts/5/change-orders/3/", {"amount": "2000"})),
    (["change-orders", "delete", "3", "--contract", "5"], ("DELETE", "contracts/5/change-orders/3/", None)),
    (["change-orders", "approve", "3", "--contract", "5"], ("POST", "contracts/5/change-orders/3/approve/", None)),
]


@pytest.mark.parametrize("argv,expected", CASES, ids=[" ".join(a[:3]) for a, _ in CASES])
def test_each_command_hits_the_contract_nested_path(argv, expected, capsys):
    c = run(argv)
    method, path, payload = expected
    assert c.calls == [(method, path, payload, True)]


def test_every_registered_subcommand_is_covered():
    covered = {(a[0], a[1]) for a, _ in CASES}
    registered = {(g, s) for g, subs in SUB_HANDLERS.items() for s in subs}
    assert registered - covered == set()


def test_money_stays_a_string_exactly_as_typed(capsys):
    c = run(["sov", "create", "--contract", "5", "--item-number", "1", "--description", "d", "--value", "0.10"])
    value = c.calls[0][2]["scheduled_value"]
    assert value == "0.10" and isinstance(value, str)


def test_a_non_decimal_amount_is_rejected_before_any_call(capsys):
    with pytest.raises(SystemExit):
        parse(["change-orders", "create", "--contract", "5", "--description", "d", "--amount", "12,000"])
    assert "decimal amount" in capsys.readouterr().err


def test_nested_groups_require_contract(capsys):
    with pytest.raises(SystemExit):
        parse(["pay-apps", "list"])
    assert "--contract" in capsys.readouterr().err


def test_dry_run_write_makes_no_call(capsys):
    c = run(["--dry-run", "contracts", "create", "--name", "T", "--type", "gmp", "--original-amount", "1.00"])
    assert c.calls == []
    out = capsys.readouterr().out
    assert out.startswith("Would CREATE contract:")
    assert json.loads(out.split(":", 1)[1]) == {"name": "T", "contract_type": "gmp", "original_amount": "1.00"}


def test_dry_run_workflow_action_makes_no_call(capsys):
    c = run(["--dry-run", "pay-apps", "reject", "8", "--contract", "5", "--reason", "no"])
    assert c.calls == []
    assert "Would REJECT pay application 8 on contract 5" in capsys.readouterr().out


def test_bad_data_json_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as e:
        run(["contracts", "update", "5", "--data", "{not json"])
    assert e.value.code == 1
    assert "--data is not valid JSON" in capsys.readouterr().err


def test_data_must_be_an_object(capsys):
    with pytest.raises(SystemExit):
        run(["change-orders", "update", "3", "--contract", "5", "--data", "[1]"])
    assert "JSON object" in capsys.readouterr().err


def test_update_with_no_fields_refuses(capsys):
    with pytest.raises(SystemExit):
        run(["pay-apps", "lines", "update", "8", "40", "--contract", "5"])
    assert "No fields to update" in capsys.readouterr().err


def test_table_output_renders_pay_app_detail(capsys):
    body = {"id": 8, "application_number": 2, "status": "draft", "period_start": "2026-09-01",
            "period_end": "2026-09-30", "current_payment_due": "4500.00",
            "line_items": [{"id": 40, "sov_item_number": "001", "current_completed": "5000.00"}]}
    run(["--format", "table", "pay-apps", "get", "8", "--contract", "5"], FakeClient(body))
    out = capsys.readouterr().out
    assert "Pay application 8 #2 [draft]" in out and "4500.00" in out and "5000.00" in out
