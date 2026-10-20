"""`pcxa audit` — the company audit log.

Every failure worth testing here produces an export that looks complete and is
not: a date cutoff that stops a page early, a row counted twice when new events
shift the pages, a limit that truncates a CSV without saying so, a username
guess that names nobody (or somebody else), a history that is empty because
auditing was off, and a rate limit that leaves half a history on disk.
"""

import csv
import json
from types import SimpleNamespace

import pytest

from pcxa._http import HTTPError
from pcxa._main import COMPANY_ONLY, SUB_HANDLERS
from pcxa._parser import build_parser
from pcxa._resolve import resolve_ids
from pcxa.commands.audit import (
    LIST_ORDERING,
    PAGE_SIZE,
    cmd_audit_history,
    cmd_audit_list,
    parse_when,
    resolve_username,
)
from tests.conftest import FakeResponse


def ev(seq, ts, user="alice", event="files.get", details=None):
    return {
        "sequence_number": seq, "timestamp": ts, "username": user,
        "user_display_name": "Alice A", "ip_address": "10.0.0.1",
        "event_category": "data_access", "event_type": event,
        "resource_type": "file.File", "resource_id": "42", "resource_label": "a.pdf",
        "details": details or {},
    }


class FakeClient:
    """Serves canned pages per path; records every GET."""

    company_id = 3
    project_id = None
    timeout = None

    def __init__(self, pages=None, members=None, error=None):
        self.pages = pages or {}
        self.members = members or []
        self.error = error
        self.calls = []

    def get(self, path, params=None, project_scoped=True, timeout=None):
        self.calls.append({"path": path, "params": dict(params or {}),
                           "project_scoped": project_scoped, "timeout": timeout})
        if path == "memberships/":
            return {"results": self.members}
        if self.error and self.error[0] == params.get("page"):
            raise self.error[1]
        pages = self.pages[path]
        index = params["page"] - 1
        body = dict(pages[index])
        body.setdefault("next", "more" if index + 1 < len(pages) else None)
        return body


def list_args(**kw):
    d = {"user": None, "type": None, "category": None, "resource_type": None,
         "search": None, "since": None, "until": None, "limit": None, "csv": None,
         "company": None, "format": "json", "dry_run": False}
    d.update(kw)
    return SimpleNamespace(**d)


def history_args(**kw):
    d = {"resource_id": "42", "resource_type": "file.File", "csv": None,
         "company": None, "format": "json", "dry_run": False}
    d.update(kw)
    return SimpleNamespace(**d)


def printed(capsys):
    out = capsys.readouterr()
    return json.loads(out.out), out.err


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_is_company_scoped_time_ordered_and_uses_max_page_size(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": [ev(1, "2026-09-13T10:00:00Z")]}]})
    cmd_audit_list(c, list_args(limit=0, type="file.presign.issued"))
    call = c.calls[0]
    assert call["project_scoped"] is False
    assert call["params"]["ordering"] == LIST_ORDERING
    assert call["params"]["page_size"] == PAGE_SIZE
    assert call["params"]["event_type"] == "file.presign.issued"
    assert call["timeout"] and call["timeout"] > 30


def test_a_longer_global_timeout_is_not_capped(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": []}]})
    c.timeout = 600
    cmd_audit_list(c, list_args(limit=0))
    assert c.calls[0]["timeout"] == 600


def test_since_stops_paging_at_the_first_older_row(capsys):
    c = FakeClient(pages={"audit-logs/": [
        {"results": [ev(9, "2026-09-14T08:00:00Z"), ev(8, "2026-09-13T00:00:00Z"),
                     ev(7, "2026-09-12T23:59:59Z")]},
        {"results": [ev(6, "2026-09-12T01:00:00Z")]},
    ]})
    cmd_audit_list(c, list_args(since="2026-09-13", limit=0))
    data, _ = printed(capsys)
    assert [r["sequence_number"] for r in data["results"]] == [9, 8]
    assert len(c.calls) == 1, "must not read past the cutoff"


def test_until_date_includes_that_whole_day(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": [
        ev(3, "2026-09-14T00:00:00Z"), ev(2, "2026-09-13T23:59:59.999999Z"),
        ev(1, "2026-09-13T00:00:00Z"),
    ]}]})
    cmd_audit_list(c, list_args(until="2026-09-13", limit=0))
    data, _ = printed(capsys)
    assert [r["sequence_number"] for r in data["results"]] == [2, 1]


