"""`activities list --wbs` / `--wbs-branch`, and the guard behind them.

The flags themselves are two query params. The load-bearing part is the guard:
django-filter **silently ignores query params it does not recognise**, so an API
that predates the server-side WBS filters answers `?wbs_branch=1.4` with the
entire project and a 200. The CLI self-upgrades (`pcxa update`) independently of
when the backend deploys, so that window is real, and the failure shape is the
worst kind — a confident superset presented as a filtered result.

Every returned row is therefore checked locally against the requested scope, and
a violation aborts instead of printing.
"""

from types import SimpleNamespace

import pytest

from pcxa._parser import build_parser
from pcxa.commands.activities import _wbs_matches, cmd_activities_list


def _args(**kw):
    d = {
        "status": None, "priority": None, "owner": None, "assignee": None,
        "type": None, "parent": None, "root_only": False, "wbs": None,
        "wbs_branch": None, "search": None, "exact": False, "tags": None,
        "tags_mode": None, "after": None, "before": None, "created_after": None,
        "created_before": None, "sort": None, "count_only": False,
        "limit": 50, "offset": 0, "format": "table", "dry_run": False,
    }
    d.update(kw)
    return SimpleNamespace(**d)


class _Client:
    company_id, project_id = 1, 2

    def __init__(self, rows, count=None):
        self.rows = rows
        self.count = count if count is not None else len(rows)
        self.calls = []

    def paginate_params(self, limit, offset):
        return {"limit": limit, "offset": offset}

    def get(self, path, params=None, **kw):
        self.calls.append(params or {})
        return {"count": self.count, "results": self.rows}

    def get_count(self, path, params=None):
        self.calls.append(params or {})
        return self.count


def _row(i, wbs):
    return {"id": i, "title": f"A{i}", "status": "not_started",
            "percent_complete": 0, "priority": 0, "wbs_code": wbs}


# ── the boundary rule ──────────────────────────────────────────────────────


class TestWbsMatches:
    def test_branch_includes_the_root_itself(self):
        assert _wbs_matches("1.4", None, "1.4")

    def test_branch_includes_descendants_at_any_depth(self):
        assert _wbs_matches("1.4.2.7", None, "1.4")

    def test_branch_excludes_digit_prefix_siblings(self):
        """The trailing dot, mirrored from the server filter."""
        assert not _wbs_matches("1.40", None, "1.4")
        assert not _wbs_matches("1.41", None, "1.4")

    def test_exact_excludes_descendants(self):
        assert _wbs_matches("1.4", "1.4", None)
        assert not _wbs_matches("1.4.1", "1.4", None)

    def test_missing_wbs_code_never_satisfies_a_filter(self):
        assert not _wbs_matches(None, "1.4", None)
        assert not _wbs_matches("", None, "1.4")


# ── params ─────────────────────────────────────────────────────────────────


class TestWbsParams:
    def test_wbs_sends_wbs_code(self):
        c = _Client([_row(1, "1.4")])
        cmd_activities_list(c, _args(wbs="1.4"))
        assert c.calls[0]["wbs_code"] == "1.4"

    def test_wbs_branch_sends_wbs_branch(self):
        c = _Client([_row(1, "1.4"), _row(2, "1.4.1")])
        cmd_activities_list(c, _args(wbs_branch="1.4"))
        assert c.calls[0]["wbs_branch"] == "1.4"

    def test_neither_param_sent_when_unused(self):
        c = _Client([_row(1, "9.9")])
        cmd_activities_list(c, _args())
        assert "wbs_code" not in c.calls[0]
        assert "wbs_branch" not in c.calls[0]

    def test_parser_exposes_both_flags(self):
        a = build_parser().parse_args(
            ["activities", "list", "--wbs", "1.4.2", "--wbs-branch", "1.4"]
        )
        assert a.wbs == "1.4.2"
        assert a.wbs_branch == "1.4"


# ── the guard ──────────────────────────────────────────────────────────────


class TestIgnoredFilterGuard:
    def test_aborts_when_server_returns_an_out_of_scope_row(self, capsys):
        """An API without the filter returns the whole project, 200 and all."""
        c = _Client([_row(1, "1.4"), _row(2, "7.2"), _row(3, "1.4.1")])
        with pytest.raises(SystemExit):
            cmd_activities_list(c, _args(wbs_branch="1.4"))
        err = capsys.readouterr().err
        assert "ignored --wbs-branch 1.4" in err
        assert "Refusing to print" in err

    def test_aborts_on_a_digit_prefix_sibling(self, capsys):
        """The subtle case: 1.40 back from ?wbs_branch=1.4 means a prefix
        match without the trailing dot, i.e. the wrong filter, not no filter."""
        c = _Client([_row(1, "1.4"), _row(2, "1.40")])
        with pytest.raises(SystemExit):
            cmd_activities_list(c, _args(wbs_branch="1.4"))
        assert "1.40" in capsys.readouterr().err

    def test_passes_when_every_row_is_in_scope(self, capsys):
        c = _Client([_row(1, "1.4"), _row(2, "1.4.1"), _row(3, "1.4.2.7")])
        cmd_activities_list(c, _args(wbs_branch="1.4"))
        assert "Activities: 3" in capsys.readouterr().out

    def test_guard_is_inert_without_a_wbs_filter(self, capsys):
        """Rows with unrelated wbs codes are fine when nothing was filtered."""
        c = _Client([_row(1, "9.9"), _row(2, "3.1")])
        cmd_activities_list(c, _args())
        assert "Activities: 2" in capsys.readouterr().out

    def test_json_output_is_also_guarded(self):
        """The check runs before rendering, so --format json cannot slip past."""
        c = _Client([_row(1, "7.2")])
        with pytest.raises(SystemExit):
            cmd_activities_list(c, _args(wbs="1.4", format="json"))

    def test_count_only_probes_before_reporting(self, capsys):
        """A count from an ignored filter is the project total labelled a subtree."""
        c = _Client([_row(1, "7.2")], count=124210)
        with pytest.raises(SystemExit):
            cmd_activities_list(c, _args(wbs_branch="1.4", count_only=True))
        assert "ignored" in capsys.readouterr().err

    def test_count_only_reports_when_the_probe_is_clean(self, capsys):
        c = _Client([_row(1, "1.4.1")], count=621)
        cmd_activities_list(c, _args(wbs_branch="1.4", count_only=True))
        assert '"count": 621' in capsys.readouterr().out


# ── output ─────────────────────────────────────────────────────────────────


class TestWbsColumn:
    def test_wbs_column_appears_only_when_filtering_by_wbs(self, capsys):
        c = _Client([_row(1, "1.4.1")])
        cmd_activities_list(c, _args(wbs_branch="1.4"))
        out = capsys.readouterr().out
        assert "wbs" in out.split("\n")[2]
        assert "1.4.1" in out

    def test_no_wbs_column_otherwise(self, capsys):
        c = _Client([_row(1, "1.4.1")])
        cmd_activities_list(c, _args())
        assert "wbs" not in capsys.readouterr().out.split("\n")[2]
