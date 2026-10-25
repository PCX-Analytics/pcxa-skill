"""`activities create|update --field "Label=value"` sets custom fields by label.

The fleet keeps `plan_fields.py` only because the CLI took custom-field ids;
these pin that a label resolves against the activity's own type and merges
into, rather than replaces, the values already set.
"""

from pcxa._parser import build_parser
from pcxa.commands.activities import cmd_activities_create, cmd_activities_update

FIELDS = [{"id": 31, "label": "Size", "field_type": "text"},
          {"id": 32, "label": "Points", "field_type": "number"}]


class Client:
    project_id = 58

    def __init__(self, activity=None):
        self.activity = activity or {}
        self.posts, self.patches, self.pages = [], [], []

    def get_all_pages(self, path, params=None, **kw):
        self.pages.append(path)
        return FIELDS

    def get(self, path, params=None, **kw):
        return self.activity

    def post(self, path, payload=None, **kw):
        self.posts.append(payload)
        return {"id": 9, "title": "T"}

    def patch(self, path, payload=None, **kw):
        self.patches.append(payload)
        return {"id": 9, "title": "T"}


def _parse(*argv):
    return build_parser().parse_args(["activities", *argv, "--no-fuzzy"])


def test_create_resolves_labels_against_the_given_type():
    c = Client()
    cmd_activities_create(c, _parse("create", "--title", "T", "--type", "170",
                                    "--field", "Size=M", "--field", "points=3"))
    assert c.pages == ["activity-types/170/fields/"]
    assert c.posts[0]["custom_field_data"] == {"31": "M", "32": 3}


def test_update_resolves_against_the_activitys_type_and_merges():
    c = Client({"activity_type": 171, "custom_field_data": {"31": "S", "99": "keep"}})
    cmd_activities_update(c, _parse("update", "5", "--field", "Size=L", "--field", "Points="))
    assert c.pages == ["activity-types/171/fields/"]
    assert c.patches[0]["custom_field_data"] == {"31": "L", "32": None, "99": "keep"}