def test_row_repeated_across_a_page_boundary_is_counted_once(capsys):
    # A new event arriving mid-crawl pushes row 5 from page 1 onto page 2.
    c = FakeClient(pages={"audit-logs/": [
        {"results": [ev(6, "2026-09-14T02:00:00Z"), ev(5, "2026-09-14T01:00:00Z")]},
        {"results": [ev(5, "2026-09-14T01:00:00Z"), ev(4, "2026-09-14T00:00:00Z")]},
    ]})
    cmd_audit_list(c, list_args(limit=0))
    data, _ = printed(capsys)
    assert [r["sequence_number"] for r in data["results"]] == [6, 5, 4]


def test_limit_that_cuts_off_matching_rows_says_so(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": [
        ev(3, "2026-09-14T03:00:00Z"), ev(2, "2026-09-14T02:00:00Z"), ev(1, "2026-09-14T01:00:00Z"),
    ]}]})
    cmd_audit_list(c, list_args(limit=2))
    data, err = printed(capsys)
    assert data["truncated"] is True and data["count"] == 2
    assert "--limit 0" in err


def test_limit_equal_to_the_data_is_not_reported_as_truncated(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": [
        ev(2, "2026-09-14T02:00:00Z"), ev(1, "2026-09-14T01:00:00Z"),
    ]}]})
    cmd_audit_list(c, list_args(limit=2))
    data, err = printed(capsys)
    assert data["truncated"] is False
    assert "--limit" not in err


def test_csv_defaults_to_every_row_and_keeps_the_evidence_columns(tmp_path, capsys):
    # 600 rows, one per page, newest first: more than the on-screen default of 500.
    c = FakeClient(pages={"audit-logs/": [
        {"results": [ev(600 - i, f"2026-09-14T00:00:00.{600 - i:06d}Z")]} for i in range(600)
    ]})
    out = tmp_path / "out.csv"
    cmd_audit_list(c, list_args(csv=str(out)))
    with out.open(newline="", encoding="utf-8") as fh:
        written = list(csv.DictReader(fh))
    assert len(written) == 600, "a CSV export must not stop at the on-screen default"
    assert {"sequence_number", "details", "user_display_name"} <= set(written[0])


