"""Admin commands: the web UI's project Members / Permissions screens, the
company Team, Settings (company, security policy, permission templates),
programs, notifications and "Needs you".

Backend routes (``api/backend/nested_urls.py`` @95addac48):

- ``members``    project ``memberships/`` + ``access-summary/`` + ``resource-access/``
- ``groups``     project ``groups/`` (+ nested ``members/``, ``rules/``)
- ``team``       company ``memberships/`` + ``invitations/``
- ``permission-templates``  company ``group-templates/`` (+ nested ``rules/``)
- ``company``    ``/api/companies/{id}/`` + ``security-policy/`` + ``sso-config/`` (read only)
- ``programs``   company ``programs/``
- ``notifications`` / ``needs-you`` live OUTSIDE the company tree, at
  ``/api/notifications/`` with a mandatory ``?company=`` (notifications/urls.py),
  so they go through ``client._request`` with a full URL.

``pcxa project members`` (name -> user id lookup) is kept; ``members`` here is the
full roster management.
"""

import argparse
import json
import sys

from pcxa._output import out_json, out_table


def _bool(x):
    return x.lower() in ("true", "1", "yes")


def _ids(raw):
    return [int(x) for x in raw.split(",") if x.strip()]


def _json_data(raw, flag="--data"):
    """Parse a JSON-object flag, or exit 1 with a clean message."""
    try:
        value = json.loads(raw)
    except ValueError as e:
        print(f"Invalid JSON for {flag}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(value, dict):
        print(f"{flag} must be a JSON object.", file=sys.stderr)
        sys.exit(1)
    return value


def _merge_data(payload, args):
    """Merge ``--data`` last so it can set any field the typed flags don't."""
    if getattr(args, "data", None):
        payload.update(_json_data(args.data))
    return payload


def _require(payload):
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)


def _results(data):
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    return results, total


def _list_params(client, args):
    params = client.paginate_params(args.limit, args.offset)
    if getattr(args, "search", None):
        params["search"] = args.search
    if getattr(args, "sort", None):
        params["ordering"] = args.sort
    return params


def _show(args, data, line):
    if args.format == "json":
        out_json(data)
    else:
        print(line)


# ═══════════════════════════════════════════════════════════════════════════════
# MEMBERS — project memberships (ProjectMembershipViewSet)
# ═══════════════════════════════════════════════════════════════════════════════


def _member_row(m):
    return {
        "id": str(m.get("id", "")),
        "user": str(m.get("user", "")),
        "name": str(m.get("user_name") or m.get("user_display_name") or "")[:30],
        "email": str(m.get("user_email", ""))[:35],
        "role": str(m.get("role", ""))[:25],
        "admin": "yes" if m.get("is_admin") else "",
        "groups": ",".join(m.get("groups") or [])[:30],
    }


