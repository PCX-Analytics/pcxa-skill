"""Cross-cutting helpers: ID/name resolution, generic object refs.

These utilities are shared by multiple command modules and depend only on the
APIClient.
"""

import difflib
import sys

from pcxa._http import requests


def resolve_member_by_name(client, query):
    """Resolve a name query to a user ID via fuzzy matching on project members.

    Returns (user_id, message) where message describes the match outcome.
    On ambiguity or no match, user_id is None and message explains next steps.
    """
    data = client.get("memberships/", {"limit": 200})
    results = data.get("results", data) if isinstance(data, dict) else data
    members = [m for m in results if not m.get("is_ai_agent")]
    if not members:
        return None, "No project members found."

    query_lower = query.lower().strip()

    candidates = []
    for m in members:
        uid = m.get("user")
        name = m.get("user_name", "")
        username = m.get("user_username", "")
        email = m.get("user_email", "")
        candidates.append((uid, name, username, email))

    for uid, name, username, email in candidates:
        if query_lower in (name.lower(), username.lower(), email.lower()):
            return uid, f"Exact match: {name} (user {uid})"

    substring_hits = []
    for uid, name, username, email in candidates:
        combined = f"{name} {username} {email}".lower()
        if query_lower in combined:
            substring_hits.append((uid, name, username))

    if len(substring_hits) == 1:
        uid, name, username = substring_hits[0]
        return uid, f"Matched: {name} [{username}] (user {uid})"

    if len(substring_hits) >= 2:
        lines = [f"Multiple matches for '{query}':"]
        for uid, name, username in substring_hits:
            lines.append(f"  - user {uid}: {name} [{username}]")
        lines.append("Pass --assignee <user_id> with the correct ID.")
        return None, "\n".join(lines)

    scored = []
    for uid, name, username, email in candidates:
        s1 = difflib.SequenceMatcher(None, query_lower, name.lower()).ratio()
        s2 = difflib.SequenceMatcher(None, query_lower, username.lower()).ratio()
        best = max(s1, s2)
        scored.append((best, uid, name, username))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored[0][0] >= 0.6:
        top = scored[0]
        if len(scored) > 1 and scored[1][0] >= top[0] - 0.1:
            close = [s for s in scored if s[0] >= top[0] - 0.1][:5]
            lines = [f"No exact match for '{query}'. Close matches:"]
            for score, uid, name, username in close:
                lines.append(f"  - user {uid}: {name} [{username}] ({score:.0%})")
            lines.append("Pass --assignee <user_id> with the correct ID.")
            return None, "\n".join(lines)
        _, uid, name, username = top
        return uid, f"Fuzzy match: {name} [{username}] (user {uid})"

    return None, f"No match found for '{query}'. Use `pcxa project members` to list all members."


def fetch_field_choice_options(client, object_id, scope="project"):
    """Return the list of option dicts for a custom object (field-choice).

    ``scope`` is ``"project"`` (default) or ``"company"`` — field-choices exist
    at both scopes. Returns a flat list (all pages).
    """
    project_scoped = scope != "company"
    return client.get_all_pages(
        f"field-choices/{object_id}/options/", project_scoped=project_scoped
    )


def resolve_field_choice_option(client, object_id, query, scope="project", options=None):
    """Resolve a query to a custom-object option (FieldChoiceOption).

    Returns ``(option, message)``. On a confident hit (exact label/id or a single
    substring match) ``option`` is the matching dict. Otherwise ``option`` is
    None and ``message`` carries a "No match for 'X'. Did you mean 'Y'?" style
    suggestion — fuzzy guesses are surfaced as suggestions, never silently
    accepted into a write.

    Pass ``options`` to validate against an already-fetched list (avoids a
    second round trip when several values share one object).
    """
    if options is None:
        options = fetch_field_choice_options(client, object_id, scope)
    active = [o for o in options if o.get("is_active", True)]
    if not active:
        return None, f"Custom object {object_id} has no options to match against."

    q = str(query).strip()
    ql = q.lower()

    # Exact: case-insensitive label, or the option id itself.
    for o in active:
        if ql == str(o.get("label", "")).strip().lower() or str(o.get("id")) == q:
            return o, f"Exact match: {o.get('label')} (option {o.get('id')})"

    # Substring on label — a single hit is confident enough to auto-resolve.
    subs = [o for o in active if ql in str(o.get("label", "")).lower()]
    if len(subs) == 1:
        o = subs[0]
        return o, f"Matched: {o.get('label')} (option {o.get('id')})"
    if len(subs) >= 2:
        lines = [f"Multiple matches for '{q}':"]
        for o in subs[:10]:
            lines.append(f"  - option {o.get('id')}: {o.get('label')}")
        lines.append("Pass the exact label or option id.")
        return None, "\n".join(lines)

    # Fuzzy: never auto-resolve, always suggest the closest label(s).
    scored = sorted(
        (
            (difflib.SequenceMatcher(None, ql, str(o.get("label", "")).lower()).ratio(), o)
            for o in active
        ),
        key=lambda x: x[0],
        reverse=True,
    )
    best_score, best = scored[0]
    close = [(s, o) for s, o in scored if s >= max(best_score - 0.1, 0.45)][:5]
    if len(close) > 1:
        lines = [f"No match for '{q}'. Did you mean:"]
        for s, o in close:
            lines.append(f"  - option {o.get('id')}: {o.get('label')} ({s:.0%})")
        return None, "\n".join(lines)
    return None, f"No match for '{q}'. Did you mean '{best.get('label')}' (option {best.get('id')})?"


