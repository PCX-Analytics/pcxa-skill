"""`pcxa api` — call any PCXA REST route with the CLI's auth.

The escape hatch for whatever the web UI does that has no dedicated command
yet: same JWT, auto-refresh, timeout and error reporting as every other
command, so nobody needs a hand-rolled HTTP client (the fleet's
`plan_fields.py` and `pcxa_sync` exist only because this did not).

Paths are relative to the current project by default
(`/api/companies/{c}/projects/{p}/`), to the company with `--company-level`,
or absolute when they start with `/api/`.
"""

import json
import sys

from pcxa._output import out_json

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _parse_params(pairs):
    params = {}
    for pair in pairs or []:
        if "=" not in pair:
            print(f"--param expects key=value, got {pair!r}", file=sys.stderr)
            sys.exit(1)
        key, value = pair.split("=", 1)
        params.setdefault(key, []).append(value)
    # A key given once is sent as a scalar; repeated keys stay a list (?a=1&a=2).
    return {k: v[0] if len(v) == 1 else v for k, v in params.items()}


def _load_body(args):
    raw = args.data
    if args.data_file:
        with open(args.data_file, encoding="utf-8") as fh:
            raw = fh.read()
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Request body is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_api(client, args):
    """Send one request and print the JSON reply."""
    method = args.method.upper()
    path = args.path
    if path.startswith("/api/"):
        url = client.base_url + path
    else:
        url = client._url(path.lstrip("/"), project_scoped=not args.company_level)
    params = _parse_params(args.param)
    body = _load_body(args)
    if method in WRITE_METHODS and args.dry_run:
        print(f"Would {method} {url}" + (f" params={params}" if params else "")
              + (f": {json.dumps(body, indent=2)}" if body is not None else ""))
        return
    kwargs = {}
    if params:
        kwargs["params"] = params
    if body is not None:
        kwargs["json"] = body
    resp = client._request(method, url, **kwargs)
    if resp.status_code == 204 or not resp.content:
        out_json({"status": resp.status_code})
        return
    try:
        out_json(resp.json())
    except ValueError:
        print(resp.text)