def cmd_members_list(client, args):
    """List project memberships (with permission-group names)."""
    params = _list_params(client, args)
    if args.user:
        params["user"] = args.user
    data = client.get("memberships/", params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    print(f"Project members: {len(results)} of {total}\n")
    out_table([_member_row(m) for m in results], ["id", "user", "name", "email", "role", "admin", "groups"])


def cmd_members_get(client, args):
    """Project membership detail."""
    data = client.get(f"memberships/{args.membership_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Membership {data.get('id')}: {data.get('user_name')} <{data.get('user_email')}>")
    print(f"  User:   {data.get('user')}")
    print(f"  Role:   {data.get('role')}")
    print(f"  Admin:  {data.get('is_admin')}")
    print(f"  Owner:  {data.get('is_project_owner')}")
    print()


def cmd_members_add(client, args):
    """Add a company member to the project.

    ``authorization_level`` is a required legacy column; it is derived from
    ``--admin`` exactly as the web UI does (members/api/index.ts).
    """
    payload = {
        "user": args.user,
        "project": client.project_id,
        "role": args.role,
        "is_admin": args.admin,
        "authorization_level": "admin" if args.admin else "standard",
    }
    if args.groups:
        payload["permission_group_ids"] = _ids(args.groups)
    _merge_data(payload, args)
    if args.dry_run:
        print(f"Would CREATE project membership: {json.dumps(payload, indent=2)}")
        return
    data = client.post("memberships/", payload)
    _show(args, data, f"Added user {data.get('user')} to project (membership {data.get('id')})")


def cmd_members_update(client, args):
    """Update a project membership (role, admin flag, names)."""
    payload = {}
    if args.role is not None:
        payload["role"] = args.role
    if args.admin is not None:
        payload["is_admin"] = args.admin
    if args.first_name is not None:
        payload["user_first_name"] = args.first_name
    if args.last_name is not None:
        payload["user_last_name"] = args.last_name
    _merge_data(payload, args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE project membership {args.membership_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"memberships/{args.membership_id}/", payload)
    _show(args, data, f"Updated project membership {data.get('id')}")


def cmd_members_remove(client, args):
    """Remove a member from the project (the owner cannot be removed)."""
    if args.dry_run:
        print(f"Would DELETE project membership {args.membership_id}")
        return
    client.delete(f"memberships/{args.membership_id}/")
    print(f"Removed project membership {args.membership_id}")


def cmd_members_access(client, args):
    """Access summary: every member's groups + access counts, or one user's detail."""
    if args.user_id is not None:
        data = client.get(f"access-summary/{args.user_id}/")
        out_json(data)
        return
    data = client.get("access-summary/")
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for s in data:
        user = s.get("user") or {}
        rows.append({
            "user": str(user.get("id", "")),
            "name": str(user.get("display_name") or user.get("username") or "")[:30],
            "groups": ",".join(g.get("name", "") for g in s.get("groups") or [])[:40],
        })
    out_table(rows, ["user", "name", "groups"])


def cmd_members_resource_access(client, args):
    """Company resources (custom objects, activity types, forms) enabled in this project."""
    params = client.paginate_params(args.limit, args.offset)
    if args.enabled is not None:
        params["is_enabled"] = str(args.enabled).lower()
    data = client.get("resource-access/", params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(r.get("id", "")),
        "type": str(r.get("resource_type", "")),
        "resource": str(r.get("resource_id", "")),
        "name": str(r.get("resource_name") or "")[:35],
        "enabled": "yes" if r.get("is_enabled") else "",
    } for r in results]
    print(f"Resource access: {len(rows)} of {total}\n")
    out_table(rows, ["id", "type", "resource", "name", "enabled"])


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPS — project permission groups (ProjectGroupViewSet + nested members/rules)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_groups_list(client, args):
    """List permission groups in the project."""
    data = client.get("groups/", _list_params(client, args))
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(g.get("id", "")),
        "name": str(g.get("name", ""))[:35],
        "template": str(g.get("template_name") or ""),
        "default": "yes" if g.get("is_default") else "",
        "members": str(g.get("member_count", "")),
        "rules": str(g.get("rule_count", "")),
    } for g in results]
    print(f"Permission groups: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "template", "default", "members", "rules"])


def cmd_groups_get(client, args):
    """Group detail, including members and rules."""
    out_json(client.get(f"groups/{args.group_id}/"))


def _group_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.template is not None:
        payload["linked_template"] = args.template or None
    return _merge_data(payload, args)


def cmd_groups_create(client, args):
    """Create a permission group."""
    payload = _group_payload(args)
    if args.dry_run:
        print(f"Would CREATE permission group: {json.dumps(payload, indent=2)}")
        return
    data = client.post("groups/", payload)
    _show(args, data, f"Created permission group {data.get('id')}: '{data.get('name')}'")


def cmd_groups_update(client, args):
    """Update a permission group."""
    payload = _group_payload(args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE permission group {args.group_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"groups/{args.group_id}/", payload)
    _show(args, data, f"Updated permission group {data.get('id')}")


def cmd_groups_delete(client, args):
    """Delete a permission group."""
    if args.dry_run:
        print(f"Would DELETE permission group {args.group_id}")
        return
    client.delete(f"groups/{args.group_id}/")
    print(f"Deleted permission group {args.group_id}")


def cmd_group_members_list(client, args):
    """List a group's members (ids here are group-membership ids, not user ids)."""
    params = client.paginate_params(args.limit, args.offset)
    if args.search:
        params["search"] = args.search
    data = client.get(f"groups/{args.group_id}/members/", params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = []
    for m in results:
        user = m.get("user_detail") or {}
        rows.append({
            "id": str(m.get("id", "")),
            "user": str(m.get("user", "")),
            "name": str(user.get("display_name") or user.get("username") or "")[:30],
            "added": str(m.get("added_at", ""))[:10],
        })
    print(f"Group {args.group_id} members: {len(rows)} of {total}\n")
    out_table(rows, ["id", "user", "name", "added"])


def cmd_group_members_add(client, args):
    """Add a project member (by user id) to a group."""
    payload = {"user": args.user}
    if args.dry_run:
        print(f"Would CREATE group membership in group {args.group_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"groups/{args.group_id}/members/", payload)
    _show(args, data, f"Added user {args.user} to group {args.group_id} (membership {data.get('id')})")


def cmd_group_members_remove(client, args):
    """Remove a group membership (by group-membership id)."""
    if args.dry_run:
        print(f"Would DELETE group membership {args.membership_id} from group {args.group_id}")
        return
    client.delete(f"groups/{args.group_id}/members/{args.membership_id}/")
    print(f"Removed group membership {args.membership_id} from group {args.group_id}")


# ── access rules: shared by `groups rules` and `permission-templates rules` ──
# Same serializer shape on both sides (accounts/serializers.py:231 and :427).


RULE_RESOURCE_TYPES = [
    "activity_type", "form_type", "form_template", "drawing_filter", "photo",
    "file", "resource", "timesheet", "invoice", "members",
]


def _rules_base(args):
    """(path, project_scoped, label) for the rules of a group or a template."""
    if args.command == "groups":
        return f"groups/{args.group_id}/rules/", True, f"group {args.group_id}"
    return f"group-templates/{args.template_id}/rules/", False, f"template {args.template_id}"


def _rule_payload(args):
    payload = {}
    if args.type is not None:
        payload["resource_type"] = args.type
    if args.resource_id is not None:
        payload["resource_id"] = args.resource_id
    if args.value is not None:
        payload["resource_value"] = args.value
    if args.level is not None:
        payload["permission_level"] = args.level
    return payload


def cmd_rules_list(client, args):
    """List access rules."""
    path, scoped, label = _rules_base(args)
    data = client.get(path, client.paginate_params(args.limit, args.offset), project_scoped=scoped)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(r.get("id", "")),
        "type": str(r.get("resource_type", "")),
        "resource": str(r.get("resource_id") or r.get("resource_value") or ""),
        "level": f"{r.get('permission_level')} {r.get('permission_level_display', '')}".strip(),
    } for r in results]
    print(f"Rules for {label}: {len(rows)} of {total}\n")
    out_table(rows, ["id", "type", "resource", "level"])


def cmd_rules_create(client, args):
    """Create an access rule."""
    path, scoped, label = _rules_base(args)
    payload = _rule_payload(args)
    if args.dry_run:
        print(f"Would CREATE rule on {label}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(path, payload, project_scoped=scoped)
    _show(args, data, f"Created rule {data.get('id')} on {label}")


def cmd_rules_update(client, args):
    """Update an access rule."""
    path, scoped, label = _rules_base(args)
    payload = _rule_payload(args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE rule {args.rule_id} on {label}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"{path}{args.rule_id}/", payload, project_scoped=scoped)
    _show(args, data, f"Updated rule {data.get('id')} on {label}")


def cmd_rules_delete(client, args):
    """Delete an access rule."""
    path, scoped, label = _rules_base(args)
    if args.dry_run:
        print(f"Would DELETE rule {args.rule_id} from {label}")
        return
    client.delete(f"{path}{args.rule_id}/", project_scoped=scoped)
    print(f"Deleted rule {args.rule_id} from {label}")


_RULE_HANDLERS = {
    "list": cmd_rules_list,
    "create": cmd_rules_create,
    "update": cmd_rules_update,
    "delete": cmd_rules_delete,
}
_GROUP_MEMBER_HANDLERS = {
    "list": cmd_group_members_list,
    "add": cmd_group_members_add,
    "remove": cmd_group_members_remove,
}


def _dispatch(handlers, key, usage):
    def run(client, args):
        fn = handlers.get(getattr(args, key, None))
        if fn is None:
            print(f"Usage: pcxa {usage} {{{', '.join(handlers)}}}", file=sys.stderr)
            sys.exit(1)
        return fn(client, args)
    return run


cmd_groups_members = _dispatch(_GROUP_MEMBER_HANDLERS, "group_members_command", "groups members")
cmd_groups_rules = _dispatch(_RULE_HANDLERS, "group_rules_command", "groups rules")


# ═══════════════════════════════════════════════════════════════════════════════
# TEAM — company memberships + invitations
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_team_list(client, args):
    """List company members."""
    params = _list_params(client, args)
    if args.role:
        params["role"] = args.role
    if args.admin is not None:
        params["is_admin"] = str(args.admin).lower()
    data = client.get("memberships/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(m.get("id", "")),
        "user": str(m.get("user", "")),
        "name": str(m.get("user_full_name") or m.get("user_display_name") or "")[:30],
        "email": str(m.get("user_email", ""))[:35],
        "role": str(m.get("role", ""))[:20],
        "admin": "yes" if m.get("is_admin") else "",
        "ai": "yes" if m.get("ai_access_enabled") else "",
        "active": "yes" if m.get("user_is_active") else "",
    } for m in results]
    print(f"Company members: {len(rows)} of {total}\n")
    out_table(rows, ["id", "user", "name", "email", "role", "admin", "ai", "active"])


def cmd_team_get(client, args):
    """Company membership detail."""
    data = client.get(f"memberships/{args.membership_id}/", project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    print(f"Membership {data.get('id')}: {data.get('user_full_name')} <{data.get('user_email')}>")
    print(f"  User:       {data.get('user')}")
    print(f"  Role:       {data.get('role')}")
    print(f"  Admin:      {data.get('is_admin')}")
    print(f"  AI access:  {data.get('ai_access_enabled')}")
    print(f"  Active:     {data.get('user_is_active')}  (last {data.get('user_last_active') or '-'})")
    print()


def cmd_team_update(client, args):
    """Update a company membership (role, admin, AI access, names)."""
    payload = {}
    if args.role is not None:
        payload["role"] = args.role
    if args.admin is not None:
        payload["is_admin"] = args.admin
    if args.ai_access is not None:
        payload["ai_access_enabled"] = args.ai_access
    if args.first_name is not None:
        payload["user_first_name"] = args.first_name
    if args.last_name is not None:
        payload["user_last_name"] = args.last_name
    _merge_data(payload, args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE company membership {args.membership_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"memberships/{args.membership_id}/", payload, project_scoped=False)
    _show(args, data, f"Updated company membership {data.get('id')}")


def cmd_team_remove(client, args):
    """Remove a member from the company."""
    if args.dry_run:
        print(f"Would DELETE company membership {args.membership_id}")
        return
    client.delete(f"memberships/{args.membership_id}/", project_scoped=False)
    print(f"Removed company membership {args.membership_id}")


def cmd_team_invite(client, args):
    """Invite someone by email. SENDS AN EMAIL to that address.

    A pending invitation to the same email is re-sent (and its project grant
    updated) instead of duplicated (CompanyInvitationViewSet.create).
    """
    payload = {"email": args.email, "role": args.role, "is_admin": args.admin,
               "ai_access_enabled": args.ai_access}
    if args.project_id is not None:
        payload["project_id"] = args.project_id
        payload["project_is_admin"] = args.project_admin
        if args.project_role:
            payload["project_role"] = args.project_role
    if args.groups:
        payload["permission_group_ids"] = _ids(args.groups)
    if args.dry_run:
        print(f"Would CREATE invitation (emails {args.email}): {json.dumps(payload, indent=2)}")
        return
    data = client.post("invitations/", payload, project_scoped=False)
    _show(args, data, f"Invited {data.get('email')} (invitation {data.get('id')}, expires {data.get('expires_at')})")


def cmd_invitations_list(client, args):
    """List company invitations."""
    params = _list_params(client, args)
    if args.status:
        params["status"] = args.status
    data = client.get("invitations/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(i.get("id", "")),
        "email": str(i.get("email", ""))[:35],
        "role": str(i.get("role", "")),
        "status": str(i.get("status", "")),
        "project": str(i.get("project_name") or ""),
        "expires": str(i.get("expires_at") or "")[:10],
    } for i in results]
    print(f"Invitations: {len(rows)} of {total}\n")
    out_table(rows, ["id", "email", "role", "status", "project", "expires"])


def cmd_invitations_revoke(client, args):
    """Cancel a pending invitation."""
    if args.dry_run:
        print(f"Would DELETE (cancel) invitation {args.invitation_id}")
        return
    client.delete(f"invitations/{args.invitation_id}/", project_scoped=False)
    print(f"Cancelled invitation {args.invitation_id}")


def cmd_invitations_resend(client, args):
    """Re-send a pending invitation email (extends an expired one by 72h)."""
    if args.dry_run:
        print(f"Would RESEND invitation {args.invitation_id}")
        return
    data = client.post(f"invitations/{args.invitation_id}/resend/", {}, project_scoped=False)
    _show(args, data, f"Resent invitation {args.invitation_id}")


cmd_team_invitations = _dispatch({
    "list": cmd_invitations_list,
    "revoke": cmd_invitations_revoke,
    "resend": cmd_invitations_resend,
}, "team_invitations_command", "team invitations")


# ═══════════════════════════════════════════════════════════════════════════════
# PERMISSION TEMPLATES — company group templates (CompanyGroupTemplateViewSet)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_permission_templates_list(client, args):
    """List permission templates (active only unless --active false)."""
    params = _list_params(client, args)
    if args.active is not None:
        params["is_active"] = str(args.active).lower()
    data = client.get("group-templates/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(t.get("id", "")),
        "name": str(t.get("name", ""))[:35],
        "system": "yes" if t.get("is_system_default") else "",
        "active": "yes" if t.get("is_active") else "",
        "groups": str(t.get("linked_groups_count", "")),
        "rules": str(t.get("rule_count", "")),
    } for t in results]
    print(f"Permission templates: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "system", "active", "groups", "rules"])


def cmd_permission_templates_get(client, args):
    """Template detail, including rules and linked groups."""
    out_json(client.get(f"group-templates/{args.template_id}/", project_scoped=False))


def _template_payload(args):
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if getattr(args, "active", None) is not None:
        payload["is_active"] = args.active
    return _merge_data(payload, args)


def cmd_permission_templates_create(client, args):
    """Create a permission template."""
    payload = _template_payload(args)
    if args.dry_run:
        print(f"Would CREATE permission template: {json.dumps(payload, indent=2)}")
        return
    data = client.post("group-templates/", payload, project_scoped=False)
    _show(args, data, f"Created permission template {data.get('id')}: '{data.get('name')}'")


def cmd_permission_templates_update(client, args):
    """Update a permission template."""
    payload = _template_payload(args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE permission template {args.template_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"group-templates/{args.template_id}/", payload, project_scoped=False)
    _show(args, data, f"Updated permission template {data.get('id')}")


def cmd_permission_templates_delete(client, args):
    """Archive a permission template (the backend sets is_active=false)."""
    if args.dry_run:
        print(f"Would DELETE (archive) permission template {args.template_id}")
        return
    client.delete(f"group-templates/{args.template_id}/", project_scoped=False)
    print(f"Archived permission template {args.template_id}")


cmd_permission_templates_rules = _dispatch(_RULE_HANDLERS, "template_rules_command", "permission-templates rules")


# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY — the current company, its security policy, SSO (read only)
# ═══════════════════════════════════════════════════════════════════════════════


COMPANY_FIELDS = ("name", "description", "website", "industry", "email", "phone")


def cmd_company_get(client, args):
    """Current company detail."""
    data = client.get("", project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    print(f"Company {data.get('id')}: {data.get('name')}")
    for f in ("industry", "website", "email", "phone", "city", "country", "status"):
        print(f"  {f.capitalize() + ':':<10} {data.get(f) or '-'}")
    print()


def cmd_company_update(client, args):
    """Update the current company (company admins only)."""
    payload = {f: getattr(args, f) for f in COMPANY_FIELDS if getattr(args, f) is not None}
    _merge_data(payload, args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE company {client.company_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch("", payload, project_scoped=False)
    _show(args, data, f"Updated company {data.get('id')}: {data.get('name')}")


def cmd_company_security_policy(client, args):
    """The company's security policy (singleton)."""
    out_json(client.get("security-policy/", project_scoped=False))


def cmd_company_sso(client, args):
    """SSO configuration (read only; client_secret is never returned)."""
    out_json(client.get("sso-config/", project_scoped=False))


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRAMS — groupings of projects (ProgramViewSet)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_programs_list(client, args):
    """List programs."""
    params = _list_params(client, args)
    if args.archived is not None:
        params["is_archived"] = str(args.archived).lower()
    if args.owner:
        params["owner"] = args.owner
    data = client.get("programs/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(p.get("id", "")),
        "code": str(p.get("code") or ""),
        "name": str(p.get("name", ""))[:35],
        "owner": str(p.get("owner_display_name") or ""),
        "projects": str(p.get("project_count", "")),
        "pct": str(p.get("percent_complete", "")),
    } for p in results]
    print(f"Programs: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "name", "owner", "projects", "pct"])


def cmd_programs_get(client, args):
    """Program detail."""
    data = client.get(f"programs/{args.program_id}/", project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    print(f"Program {data.get('id')}: {data.get('name')} ({data.get('code') or '-'})")
    print(f"  Owner:     {data.get('owner_display_name') or '-'}")
    print(f"  Projects:  {data.get('project_count', '-')}")
    print(f"  Complete:  {data.get('percent_complete', '-')}%")
    if data.get("description"):
        print(f"  Desc:      {data['description'][:200]}")
    print()


def _program_payload(args):
    payload = {}
    for f in ("name", "code", "description"):
        if getattr(args, f) is not None:
            payload[f] = getattr(args, f)
    return _merge_data(payload, args)


def cmd_programs_create(client, args):
    """Create a program (owner defaults to you)."""
    payload = _program_payload(args)
    if args.dry_run:
        print(f"Would CREATE program: {json.dumps(payload, indent=2)}")
        return
    data = client.post("programs/", payload, project_scoped=False)
    _show(args, data, f"Created program {data.get('id')}: '{data.get('name')}'")


def cmd_programs_update(client, args):
    """Update a program."""
    payload = _program_payload(args)
    _require(payload)
    if args.dry_run:
        print(f"Would UPDATE program {args.program_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"programs/{args.program_id}/", payload, project_scoped=False)
    _show(args, data, f"Updated program {data.get('id')}")


def cmd_programs_delete(client, args):
    """Delete a program."""
    if args.dry_run:
        print(f"Would DELETE program {args.program_id}")
        return
    client.delete(f"programs/{args.program_id}/", project_scoped=False)
    print(f"Deleted program {args.program_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS / NEEDS YOU — /api/notifications/ (outside the company tree)
# ═══════════════════════════════════════════════════════════════════════════════


def _notifications(client, method, suffix="", params=None, payload=None):
    url = f"{client.base_url}/api/notifications/{suffix}"
    return client._request(method, url, params=params, json=payload).json()


def cmd_notifications_list(client, args):
    """Your notifications in the current company, newest first."""
    params = client.paginate_params(args.limit, args.offset)
    params["company"] = client.company_id
    if args.unread:
        params["read"] = "false"
    if args.verb:
        params["verb"] = args.verb
    if args.project_id:
        params["project"] = args.project_id
    data = _notifications(client, "GET", params=params)
    if args.format == "json":
        out_json(data)
        return
    results, total = _results(data)
    rows = [{
        "id": str(n.get("id", "")),
        "verb": str(n.get("verb", "")),
        "project": str(n.get("project") or ""),
        "created": str(n.get("created_at", ""))[:16],
        "read": "yes" if n.get("is_read") else "",
        "data": json.dumps(n.get("data") or {}, default=str)[:50],
    } for n in results]
    print(f"Notifications: {len(rows)} of {total}\n")
    out_table(rows, ["id", "verb", "project", "created", "read", "data"])


def cmd_notifications_unread_count(client, args):
    """Unread and unseen counts (the bell)."""
    out_json(_notifications(client, "GET", "unread_count/", params={"company": client.company_id}))


def cmd_notifications_mark_read(client, args):
    """Mark one notification read (and seen)."""
    if args.dry_run:
        print(f"Would MARK READ notification {args.notification_id}")
        return
    data = _notifications(client, "POST", f"{args.notification_id}/mark_read/", payload={})
    _show(args, data, f"Marked notification {args.notification_id} read")


def cmd_notifications_mark_all_read(client, args):
    """Mark every unread notification in the company (or one project) read."""
    payload = {"company": client.company_id}
    if args.project_id:
        payload["project"] = args.project_id
    if args.dry_run:
        print(f"Would MARK ALL READ: {json.dumps(payload)}")
        return
    data = _notifications(client, "POST", "mark_all_read/", payload=payload)
    _show(args, data, f"Marked {data.get('updated', 0)} notifications read")


def cmd_needs_you_list(client, args):
    """Agent input requests and workflow approvals waiting on you (humans only)."""
    data = _notifications(client, "GET", "needs-you/", params={"company": client.company_id})
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for r in data.get("results", []):
        if r.get("kind") == "chat_request":
            what = r.get("question", "")
            where = (r.get("channel") or {}).get("name") or "DM"
        else:
            sub = r.get("submission") or {}
            what = f"{r.get('stage_name', '')}: {sub.get('form_name', '')} {sub.get('code', '')}"
            where = f"project {sub.get('project_id', '')}"
        rows.append({"kind": r.get("kind", ""), "id": str(r.get("id", "")),
                     "where": str(where)[:25], "what": str(what)[:60]})
    out_table(rows, ["kind", "id", "where", "what"])
    if data.get("has_more"):
        print("\n(more items waiting — the server caps this list)")


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════


def _list_flags(p, sort=True):
    p.add_argument("--search", "-s")
    if sort:
        p.add_argument("--sort", help="Ordering field (prefix - for descending)")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)


def _paging(p):
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)


def _data(p):
    p.add_argument("--data", help="Extra fields as a JSON object, merged last")


def _rule_parsers(sub, parent, parent_help):
    p = sub.add_parser("list", help="List access rules")
    p.add_argument(parent, type=int, help=parent_help)
    _paging(p)

    for name in ("create", "update"):
        p = sub.add_parser(name, help=f"{name.capitalize()} an access rule")
        p.add_argument(parent, type=int, help=parent_help)
        if name == "update":
            p.add_argument("rule_id", type=int)
        p.add_argument("--type", choices=RULE_RESOURCE_TYPES, required=name == "create",
                       help="Resource type")
        p.add_argument("--resource-id", dest="resource_id", type=int,
                       help="Resource id (activity_type, form_template)")
        p.add_argument("--value", help="Resource value: 'all' for photo/file/drawing_filter/..., "
                                       "a form_type value, or '*' (activity_type/form_type/form_template)")
        p.add_argument("--level", type=int, choices=[0, 1, 2, 3], required=name == "create",
                       help="0 Disabled, 1 Read, 2 Edit, 3 Admin")

    p = sub.add_parser("delete", help="Delete an access rule")
    p.add_argument(parent, type=int, help=parent_help)
    p.add_argument("rule_id", type=int)


def _add_company_flag(subparsers_action):
    """Give every leaf subcommand of a company-only group a --company override."""
    for p in subparsers_action.choices.values():
        nested = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if nested:
            _add_company_flag(nested[0])
        else:
            p.add_argument("--company", type=int, help="Company ID (default: the profile/.pcxa company)")


def register_parser(sub):
    # ── members (project memberships) ──
    g = sub.add_parser("members", help="Project member management (roles, admin, access summary)")
    s = g.add_subparsers(dest="members_command")

    p = s.add_parser("list", help="List project members")
    _list_flags(p)
    p.add_argument("--user", type=int, help="Filter by user id")

    p = s.add_parser("get", help="Project membership detail")
    p.add_argument("membership_id", type=int, help="Project membership id (not user id)")

    p = s.add_parser("add", help="Add a company member to the project")
    p.add_argument("--user", type=int, required=True, help="User id (must already be a company member)")
    p.add_argument("--role", required=True, help="Functional role, free text (e.g. 'Project Manager')")
    p.add_argument("--admin", action="store_true", help="Make them a project admin")
    p.add_argument("--groups", help="Permission group ids, comma-separated (ignored for admins)")
    _data(p)

    p = s.add_parser("update", help="Update a project membership")
    p.add_argument("membership_id", type=int, help="Project membership id (not user id)")
    p.add_argument("--role")
    p.add_argument("--admin", type=_bool, metavar="BOOL")
    p.add_argument("--first-name", dest="first_name")
    p.add_argument("--last-name", dest="last_name")
    _data(p)

    p = s.add_parser("remove", help="Remove a member from the project")
    p.add_argument("membership_id", type=int, help="Project membership id (not user id)")

    p = s.add_parser("access", help="Access summary for all members, or one user's detail")
    p.add_argument("user_id", type=int, nargs="?", help="User id (omit for every member)")

    p = s.add_parser("resource-access", help="Company resources enabled in this project (read only)")
    p.add_argument("--enabled", type=_bool, metavar="BOOL")
    _paging(p)

    # ── groups (project permission groups) ──
    g = sub.add_parser("groups", help="Project permission groups (+ members, access rules)")
    s = g.add_subparsers(dest="groups_command")

    p = s.add_parser("list", help="List permission groups")
    _list_flags(p, sort=False)

    p = s.add_parser("get", help="Group detail (members + rules)")
    p.add_argument("group_id", type=int)

    for name in ("create", "update"):
        p = s.add_parser(name, help=f"{name.capitalize()} a permission group")
        if name == "update":
            p.add_argument("group_id", type=int)
        p.add_argument("--name", required=name == "create")
        p.add_argument("--description")
        p.add_argument("--template", type=int, help="Link to a company permission template id (0 unlinks)")
        _data(p)

    p = s.add_parser("delete", help="Delete a permission group")
    p.add_argument("group_id", type=int)

    gm = s.add_parser("members", help="Group members")
    gm_sub = gm.add_subparsers(dest="group_members_command")
    p = gm_sub.add_parser("list", help="List group members")
    p.add_argument("group_id", type=int)
    p.add_argument("--search", "-s")
    _paging(p)
    p = gm_sub.add_parser("add", help="Add a project member to the group")
    p.add_argument("group_id", type=int)
    p.add_argument("--user", type=int, required=True, help="User id (must be a project member)")
    p = gm_sub.add_parser("remove", help="Remove a group membership")
    p.add_argument("group_id", type=int)
    p.add_argument("membership_id", type=int, help="Group-membership id from `groups members list`")

    gr = s.add_parser("rules", help="Group access rules")
    _rule_parsers(gr.add_subparsers(dest="group_rules_command"), "group_id", "Permission group id")

    # ── team (company memberships + invitations) ──
    g = sub.add_parser("team", help="Company members and invitations")
    s = g.add_subparsers(dest="team_command")

    p = s.add_parser("list", help="List company members")
    _list_flags(p)
    p.add_argument("--role", help="Exact role, e.g. 'Company Admin'")
    p.add_argument("--admin", type=_bool, metavar="BOOL")

    p = s.add_parser("get", help="Company membership detail")
    p.add_argument("membership_id", type=int, help="Company membership id (not user id)")

    p = s.add_parser("update", help="Update a company membership")
    p.add_argument("membership_id", type=int, help="Company membership id (not user id)")
    p.add_argument("--role")
    p.add_argument("--admin", type=_bool, metavar="BOOL")
    p.add_argument("--ai-access", dest="ai_access", type=_bool, metavar="BOOL")
    p.add_argument("--first-name", dest="first_name")
    p.add_argument("--last-name", dest="last_name")
    _data(p)

    p = s.add_parser("remove", help="Remove a member from the company")
    p.add_argument("membership_id", type=int, help="Company membership id (not user id)")

    p = s.add_parser("invite", help="Invite by email (SENDS AN EMAIL to the invitee)")
    p.add_argument("--email", required=True, help="Invitee's email address")
    p.add_argument("--role", choices=["Company Member", "Company Admin"], default="Company Member")
    p.add_argument("--admin", action="store_true", help="Company admin on acceptance")
    p.add_argument("--ai-access", dest="ai_access", action="store_true", help="Enable AI access on acceptance")
    p.add_argument("--project-id", dest="project_id", type=int, help="Also grant this project on acceptance")
    p.add_argument("--project-role", dest="project_role", help="Role in --project-id")
    p.add_argument("--project-admin", dest="project_admin", action="store_true",
                   help="Project admin in --project-id")
    p.add_argument("--groups", help="Permission group ids in --project-id, comma-separated")

    inv = s.add_parser("invitations", help="List, cancel or resend invitations")
    inv_sub = inv.add_subparsers(dest="team_invitations_command")
    p = inv_sub.add_parser("list", help="List invitations")
    _list_flags(p, sort=False)
    p.add_argument("--status", choices=["pending", "accepted", "declined", "expired", "cancelled"])
    p = inv_sub.add_parser("revoke", help="Cancel a pending invitation")
    p.add_argument("invitation_id", type=int)
    p = inv_sub.add_parser("resend", help="Re-send a pending invitation email")
    p.add_argument("invitation_id", type=int)
    _add_company_flag(s)

    # ── permission-templates (company group templates) ──
    g = sub.add_parser("permission-templates", help="Company permission templates (+ access rules)")
    s = g.add_subparsers(dest="permission_templates_command")

    p = s.add_parser("list", help="List templates (active only by default)")
    _list_flags(p, sort=False)
    p.add_argument("--active", type=_bool, metavar="BOOL", help="false lists archived templates")

    p = s.add_parser("get", help="Template detail (rules + linked groups)")
    p.add_argument("template_id", type=int)

    for name in ("create", "update"):
        p = s.add_parser(name, help=f"{name.capitalize()} a template")
        if name == "update":
            p.add_argument("template_id", type=int)
            p.add_argument("--active", type=_bool, metavar="BOOL")
        p.add_argument("--name", required=name == "create")
        p.add_argument("--description")
        _data(p)

    p = s.add_parser("delete", help="Archive a template")
    p.add_argument("template_id", type=int)

    tr = s.add_parser("rules", help="Template access rules")
    _rule_parsers(tr.add_subparsers(dest="template_rules_command"), "template_id", "Permission template id")
    _add_company_flag(s)

    # ── company ──
    g = sub.add_parser("company", help="Current company settings (profile, security policy, SSO)")
    s = g.add_subparsers(dest="company_command")
    s.add_parser("get", help="Company detail")
    p = s.add_parser("update", help="Update company profile (company admins)")
    for f in COMPANY_FIELDS:
        p.add_argument(f"--{f}")
    _data(p)
    s.add_parser("security-policy", help="Security policy (read only)")
    s.add_parser("sso", help="SSO configuration (read only)")
    _add_company_flag(s)

    # ── programs ──
    g = sub.add_parser("programs", help="Programs (groupings of projects)")
    s = g.add_subparsers(dest="programs_command")

    p = s.add_parser("list", help="List programs")
    _list_flags(p, sort=False)
    p.add_argument("--archived", type=_bool, metavar="BOOL")
    p.add_argument("--owner", type=int, help="Owner user id")

    p = s.add_parser("get", help="Program detail")
    p.add_argument("program_id", type=int)

    for name in ("create", "update"):
        p = s.add_parser(name, help=f"{name.capitalize()} a program")
        if name == "update":
            p.add_argument("program_id", type=int)
        p.add_argument("--name", required=name == "create")
        p.add_argument("--code", help="Short identifier, unique per company (max 20)")
        p.add_argument("--description")
        _data(p)

    p = s.add_parser("delete", help="Delete a program")
    p.add_argument("program_id", type=int)
    _add_company_flag(s)

    # ── notifications ──
    g = sub.add_parser("notifications", help="Your notifications (the bell)")
    s = g.add_subparsers(dest="notifications_command")
    p = s.add_parser("list", help="List notifications, newest first")
    p.add_argument("--unread", action="store_true", help="Only unread")
    p.add_argument("--verb", help="Exact verb filter")
    p.add_argument("--project-id", dest="project_id", type=int, help="Only this project")
    _paging(p)
    s.add_parser("unread-count", help="Unread / unseen counts")
    p = s.add_parser("mark-read", help="Mark one notification read")
    p.add_argument("notification_id", type=int)
    p = s.add_parser("mark-all-read", help="Mark all unread notifications read")
    p.add_argument("--project-id", dest="project_id", type=int, help="Only this project")
    _add_company_flag(s)

    # ── needs-you ──
    g = sub.add_parser("needs-you", help="What is waiting on you: agent questions + workflow approvals")
    s = g.add_subparsers(dest="needs_you_command")
    s.add_parser("list", help="List items waiting on you")
    _add_company_flag(s)


SUB_HANDLERS = {
    "members": {
        "list": cmd_members_list,
        "get": cmd_members_get,
        "add": cmd_members_add,
        "update": cmd_members_update,
        "remove": cmd_members_remove,
        "access": cmd_members_access,
        "resource-access": cmd_members_resource_access,
    },
    "groups": {
        "list": cmd_groups_list,
        "get": cmd_groups_get,
        "create": cmd_groups_create,
        "update": cmd_groups_update,
        "delete": cmd_groups_delete,
        "members": cmd_groups_members,
        "rules": cmd_groups_rules,
    },
    "team": {
        "list": cmd_team_list,
        "get": cmd_team_get,
        "update": cmd_team_update,
        "remove": cmd_team_remove,
        "invite": cmd_team_invite,
        "invitations": cmd_team_invitations,
    },
    "permission-templates": {
        "list": cmd_permission_templates_list,
        "get": cmd_permission_templates_get,
        "create": cmd_permission_templates_create,
        "update": cmd_permission_templates_update,
        "delete": cmd_permission_templates_delete,
        "rules": cmd_permission_templates_rules,
    },
    "company": {
        "get": cmd_company_get,
        "update": cmd_company_update,
        "security-policy": cmd_company_security_policy,
        "sso": cmd_company_sso,
    },
    "programs": {
        "list": cmd_programs_list,
        "get": cmd_programs_get,
        "create": cmd_programs_create,
        "update": cmd_programs_update,
        "delete": cmd_programs_delete,
    },
    "notifications": {
        "list": cmd_notifications_list,
        "unread-count": cmd_notifications_unread_count,
        "mark-read": cmd_notifications_mark_read,
        "mark-all-read": cmd_notifications_mark_all_read,
    },
    "needs-you": {
        "list": cmd_needs_you_list,
    },
}

SUB_COMMAND_KEYS = {
    "members": "members_command",
    "groups": "groups_command",
    "team": "team_command",
    "permission-templates": "permission_templates_command",
    "company": "company_command",
    "programs": "programs_command",
    "notifications": "notifications_command",
    "needs-you": "needs_you_command",
}

COMPANY_ONLY = {"team", "permission-templates", "company", "programs", "notifications", "needs-you"}