# Candidate attribute names a form field may use to bind to a FieldChoice
# (custom object). The exact name couldn't be confirmed against the live API
# (the test account is permission-gated off field-choices/ and forms/), so we
# check the documented candidates and fall back to scanning the `options` blob.
_FIELD_CHOICE_KEYS = (
    "field_choice", "field_choice_id", "choice_set", "choice_set_id",
    "custom_object", "custom_object_id",
)


def field_choice_ref(field):
    """Return the FieldChoice id a form field is bound to, or None.

    Tolerant of int / numeric-string / nested ``{"id": N}`` shapes, and of the
    reference living either at the top level of the field or inside its
    ``options`` JSON. Returns None for plain (non-custom-object) fields.
    """
    if not isinstance(field, dict):
        return None

    def _as_id(v):
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        if isinstance(v, dict) and isinstance(v.get("id"), int):
            return v["id"]
        return None

    for key in _FIELD_CHOICE_KEYS:
        ref = _as_id(field.get(key))
        if ref is not None:
            return ref

    opts = field.get("options")
    if isinstance(opts, dict):
        for key, v in opts.items():
            if any(s in str(key).lower() for s in ("field_choice", "choice_set", "custom_object")):
                ref = _as_id(v)
                if ref is not None:
                    return ref
    return None


def validate_choice_field_values(client, fields, values):
    """Fuzzy-validate ``{field_id: value}`` against custom-object-backed fields.

    Shared by form submissions and activity custom fields. ``fields`` is a list
    of field-definition dicts (from a form's fields or ``activities/custom-fields/``);
    only those bound to a FieldChoice (see ``field_choice_ref``) are checked.

    Returns ``(problems, notes)``:
      * ``problems`` — "Field 'X' (custom object N): <did-you-mean ...>" strings
        for values with no confident match (caller decides whether to block).
      * ``notes`` — fields whose object could not be read (permissions/network);
        these are skipped, never blocked on.

    A custom object can live at project or company scope, so both are tried.
    """
    bound = {}  # str(field_id) -> (field_choice_id, label)
    for f in fields:
        ref = field_choice_ref(f)
        if ref is not None:
            label = f.get("label") or f.get("name") or f.get("id")
            bound[str(f.get("id"))] = (ref, label)

    problems, notes = [], []
    for fid, val in (values or {}).items():
        if str(fid) not in bound:
            continue
        choice_id, label = bound[str(fid)]
        option, msg = None, None
        for scope in ("project", "company"):
            try:
                option, msg = resolve_field_choice_option(client, choice_id, val, scope=scope)
                break
            except Exception:
                continue
        if msg is None:
            notes.append(f"field '{label}' (custom object {choice_id} unreadable)")
            continue
        if option is None:
            problems.append(f"Field '{label}' (custom object {choice_id}): {msg}")
    return problems, notes


def resolve_ids(client):
    """Auto-resolve company_id / project_id when only one is available."""
    if client.company_id and client.project_id:
        return
    try:
        if not client.company_id:
            data = client.get_raw(f"{client.base_url}/api/companies/")
            companies = data.get("results", data) if isinstance(data, dict) else data
            if len(companies) == 1:
                client.company_id = companies[0]["id"]
            else:
                print("Multiple companies. Use --company or set in profile.", file=sys.stderr)
                sys.exit(1)
        if not client.project_id:
            data = client.get_raw(f"{client.base_url}/api/companies/{client.company_id}/projects/")
            projects = data.get("results", data) if isinstance(data, dict) else data
            if len(projects) == 1:
                client.project_id = projects[0]["id"]
            else:
                print("Multiple projects. Use --project or set in profile.", file=sys.stderr)
                sys.exit(1)
    except requests.ConnectionError:
        print(f"Cannot connect to {client.base_url}", file=sys.stderr)
        sys.exit(1)


def parse_object_ref(ref):
    """Parse 'type:id' reference into (type_string, object_id).

    Example: 'file:170106' -> ('file', 170106)
    """
    parts = ref.split(":", 1)
    if len(parts) != 2:
        print(f"Invalid object reference '{ref}'. Use format type:id (e.g. file:123, activity:456)", file=sys.stderr)
        sys.exit(1)
    obj_type, obj_id = parts
    try:
        return obj_type.strip(), int(obj_id.strip())
    except ValueError:
        print(f"Invalid ID in reference '{ref}'. ID must be an integer.", file=sys.stderr)
        sys.exit(1)


def links_url(client, path=""):
    """Build URL for generic-links endpoint (top-level, not project-scoped)."""
    return f"{client.base_url}/api/generic-links/{path}"


__all__ = [
    "resolve_member_by_name",
    "fetch_field_choice_options",
    "resolve_field_choice_option",
    "field_choice_ref",
    "validate_choice_field_values",
    "resolve_ids",
    "parse_object_ref",
    "links_url",
]
