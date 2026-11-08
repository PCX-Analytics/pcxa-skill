"""Member lookups must read past the first 25 rows.

`memberships/` uses StandardPagination, which reads `page_size` and ignores
`limit`: sending `limit` left `project members` and every name-to-id lookup
(`--owner`, `--assignee`) on the default page of 25 members.
"""

from types import SimpleNamespace

from pcxa._resolve import resolve_member_by_name
from pcxa.commands.project import cmd_project_members


class Client:
    def __init__(self):
        self.params = []

    def get(self, path, params=None, **kw):
        self.params.append(params)
        return {"results": [{"user": 7, "user_name": "Ana Ruiz", "user_username": "ana"}]}


def test_project_members_sends_page_size():
    c = Client()
    cmd_project_members(c, SimpleNamespace(search=None, format="json"))
    assert c.params[0].get("page_size", 0) >= 1000 and "limit" not in c.params[0]


def test_member_resolution_sends_page_size():
    c = Client()
    resolve_member_by_name(c, "ana")
    assert c.params[0].get("page_size", 0) >= 1000 and "limit" not in c.params[0]
