"""`files query` and `files list --content` after pcxa v1.1.24.

pcxa retired the boolean endpoint and the `content_contains` filter together
(PCX-Analytics/pcxa#3850, issues #33 and #34). Both commands stay callable so
scripts get a pointer and exit 2 — and, above all, never reach the server: a
request to a retired route would 404 into a generic API error, and a stale
`content_contains` param might be silently ignored and return every file.
"""

import pytest

from pcxa._parser import build_parser
from pcxa.commands.files import cmd_files_list, cmd_files_query


class NoNetworkClient:
    project_id = 2

    def __getattr__(self, name):
        raise AssertionError(f"retired command touched the client: {name}")


def test_files_query_is_retired_and_never_calls_the_server(capsys):
    args = build_parser().parse_args(
        ["files", "query", 'title:report AND delay', "--ext", "PDF", "--count-only"])
    assert cmd_files_query(NoNetworkClient(), args) == 2
    err = capsys.readouterr().err
    assert "pcxa#3850" in err and "files search" in err


@pytest.mark.parametrize("argv", [
    ["files", "list", "--content", "IOCC-387"],
    ["files", "list", "--content", "IOCC-387", "--count-only"],
])
def test_files_list_content_is_retired(argv, capsys):
    args = build_parser().parse_args(argv)
    assert cmd_files_list(NoNetworkClient(), args) == 2
    assert "files search" in capsys.readouterr().err