def test_csv_flattens_ai_details_and_keeps_details_whole(tmp_path, capsys):
    details = {"is_ai_agent": True, "conversation_id": 77, "tool_name": "search_files"}
    c = FakeClient(pages={"audit-logs/": [{"results": [
        ev(1, "2026-09-14T01:00:00Z", event="file.ai_quoted", details=details)]}]})
    out = tmp_path / "out.csv"
    cmd_audit_list(c, list_args(csv=str(out)))
    with out.open(newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["is_ai_agent"] == "True" and row["conversation_id"] == "77"
    assert json.loads(row["details"]) == details


def test_invalid_date_is_refused_before_any_request(capsys):
    c = FakeClient()
    assert cmd_audit_list(c, list_args(since="13/09/2026")) == 2
    assert c.calls == []


def test_parse_when_treats_naive_times_as_utc():
    assert parse_when("2026-09-13T10:00:00") == parse_when("2026-09-13T10:00:00Z")


# ── --user ───────────────────────────────────────────────────────────────────


def test_username_without_at_is_used_verbatim_with_no_lookup():
    c = FakeClient()
    assert resolve_username(c, "jdoe1") == "jdoe1"
    assert c.calls == []


def test_email_resolves_to_the_member_with_that_exact_email(capsys):
    # Same email prefix, two people: the prefix alone names the wrong one.
    c = FakeClient(members=[
        {"user_email": "jdoe@other.com", "user_username": "jdoe"},
        {"user_email": "JDoe@Example.com", "user_username": "jdoe1"},
    ])
    assert resolve_username(c, "jdoe@example.com") == "jdoe1"
    assert c.calls[0]["project_scoped"] is False
    assert "jdoe1" in capsys.readouterr().err


def test_unknown_email_exits_instead_of_guessing_the_prefix(capsys):
    c = FakeClient(members=[{"user_email": "jdoe@other.com", "user_username": "jdoe"}])
    with pytest.raises(SystemExit) as exc:
        resolve_username(c, "jdoe@example.com")
    assert exc.value.code == 1
    assert "--search jdoe" in capsys.readouterr().err


def test_zero_rows_for_a_username_explains_the_exact_match(capsys):
    c = FakeClient(pages={"audit-logs/": [{"results": []}]})
    cmd_audit_list(c, list_args(user="jdoe"))
    data, err = printed(capsys)
    assert data["count"] == 0
    assert "exact match" in err


# ── history ──────────────────────────────────────────────────────────────────


def test_history_reads_every_page_in_server_order(capsys):
    c = FakeClient(pages={"audit-logs/resource-history/": [
        {"results": [ev(1, "2026-09-01T00:00:00Z")], "audit_enabled": True},
        {"results": [ev(2, "2026-09-02T00:00:00Z")], "audit_enabled": True},
    ]})
    cmd_audit_history(c, history_args())
    data, err = printed(capsys)
    assert [r["sequence_number"] for r in data["results"]] == [1, 2]
    assert c.calls[0]["params"]["resource_type"] == "file.File"
    assert c.calls[0]["params"]["resource_id"] == "42"
    assert c.calls[0]["params"]["page_size"] == PAGE_SIZE
    assert "WARNING" not in err


def test_history_with_auditing_disabled_warns_that_empty_is_not_evidence(capsys):
    c = FakeClient(pages={"audit-logs/resource-history/": [{"results": [], "audit_enabled": False}]})
    cmd_audit_history(c, history_args())
    data, err = printed(capsys)
    assert data["audit_enabled"] is False
    assert "does NOT mean nobody accessed it" in err


def test_rate_limit_mid_history_writes_nothing(tmp_path, capsys):
    throttled = HTTPError(SimpleNamespace(status_code=429, reason="Too Many Requests", url="u",
                                          headers={"retry-after": "1800"}))
    c = FakeClient(
        pages={"audit-logs/resource-history/": [{"results": [ev(1, "2026-09-01T00:00:00Z")]},
                                                {"results": []}]},
        error=(2, throttled),
    )
    out = tmp_path / "h.csv"
    assert cmd_audit_history(c, history_args(csv=str(out))) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "10 requests/hour" in err and "31 min" in err and "NOT written" in err


def test_non_rate_limit_errors_propagate():
    forbidden = HTTPError(FakeResponse(403, {"detail": "nope"}, reason="Forbidden"))
    c = FakeClient(pages={"audit-logs/resource-history/": [{"results": []}]}, error=(1, forbidden))
    with pytest.raises(HTTPError):
        cmd_audit_history(c, history_args())


# ── wiring ───────────────────────────────────────────────────────────────────


def test_parser_accepts_both_subcommands_with_company():
    parser = build_parser()
    a = parser.parse_args(["audit", "history", "16457660", "--company", "3", "-f", "table"])
    assert (a.command, a.audit_command, a.resource_id, a.company, a.format) == (
        "audit", "history", "16457660", 3, "table")
    a = parser.parse_args(["audit", "list", "--user", "x@y.com", "--since", "2026-09-13", "--csv", "o.csv"])
    assert a.audit_command == "list" and a.limit is None
    assert set(SUB_HANDLERS["audit"]) == {"list", "history"}


def test_company_only_commands_do_not_need_a_project():
    assert "audit" in COMPANY_ONLY

    class NoNetwork:
        company_id = 3
        project_id = None
        base_url = "https://api.example.com"

        def get_raw(self, url, params=None):
            raise AssertionError(f"unexpected lookup: {url}")

    resolve_ids(NoNetwork(), need_project=False)
