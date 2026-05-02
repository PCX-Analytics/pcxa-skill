#!/usr/bin/env python3
"""
pcxa - Unified CLI for the PCXA construction intelligence platform.

Combines file search/reading, file organization (tags, folders), and
project management (activities, steps, progress, dependencies).

Setup:
    python pcxa.py setup --url https://www.pcxa.app -u USER --password PASS

File exploration:
    python pcxa.py files list --ext PDF --limit 50
    python pcxa.py files search "structural defects" --limit 10
    python pcxa.py files read 123 --outline

File organization:
    python pcxa.py tags list
    python pcxa.py tags add 1 2 3 --tags urgent
    python pcxa.py folders tree
    python pcxa.py move 1 2 3 --folder 5

Project management:
    python pcxa.py activities list --status in_progress
    python pcxa.py activities create --title "Review drawings"
    python pcxa.py steps list 123
    python pcxa.py progress add 123 --percent 50

Config:
    Per-repo: <repo>/.pcxa-credentials.json (when a .pcxa file is found in an ancestor)
    Global:   ~/.file_explorer/config.json (fallback)
"""

import argparse
import datetime
import difflib
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

try:
    import requests
except ImportError:
    print("Error: requests library required.\n  pip install requests", file=sys.stderr)
    sys.exit(1)

# ─── Config ──────────────────────────────────────────────────────────────────

__version__ = "0.2.1"

GLOBAL_CONFIG_DIR = Path.home() / ".file_explorer"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.json"
LOCAL_CONFIG_NAME = ".pcxa"  # repo-level project override (committed)
LOCAL_CREDENTIALS_NAME = ".pcxa-credentials.json"  # repo-level credentials (gitignored)
UPDATE_CHECK_FILE = GLOBAL_CONFIG_DIR / "last_update_check.json"
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
GITHUB_RELEASES_URL = "https://api.github.com/repos/PCX-Analytics/pcxa-skill/releases/latest"
GITHUB_REPO_URL = "https://github.com/PCX-Analytics/pcxa-skill.git"

KNOWN_FILE_TYPES = [
    "PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "TXT", "CSV",
    "DWG", "DXF", "IFC", "RVT", "SKP", "3DS", "OBJ", "STL", "STEP",
    "JPG", "JPEG", "PNG", "GIF", "BMP", "TIFF", "SVG", "WEBP",
    "MP3", "MP4", "WAV", "AVI", "MOV", "MKV", "FLV",
    "ZIP", "RAR", "7Z", "TAR", "GZ",
    "XER", "MPP", "XML", "JSON", "YAML", "MD", "HTML",
]


def find_local_config_path():
    """Walk up from CWD looking for a .pcxa repo-level config file.

    Returns the Path to the .pcxa file, or None if not found.
    """
    current = Path.cwd()
    home = Path.home()
    while True:
        candidate = current / LOCAL_CONFIG_NAME
        if candidate.exists():
            return candidate
        if current == home or current == current.parent:
            return None
        current = current.parent


def find_git_root():
    """Walk up from CWD looking for a .git directory or file (handles worktrees).

    Returns the Path of the git root, or None if not inside a git repo.
    """
    current = Path.cwd()
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def resolve_credentials_path():
    """Pick where to read/write credentials.

    Priority (first match wins):
      1. <ancestor with .pcxa>/.pcxa-credentials.json — explicit project marker
      2. <git-root>/.pcxa-credentials.json            — automatic per-repo isolation
      3. ~/.file_explorer/config.json                  — global fallback (outside any repo)

    Returns (path, source) where source is "pcxa", "git", or "global".
    """
    local = find_local_config_path()
    if local is not None:
        return local.parent / LOCAL_CREDENTIALS_NAME, "pcxa"
    git_root = find_git_root()
    if git_root is not None:
        return git_root / LOCAL_CREDENTIALS_NAME, "git"
    return GLOBAL_CONFIG_FILE, "global"


def get_config_file():
    """Return the credentials config path. Prefer per-repo over global."""
    path, _ = resolve_credentials_path()
    return path


def load_config():
    path = get_config_file()
    if path.exists():
        return json.loads(path.read_text())
    return {"default_profile": "local", "profiles": {}}


def save_config(config):
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows NTFS doesn't support Unix permissions


def find_local_config():
    """Read repo-level .pcxa config. Returns {} if not found or unreadable."""
    path = find_local_config_path()
    if path is None:
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def get_profile(config, name=None):
    local = find_local_config()
    profiles = config.get("profiles", {})
    # Resolution order: explicit --profile > .pcxa user (email match) > user default
    if not name and local.get("user"):
        for pname, p in profiles.items():
            if p.get("username", "").lower() == local["user"].lower():
                name = pname
                break
    name = name or config.get("default_profile", "local")
    profile = profiles.get(name)
    if not profile:
        print(f"Profile '{name}' not found. Run: pcxa setup --help", file=sys.stderr)
        available = list(config.get("profiles", {}).keys())
        if available:
            print(f"Available profiles: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)

    # Apply repo-level overrides from .pcxa (copy so we don't mutate user config)
    if local.get("company") or local.get("project"):
        profile = dict(profile)
        if local.get("company"):
            profile["company"] = local["company"]
        if local.get("project"):
            profile["project"] = local["project"]

    return name, profile


# ─── API Client ──────────────────────────────────────────────────────────────


class APIClient:
    """HTTP client for pcxa REST API with JWT auth and auto-refresh."""

    def __init__(self, profile, profile_name, config):
        self.profile = profile
        self.profile_name = profile_name
        self.config = config
        self.base_url = profile["url"].rstrip("/")
        self.company_id = profile.get("company")
        self.project_id = profile.get("project")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self._set_auth()

    def _set_auth(self):
        auth_mode = self.profile.get("auth", "dev")
        if auth_mode == "dev":
            self.session.headers["Authorization"] = "Bearer dev"
        elif auth_mode == "jwt":
            token = self.profile.get("access_token")
            if token:
                self.session.headers["Authorization"] = f"Bearer {token}"
            else:
                print("No access token. Run: pcxa setup", file=sys.stderr)
                sys.exit(1)

    def _refresh_token(self):
        refresh = self.profile.get("refresh_token")
        if not refresh:
            return False
        try:
            # Use a clean request without the expired Authorization header
            resp = requests.post(
                f"{self.base_url}/api/accounts/token/refresh/",
                json={"refresh": refresh},
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.profile["access_token"] = data["access"]
                if "refresh" in data:
                    self.profile["refresh_token"] = data["refresh"]
                self.config["profiles"][self.profile_name] = self.profile
                save_config(self.config)
                self.session.headers["Authorization"] = f"Bearer {data['access']}"
                return True
            else:
                print(f"Token refresh failed ({resp.status_code}). Run: pcxa setup -u YOUR_EMAIL", file=sys.stderr)
        except Exception as e:
            print(f"Token refresh error: {e}", file=sys.stderr)
        return False

    def _url(self, path, project_scoped=True):
        if project_scoped:
            return (
                f"{self.base_url}/api/companies/{self.company_id}"
                f"/projects/{self.project_id}/{path}"
            )
        return f"{self.base_url}/api/companies/{self.company_id}/{path}"

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code in (401, 403) and self.profile.get("auth") == "jwt":
            # Check if it's a token expiry (vs a real permission error)
            try:
                body = resp.json()
            except Exception:
                body = {}
            if body.get("code") == "token_not_valid" or resp.status_code == 401:
                if self._refresh_token():
                    resp = self.session.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

    def get(self, path, params=None, project_scoped=True):
        url = self._url(path, project_scoped=project_scoped)
        return self._request("GET", url, params=params).json()

    def post(self, path, json_data=None, project_scoped=True):
        return self._request("POST", self._url(path, project_scoped=project_scoped), json=json_data).json()

    def patch(self, path, json_data=None, project_scoped=True):
        return self._request("PATCH", self._url(path, project_scoped=project_scoped), json=json_data).json()

    def delete(self, path, json_data=None, project_scoped=True):
        resp = self._request("DELETE", self._url(path, project_scoped=project_scoped), json=json_data)
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    def get_raw(self, url, params=None):
        return self._request("GET", url, params=params).json()

    @staticmethod
    def paginate_params(limit, offset=0):
        """Convert limit/offset to page_size/page for DRF PageNumberPagination."""
        params = {"page_size": limit}
        if offset > 0:
            page = (offset // limit) + 1
            params["page"] = page
        return params

    def get_all_pages(self, path, params=None, max_pages=50, project_scoped=True):
        params = dict(params or {})
        all_results = []
        page = 0
        while True:
            data = self.get(path, params, project_scoped=project_scoped)
            if isinstance(data, list):
                return data
            all_results.extend(data.get("results", []))
            page += 1
            if page >= max_pages:
                break
            next_url = data.get("next")
            if not next_url:
                break
            parsed = parse_qs(urlparse(next_url).query)
            if "offset" in parsed:
                params["offset"] = parsed["offset"][0]
            elif "page" in parsed:
                params["page"] = parsed["page"][0]
            else:
                break
        return all_results

    def get_count(self, path, params=None):
        params = dict(params or {})
        params["page_size"] = 1
        data = self.get(path, params)
        return data.get("count", 0) if isinstance(data, dict) else len(data)

    def file_url(self, file_id, highlight=None, chunk=None):
        frontend = self.profile.get("frontend_url", self.base_url)
        url = (
            f"{frontend.rstrip('/')}/company/{self.company_id}"
            f"/project/{self.project_id}/files/view/{file_id}"
        )
        params = []
        if chunk is not None:
            params.append(f"chunk={chunk}")
        if highlight:
            snippet = highlight.strip().strip(".").strip()[:120].strip()
            if snippet:
                params.append(f"highlight={quote(snippet)}")
        if params:
            url += "?" + "&".join(params)
        return url


# ─── Output Helpers ──────────────────────────────────────────────────────────


def out_json(data):
    print(json.dumps(data, indent=2, default=str))


def out_table(rows, columns):
    if not rows:
        print("No results.")
        return
    widths = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))
    header = " | ".join(col.ljust(widths[col]) for col in columns)
    sep = "-+-".join("-" * widths[col] for col in columns)
    print(header)
    print(sep)
    for row in rows:
        print(" | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


def fmt_size(b):
    if b is None:
        return "-"
    for u in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}TB"


def tag_names(tags):
    if not tags:
        return ""
    return ",".join(
        t.get("name", t) if isinstance(t, dict) else str(t) for t in tags
    )


# ─── Setup ───────────────────────────────────────────────────────────────────


def cmd_login(args):
    """Browser-based login — opens pcxa.app and captures tokens via local callback."""
    import secrets
    import socket
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    def find_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = find_free_port()
    state = secrets.token_urlsafe(16)
    result: dict = {}
    done = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                params = parse_qs(parsed.query, keep_blank_values=True)
                if params.get("state", [""])[0] != state:
                    self._html(400, "<h2>State mismatch — please run <code>pcxa login</code> again.</h2>")
                    done.set()
                    return
                error = params.get("error", [None])[0]
                if error:
                    result["error"] = error
                    self._html(400, f"<h2>Authorization failed: {error}</h2><p>Return to your terminal.</p>")
                else:
                    result["access"] = params.get("access", [""])[0]
                    result["refresh"] = params.get("refresh", [""])[0]
                    result["company"] = params.get("company", [None])[0]
                    result["username"] = params.get("username", [""])[0]
                    self._html(200, """<html><head><style>
                        body{font-family:system-ui,-apple-system,sans-serif;display:flex;
                        align-items:center;justify-content:center;min-height:100vh;
                        margin:0;background:#f4f4f5}
                        .card{text-align:center;padding:2.5rem 3rem;background:#fff;
                        border-radius:12px;box-shadow:0 2px 16px rgba(0,0,0,.08)}
                        h2{color:#16a34a;margin:0 0 .5rem;font-size:1.25rem}
                        p{color:#71717a;margin:0;font-size:.9rem}
                        </style></head><body>
                        <div class="card">
                          <h2>&#10003; CLI authorized</h2>
                          <p>You can close this tab and return to your terminal.</p>
                        </div></body></html>""")
                done.set()
            else:
                self._html(404, "<h2>Not found</h2>")

        def _html(self, code, body):
            body_bytes = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def log_message(self, *_args):
            pass  # suppress access logs

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    frontend_url = (args.frontend_url or "https://www.pcxa.app").rstrip("/")
    api_url = (args.url or "https://api.pcxa.app").rstrip("/")
    auth_url = f"{frontend_url}/auth/cli-auth?port={port}&state={state}"

    print("Opening browser to authenticate...")
    print(f"  If your browser does not open automatically, visit:\n  {auth_url}")
    webbrowser.open(auth_url)

    timeout = getattr(args, "timeout", 120) or 120
    if not done.wait(timeout=timeout):
        server.shutdown()
        print("Timed out waiting for browser authentication.", file=sys.stderr)
        sys.exit(1)

    server.shutdown()

    if result.get("error"):
        print(f"Authorization error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    if not result.get("access"):
        print("No token received — authentication may have been cancelled.", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    name = args.profile or "prod"
    profile = config.get("profiles", {}).get(name, {})
    profile["url"] = api_url
    profile["frontend_url"] = frontend_url
    profile["auth"] = "jwt"
    profile["access_token"] = result["access"]
    profile["refresh_token"] = result["refresh"]
    if result.get("username"):
        profile["username"] = result["username"]
    if result.get("company"):
        try:
            profile["company"] = int(result["company"])
        except (ValueError, TypeError):
            pass

    config.setdefault("profiles", {})[name] = profile
    config["default_profile"] = name
    save_config(config)

    user_str = result.get("username") or "unknown"
    print(f"Logged in as {user_str}. Profile '{name}' saved.")
    if profile.get("company"):
        print(f"  Company ID: {profile['company']}")
    print(f"  Config: {get_config_file()}")


def cmd_setup(args):
    """Profile setup and login."""
    import getpass

    config = load_config()
    name = args.profile or "prod"

    profile = config.get("profiles", {}).get(name, {})
    profile["url"] = args.url.rstrip("/")
    profile["auth"] = "jwt"
    profile["frontend_url"] = args.frontend_url or "https://www.pcxa.app"

    if args.company:
        profile["company"] = args.company
    if args.project:
        profile["project"] = args.project

    password = args.password or getpass.getpass("Password: ")

    try:
        resp = requests.post(
            f"{profile['url']}/api/accounts/login/",
            json={"username": args.username, "password": password},
            timeout=15,
        )
    except requests.ConnectionError:
        print(f"Cannot connect to {args.url}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Login failed ({resp.status_code}): {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("mfa_required"):
        print("MFA required — not supported in CLI.", file=sys.stderr)
        sys.exit(1)

    profile["access_token"] = data["access"]
    profile["refresh_token"] = data["refresh"]
    profile["username"] = args.username

    dc = data.get("default_company")
    if dc and not profile.get("company"):
        profile["company"] = dc["id"]
        print(f"Company auto-detected: {dc.get('name', '?')} (id={dc['id']})")

    config.setdefault("profiles", {})[name] = profile
    config["default_profile"] = name
    save_config(config)

    print(f"Profile '{name}' saved. Authenticated as {args.username}")
    print(f"  URL:     {profile['url']}")
    print(f"  Company: {profile.get('company', 'auto-detect')}")
    print(f"  Project: {profile.get('project', 'auto-detect')}")
    print(f"  Config:  {get_config_file()}")


def cmd_whoami(client, args):
    """Show current profile and auth status."""
    config = load_config()
    local = find_local_config()
    profiles = config.get("profiles", {})
    if not profiles:
        print("No profiles configured. Run: pcxa setup -u YOUR_EMAIL")
        return

    name = args.profile
    profile_src = ""
    if not name and local.get("user"):
        for pname, p in profiles.items():
            if p.get("username", "").lower() == local["user"].lower():
                name = pname
                profile_src = f" (matched .pcxa user {local['user']})"
                break
        if not name:
            profile_src = f" (no profile matches .pcxa user {local['user']}; using default)"
    name = name or config.get("default_profile", "local")
    if name not in profiles:
        print(f"Profile '{name}' not found. Run: pcxa setup -u YOUR_EMAIL")
        return

    p = profiles[name]

    company = local.get("company") or p.get("company", "not set")
    project = local.get("project") or p.get("project", "not set")
    company_src = " (from .pcxa)" if local.get("company") else ""
    project_src = " (from .pcxa)" if local.get("project") else ""

    creds_path, creds_src = resolve_credentials_path()
    src_label = {
        "pcxa": "per-repo (.pcxa marker)",
        "git": "per-repo (git root)",
        "global": "global fallback",
    }[creds_src]

    print(f"Active profile: {name}{profile_src}")
    print(f"  URL:     {p.get('url')}")
    print(f"  Auth:    {p.get('auth')}")
    if p.get("username"):
        print(f"  User:    {p.get('username')}")
    print(f"  Company: {company}{company_src}")
    print(f"  Project: {project}{project_src}")
    print(f"  Token:   {'cached' if p.get('access_token') else 'none'}")
    print(f"  Creds:   {creds_path}  [{src_label}]")

    # If authenticated but missing company/project, list available ones
    if p.get("access_token") and (not p.get("company") or not p.get("project")):
        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {p['access_token']}"
        base = p["url"].rstrip("/")
        try:
            if not p.get("company"):
                data = session.get(f"{base}/api/companies/", timeout=10).json()
                companies = data.get("results", data) if isinstance(data, dict) else data
                print(f"\n  Available companies:")
                for c in companies:
                    print(f"    {c['id']}: {c.get('name', '?')}")
                print(f"\n  Set with: pcxa setup -u {p.get('username', 'EMAIL')} --company ID")
            elif not p.get("project"):
                data = session.get(f"{base}/api/companies/{p['company']}/projects/", timeout=10).json()
                projects = data.get("results", data) if isinstance(data, dict) else data
                print(f"\n  Available projects:")
                for proj in projects:
                    print(f"    {proj['id']}: {proj.get('name', '?')}")
                print(f"\n  Set with: pcxa setup -u {p.get('username', 'EMAIL')} --project ID")
        except Exception:
            pass


def cmd_set_project(args):
    """Set the default project — globally in user config, or locally in .pcxa."""
    if getattr(args, "local", False):
        local_file = Path.cwd() / LOCAL_CONFIG_NAME
        local_cfg = {}
        if local_file.exists():
            try:
                local_cfg = json.loads(local_file.read_text())
            except Exception:
                pass
        local_cfg["project"] = args.project_id
        if getattr(args, "company", None):
            local_cfg["company"] = args.company
        if getattr(args, "user", None):
            local_cfg["user"] = args.user
        local_file.write_text(json.dumps(local_cfg, indent=2))
        print(f"Repo-level config written to {local_file}")
        print(f"  Project: {args.project_id}")
        if local_cfg.get("company"):
            print(f"  Company: {local_cfg['company']}")
        if local_cfg.get("user"):
            print(f"  User:    {local_cfg['user']}")
    else:
        config = load_config()
        name = args.profile or config.get("default_profile", "local")
        if name not in config.get("profiles", {}):
            print(f"Profile '{name}' not found. Run: pcxa setup -u YOUR_EMAIL", file=sys.stderr)
            sys.exit(1)
        config["profiles"][name]["project"] = args.project_id
        save_config(config)
        print(f"Default project set to {args.project_id} (global)")


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT — View and update project metadata
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_project_get(client, args):
    """Show project details."""
    url = client._url("")
    data = client._request("GET", url).json()
    if args.format == "json":
        out_json(data)
    else:
        rows = []
        display_fields = [
            "id", "name", "code", "industry", "description", "scope_statement",
            "life_cycle", "start_date", "end_date", "percent_complete",
            "progress_input_method", "rollup_method", "owner_username",
            "company_name", "is_archived", "created_at", "updated_at",
        ]
        for key in display_fields:
            val = data.get(key, "")
            if val is None:
                val = ""
            rows.append({"field": key, "value": str(val)})
        out_table(rows, ["field", "value"])


def cmd_project_update(client, args):
    """Update project metadata."""
    payload = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.code is not None:
        payload["code"] = args.code or None
    if args.description is not None:
        payload["description"] = args.description
    if args.scope_statement is not None:
        payload["scope_statement"] = args.scope_statement
    if args.industry is not None:
        payload["industry"] = args.industry
    if args.life_cycle is not None:
        payload["life_cycle"] = args.life_cycle
    if args.start_date is not None:
        payload["start_date"] = args.start_date or None
    if args.end_date is not None:
        payload["end_date"] = args.end_date or None
    if args.progress_input_method is not None:
        payload["progress_input_method"] = args.progress_input_method
    if args.rollup_method is not None:
        payload["rollup_method"] = args.rollup_method
    if not payload:
        print("Nothing to update — provide at least one field.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE project: {json.dumps(payload, indent=2)}")
        return
    url = client._url("")
    data = client._request("PATCH", url, json=payload).json()
    if args.format == "json":
        out_json(data)
    else:
        print(f"Project updated: {data.get('name')}")


def cmd_project_members(client, args):
    """List project members with user IDs."""
    params = {"limit": 100}
    if args.search:
        params["search"] = args.search
    data = client.get("memberships/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for m in results:
        if m.get("is_ai_agent"):
            continue
        rows.append({
            "user_id": m.get("user"),
            "name": m.get("user_name", ""),
            "username": m.get("user_username", ""),
            "email": m.get("user_email", ""),
            "role": m.get("role", ""),
        })
    print(f"Project members: {len(rows)}\n")
    out_table(rows, ["user_id", "name", "username", "email", "role"])


# ═══════════════════════════════════════════════════════════════════════════════
# FILES — Search, explore, read
# ═══════════════════════════════════════════════════════════════════════════════


def _file_row(f):
    return {
        "id": str(f.get("id", "")),
        "title": str(f.get("title", ""))[:55],
        "type": f.get("file_type", ""),
        "folder": (f.get("folder_info") or {}).get("full_path", "/"),
        "size": fmt_size(f.get("file_size")),
        "created": str(f.get("created_at", ""))[:10],
        "tags": tag_names(f.get("tags"))[:25],
    }


def cmd_files_list(client, args):
    """List/filter files by metadata."""
    params = client.paginate_params(args.limit, args.offset)
    if args.ext:
        types = [t.strip().upper() for t in args.ext.split(",")]
        if len(types) == 1:
            params["file_type"] = types[0]
    if args.tags:
        params["tags"] = args.tags
        if getattr(args, "tags_mode", None):
            params["tags_mode"] = args.tags_mode
    if args.folder:
        params["folder"] = args.folder
    if args.category:
        params["category"] = args.category
    if args.search:
        params["search"] = args.search
    if args.index_status:
        params["search_status"] = args.index_status
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("files/", params)}))
        return

    data = client.get("files/", params)
    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", data) if isinstance(data, dict) else data
        total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
        rows = [_file_row(f) for f in results]
        print(f"Files: {len(rows)} of {total}\n")
        out_table(rows, ["id", "title", "type", "folder", "size", "created", "tags"])


def cmd_files_search(client, args):
    """Semantic vector search."""
    params = {"q": args.query, "limit": args.limit}
    if args.types:
        params["source_types"] = args.types
    if args.ext:
        params["file_types"] = args.ext

    data = client.get("semantic-search/search/", params)
    for r in data.get("results", []):
        fid = r.get("file_id")
        if fid:
            r["url"] = client.file_url(fid, highlight=args.query)

    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", [])
        print(f"Search: '{data.get('query', args.query)}' — {data.get('total_results', len(results))} results\n")
        rows = []
        for r in results:
            row = {
                "score": f"{r.get('score', 0):.3f}",
                "id": str(r.get("file_id") or r.get("object_id", "")),
                "name": (r.get("file_name") or r.get("file_title") or r.get("title", ""))[:45],
                "type": r.get("file_type", ""),
                "url": r.get("url", ""),
            }
            if args.show_content and r.get("content"):
                row["content"] = r["content"][:100]
            rows.append(row)
        cols = ["score", "id", "name", "type"]
        if args.show_content:
            cols.append("content")
        cols.append("url")
        out_table(rows, cols)


def cmd_files_content(client, args):
    """Keyword search in indexed file text."""
    try:
        params = {"q": args.query, "limit": args.limit, "offset": args.offset}
        if args.ext:
            params["file_types"] = args.ext
        if args.folder:
            params["folder"] = args.folder
        data = client.get("semantic-search/content-search/", params)
        for r in data.get("results", []):
            fid = r.get("file_id")
            if fid:
                r["url"] = client.file_url(fid, highlight=args.query)
        if args.format == "json":
            out_json(data)
        else:
            results = data.get("results", [])
            total = data.get("total_results", len(results))
            print(f"Content matches for '{args.query}': {total}\n")
            rows = []
            for r in results:
                rows.append({
                    "file_id": str(r.get("file_id", "")),
                    "name": str(r.get("file_name", ""))[:40],
                    "page": str(r.get("page_number", "-")),
                    "match": str(r.get("content", ""))[:70],
                    "url": r.get("url", ""),
                })
            out_table(rows, ["file_id", "name", "page", "match", "url"])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            # Fallback to title search
            params = {"search": args.query, "page_size": args.limit}
            data = client.get("files/", params)
            if args.format == "json":
                out_json(data)
            else:
                results = data.get("results", [])
                rows = [_file_row(f) for f in results]
                print(f"Title matches (content search unavailable): {len(rows)}\n")
                out_table(rows, ["id", "title", "type", "folder"])
        else:
            raise


def cmd_files_read(client, args):
    """Read file content from indexed chunks."""
    params = {}
    if args.outline:
        params["outline"] = "true"
    else:
        if args.all:
            params["window"] = 50
            params["start"] = 0
        else:
            params["window"] = args.window
            if args.start is not None:
                params["start"] = args.start
            if args.end is not None:
                params["end"] = args.end

    data = client.get(f"semantic-search/file-content/{args.file_id}/", params)
    file_id = data.get("file_id", args.file_id)
    for m in data.get("chunks_meta", []):
        m["url"] = client.file_url(file_id, chunk=m.get("index"))

    if args.format == "json":
        out_json(data)
        return

    print(f"File: {data.get('file_title')} (id={file_id})")
    print(f"Type: {data.get('file_type')}  Chunks: {data.get('total_chunks')}  Chars: {data.get('total_chars', '?'):,}")
    summary = data.get("document_summary")
    if summary:
        print(f"\nSummary: {summary[:300]}")

    if args.outline:
        sections = data.get("sections", [])
        print(f"\nSections ({len(sections)}):")
        for s in sections:
            path = " > ".join(s.get("path", [])) or s.get("title", "?")
            chunks = s.get("chunks", [])
            chunk_range = f"chunks {chunks[0]}-{chunks[-1]}" if chunks else ""
            print(f"  {path}")
            print(f"    {chunk_range}  ({s.get('chars', 0):,} chars)")
    else:
        w = data.get("window", {})
        print(f"\n--- Chunks {w.get('start', 0)}-{w.get('end', '?')} of {data.get('total_chunks', '?')} ---\n")
        print(data.get("content", ""))
        if w.get("has_more"):
            print(f"\n--- More available. Next: --start {w.get('next_start')} ---")
        meta = data.get("chunks_meta", [])
        if meta and not args.all:
            print(f"\nChunk details:")
            rows = [{"idx": str(m["index"]), "page": str(m.get("page") or "-"),
                      "section": (m.get("section") or "")[:35], "chars": str(m.get("chars", 0)),
                      "url": m.get("url", "")} for m in meta]
            out_table(rows, ["idx", "page", "section", "chars", "url"])


def cmd_files_info(client, args):
    """Detailed file metadata."""
    data = client.get(f"files/{args.file_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"File {data.get('id')}: {data.get('title')}")
    print(f"  Type:     {data.get('file_type')}")
    print(f"  Size:     {fmt_size(data.get('file_size'))}")
    print(f"  Category: {data.get('category') or '-'}")
    fi = data.get("folder_info") or {}
    print(f"  Folder:   {fi.get('full_path') or '/'} (id={data.get('folder') or 'root'})")
    print(f"  Tags:     {tag_names(data.get('tags')) or '-'}")
    cb = data.get("created_by") or {}
    print(f"  Created:  {str(data.get('created_at', ''))[:10]} by {cb.get('username', '-')}")
    print(f"  Index:    {data.get('search_status', '-')}")
    desc = data.get("description") or ""
    if desc:
        print(f"  Desc:     {desc[:200]}")
    versions = data.get("versions") or []
    if versions:
        print(f"\n  Versions ({len(versions)}):")
        for v in versions:
            meta = v.get("file_metadata") or {}
            print(f"    v{v.get('version_number')}: {meta.get('original_filename', '-')} ({fmt_size(meta.get('size'))}) - {str(v.get('created_at', ''))[:10]}")


def cmd_files_stats(client, args):
    """Project file statistics."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stats = {"total_files": client.get_count("files/")}

    try:
        stats["indexing"] = client.get("semantic-search/status/")
    except requests.HTTPError:
        stats["indexing"] = None

    type_counts = {}
    def _count_type(ft):
        try:
            return ft, client.get_count("files/", {"file_type": ft})
        except Exception:
            return ft, None
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_count_type, ft) for ft in KNOWN_FILE_TYPES]
        for fut in as_completed(futs):
            ft, c = fut.result()
            if c and c > 0:
                type_counts[ft] = c
    stats["by_type"] = dict(sorted(type_counts.items(), key=lambda x: -x[1]))

    if args.format == "json":
        out_json(stats)
        return

    print(f"Total files: {stats['total_files']:,}\n")
    idx = stats.get("indexing")
    if isinstance(idx, dict):
        print("Indexing:")
        for k, v in idx.items():
            if isinstance(v, (int, float)):
                print(f"  {k}: {v:,}")
        print()
    by_type = stats.get("by_type")
    if by_type:
        print(f"File types ({len(by_type)}):")
        for ft, count in by_type.items():
            bar = "#" * min(50, max(1, int(count / max(stats["total_files"], 1) * 50)))
            print(f"  {ft:8s} {count:>8,}  {bar}")


def cmd_files_aggregate(client, args):
    """Aggregate file counts by dimension."""
    params = {"group_by": args.group_by}
    if args.ext:
        params["file_type"] = args.ext.upper()
    if args.tags:
        params["tags"] = args.tags
    if args.folder:
        params["folder"] = args.folder
    if args.search:
        params["search"] = args.search
    if args.top:
        params["top"] = args.top

    data = client.get("files/aggregate/", params)
    if args.format == "json":
        out_json(data)
        return

    total = data.get("total_matching", 0)
    groups = data.get("groups", [])
    print(f"Total: {total:,} — grouped by: {data.get('group_by')}\n")
    rows = []
    for g in groups:
        row = {"value": str(g.get("value", ""))[:45], "count": str(g.get("count", 0))}
        if "id" in g:
            row["id"] = str(g["id"] or "")
        pct = (g["count"] / total * 100) if total else 0
        row["pct"] = f"{pct:.1f}%"
        rows.append(row)
    cols = ["value"]
    if any("id" in r for r in rows):
        cols.append("id")
    cols.extend(["count", "pct"])
    out_table(rows, cols)


def cmd_files_recent(client, args):
    """Recently uploaded files."""
    params = {"page_size": args.limit, "ordering": "-created_at"}
    if args.ext:
        params["file_type"] = args.ext.upper()
    if args.folder:
        params["folder"] = args.folder
    data = client.get("files/", params)
    if args.format == "json":
        out_json(data)
    else:
        results = data.get("results", data) if isinstance(data, dict) else data
        rows = [_file_row(f) for f in results]
        print(f"Recent files ({len(rows)}):\n")
        out_table(rows, ["id", "title", "type", "folder", "size", "created"])


def cmd_files_download(client, args):
    """Download a file to disk via presigned URL."""
    file_data = client.get(f"files/{args.file_id}/")
    current_version = file_data.get("current_version")
    if not current_version:
        print(f"File {args.file_id} has no versions to download.", file=sys.stderr)
        sys.exit(1)

    version_id = current_version["id"]
    dl = client.get(f"files/{args.file_id}/versions/{version_id}/presign-download/")
    presigned_url = dl.get("url")
    if not presigned_url:
        print("Could not get download URL from API.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
    else:
        meta = current_version.get("file_metadata") or {}
        filename = meta.get("original_filename") or file_data.get("title", f"file_{args.file_id}")
        out_path = Path(filename)

    print(f"Downloading: {file_data.get('title')} (v{current_version.get('version_number', '?')})")
    print(f"  Saving to: {out_path}")

    resp = requests.get(presigned_url, stream=True, timeout=120)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                print(f"\r  Progress: {pct:.1f}%", end="", flush=True)

    print(f"\n  Done: {out_path} ({out_path.stat().st_size:,} bytes)")


def cmd_files_upload(client, args):
    """Upload one or more files (or all files in a directory)."""
    # Resolve paths — expand directories to their file contents
    file_paths = []
    for p in args.paths:
        path = Path(p)
        if not path.exists():
            print(f"Path not found: {p}", file=sys.stderr)
            sys.exit(1)
        if path.is_dir():
            children = sorted(f for f in path.iterdir() if f.is_file() and not f.name.startswith("."))
            if not children:
                print(f"No files in directory: {p}", file=sys.stderr)
                sys.exit(1)
            file_paths.extend(children)
        else:
            file_paths.append(path)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    if args.dry_run:
        for fp in file_paths:
            title = args.title if (args.title and len(file_paths) == 1) else fp.stem
            parts = [f"title='{title}'"]
            if args.folder:
                parts.append(f"folder={args.folder}")
            if tags:
                parts.append(f"tags={tags}")
            print(f"Would UPLOAD {fp.name} ({fp.stat().st_size:,} bytes) — {', '.join(parts)}")
        return

    # Files > 10 MB use the 3-step presign flow (upload directly to storage
    # provider, no bytes through server). Smaller files use multipart POST.
    large_file_threshold = 10 * 1024 * 1024

    url = client._url("files/")
    presign_url = client._url("files/presign-upload/")
    uploaded = 0
    for fp in file_paths:
        title = args.title if (args.title and len(file_paths) == 1) else fp.stem
        file_size = fp.stat().st_size

        print(f"Uploading: {fp.name} ({file_size:,} bytes) ...", end=" ", flush=True)

        if file_size > large_file_threshold:
            result = _upload_via_presign(client, fp, title, args.folder, tags, presign_url, url)
        else:
            result = _upload_via_multipart(client, fp, title, args.folder, tags, url)

        if args.format == "json":
            out_json(result)
        else:
            print(f"OK — id={result.get('id')} title='{result.get('title')}'")
        uploaded += 1

    if len(file_paths) > 1 and args.format != "json":
        print(f"\nUploaded {uploaded} file(s)")


def _upload_via_multipart(client, fp, title, folder, tags, url):
    """Small files: single multipart POST (bytes through server)."""
    import mimetypes

    data_fields = {"title": title}
    if folder:
        data_fields["folder"] = str(folder)
    for i, tag in enumerate(tags):
        data_fields[f"tags[{i}]"] = tag

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    with open(fp, "rb") as fh:
        files = {"file_upload": (fp.name, fh, content_type)}
        resp = client._request("POST", url, data=data_fields, files=files)
    return resp.json()


def _upload_via_presign(client, fp, title, folder, tags, presign_url, create_url):
    """Large files: 3-step presign flow (upload directly to storage provider)."""
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    file_size = fp.stat().st_size

    # Step 1: Get presigned upload URL from backend
    presign = client._request("POST", presign_url, json={
        "filename": fp.name,
        "content_type": content_type,
        "file_size": file_size,
    }).json()

    upload_url = presign["upload_url"]
    upload_type = presign.get("upload_type", "presigned_put")

    # Step 2: Upload directly to storage provider
    if upload_type == "sharepoint_session":
        drive_item = _upload_sharepoint_chunked(upload_url, fp, file_size)
        drive_item_id = drive_item["id"]
        drive_id = drive_item.get("parentReference", {}).get("driveId", "")
    else:
        # R2 presigned PUT
        with open(fp, "rb") as fh:
            resp = requests.put(upload_url, data=fh, headers={
                "Content-Type": content_type,
            }, timeout=300)
            resp.raise_for_status()
        drive_item_id = None
        drive_id = None

    # Step 3: Create file record in the app
    payload = {
        "title": title,
        "original_filename": fp.name,
        "content_type": content_type,
        "file_size_input": file_size,
    }
    if folder:
        payload["folder"] = folder
    if tags:
        payload["tags"] = tags

    # Include storage reference data
    storage_key = presign.get("storage_key", "")
    if storage_key:
        payload["storage_key"] = storage_key
    if drive_item_id:
        payload["drive_item_id"] = drive_item_id
        payload["drive_id"] = drive_id
        payload["provider_type"] = presign.get("provider_type", "")
        storage_ref_data = presign.get("storage_ref_data", {})
        if storage_ref_data.get("provider_id"):
            payload["provider_id"] = storage_ref_data["provider_id"]

    resp = client._request("POST", create_url, json=payload)
    return resp.json()


def cmd_files_upload_version(client, args):
    """Upload a new version of an existing file.

    Hits POST /files/{id}/upload_new_version/ on the backend. Small files
    (≤ 10 MB) go through multipart POST; larger ones use the presign flow
    (identical to `files upload`) then reference the resulting storage_key
    in the version create payload.
    """
    fp = Path(args.path)
    if not fp.exists() or not fp.is_file():
        print(f"Path not found or not a file: {args.path}", file=sys.stderr)
        sys.exit(1)

    file_size = fp.stat().st_size
    large_file_threshold = 10 * 1024 * 1024

    version_url = client._url(f"files/{args.file_id}/upload_new_version/")

    if args.dry_run:
        mode = "presign" if file_size > large_file_threshold else "multipart"
        print(f"Would UPLOAD new version of file id={args.file_id} "
              f"from {fp.name} ({file_size:,} bytes, {mode})")
        return

    print(f"Uploading new version of file {args.file_id}: {fp.name} "
          f"({file_size:,} bytes) ...", end=" ", flush=True)

    if file_size > large_file_threshold:
        result = _upload_version_via_presign(client, fp, args.file_id, args.notes, version_url)
    else:
        result = _upload_version_via_multipart(client, fp, args.notes, version_url)

    if args.format == "json":
        out_json(result)
    else:
        print(f"OK — version_id={result.get('id')} v{result.get('version_number')}")


def _upload_version_via_multipart(client, fp, notes, version_url):
    """Small-file path: direct multipart POST to upload_new_version."""
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    data_fields = {}
    if notes:
        data_fields["version_notes"] = notes
    with open(fp, "rb") as fh:
        files = {"file_upload": (fp.name, fh, content_type)}
        resp = client._request("POST", version_url, data=data_fields, files=files)
    return resp.json()


def _upload_version_via_presign(client, fp, file_id, notes, version_url):
    """Large-file path: presign → PUT-to-storage → POST version with storage_key.

    Reuses the same `files/presign-upload/` endpoint the CLI already uses for
    new uploads. The returned storage_key is passed to
    `files/{id}/upload_new_version/` instead of the `files/` create URL.
    """
    import mimetypes

    content_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    file_size = fp.stat().st_size

    presign_url = client._url("files/presign-upload/")

    # Step 1: presigned upload URL. Deliberately do NOT pass a `folder`
    # because the version inherits its file's folder — the backend ignores
    # folder here but the caller would be confusing.
    presign = client._request("POST", presign_url, json={
        "filename": fp.name,
        "content_type": content_type,
        "file_size": file_size,
    }).json()

    upload_url = presign["upload_url"]
    upload_type = presign.get("upload_type", "presigned_put")

    # Step 2: PUT bytes to storage.
    if upload_type == "sharepoint_session":
        drive_item = _upload_sharepoint_chunked(upload_url, fp, file_size)
        drive_item_id = drive_item["id"]
        drive_id = drive_item.get("parentReference", {}).get("driveId", "")
    else:
        with open(fp, "rb") as fh:
            resp = requests.put(upload_url, data=fh, headers={
                "Content-Type": content_type,
            }, timeout=300)
            resp.raise_for_status()
        drive_item_id = None
        drive_id = None

    # Step 3: create the FileVersion record pointing at the uploaded bytes.
    payload = {
        "original_filename": fp.name,
        "content_type": content_type,
        "file_size_input": file_size,
    }
    if notes:
        payload["version_notes"] = notes
    storage_key = presign.get("storage_key", "")
    if storage_key:
        payload["storage_key"] = storage_key
    if drive_item_id:
        payload["drive_item_id"] = drive_item_id
        payload["drive_id"] = drive_id
        payload["provider_type"] = presign.get("provider_type", "")
        storage_ref_data = presign.get("storage_ref_data", {})
        if storage_ref_data.get("provider_id"):
            payload["provider_id"] = storage_ref_data["provider_id"]

    resp = client._request("POST", version_url, json=payload)
    return resp.json()


def _upload_sharepoint_chunked(upload_url, fp, total_size):
    """Upload to SharePoint via chunked upload session. Returns driveItem."""
    chunk_size = 5 * 320 * 1024  # 1.6 MB aligned to 320 KiB
    offset = 0
    item = None

    with open(fp, "rb") as fh:
        while offset < total_size:
            end = min(offset + chunk_size, total_size)
            chunk = fh.read(end - offset)

            resp = requests.put(upload_url, data=chunk, headers={
                "Content-Length": str(end - offset),
                "Content-Range": f"bytes {offset}-{end - 1}/{total_size}",
            }, timeout=120)

            if resp.status_code in (200, 201):
                item = resp.json()
                break
            elif resp.status_code == 202:
                offset = end
                pct = offset / total_size * 100
                print(f"\r  Progress: {pct:.0f}%", end="", flush=True)
            else:
                resp.raise_for_status()

    if not item:
        raise RuntimeError("SharePoint upload completed but no driveItem returned")

    print(f"\r  Progress: 100%", flush=True)
    return item


# ═══════════════════════════════════════════════════════════════════════════════
# TAGS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_tags_list(client, args):
    """List all tags with file counts."""
    data = client.get("files/value-counts/", {"column": "tags", "page_size": 200})
    if args.format == "json":
        out_json(data)
        return
    # Handle both formats: {value_counts: [...]} and {results: [...]}
    if isinstance(data, dict):
        results = data.get("value_counts") or data.get("results") or []
    else:
        results = data
    rows = []
    for item in results:
        if isinstance(item, dict):
            rows.append({"tag": item.get("value", ""), "files": str(item.get("count", 0))})
        else:
            rows.append({"tag": str(item), "files": "-"})
    rows.sort(key=lambda r: int(r["files"]) if r["files"] != "-" else 0, reverse=True)
    print(f"Tags: {len(rows)} unique\n")
    out_table(rows, ["tag", "files"])


def cmd_tags_add(client, args):
    """Add tags to files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would ADD tags {tags} to files {args.file_ids}")
        return
    data = client.post("files/bulk_update/", {"file_ids": args.file_ids, "tags": tags, "tag_mode": "add"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added tags {tags} to {data.get('success_count', 0)} files")


def cmd_tags_remove(client, args):
    """Remove tags from files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would REMOVE tags {tags} from files {args.file_ids}")
        return
    data = client.post("files/bulk_update/", {"file_ids": args.file_ids, "tags": tags, "tag_mode": "remove"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Removed tags {tags} from {data.get('success_count', 0)} files")


def cmd_tags_set(client, args):
    """Replace all tags on files."""
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"Would SET tags to {tags} on files {args.file_ids}")
        return
    data = client.post("files/bulk_update/", {"file_ids": args.file_ids, "tags": tags, "tag_mode": "set"})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Set tags {tags} on {data.get('success_count', 0)} files")


# ═══════════════════════════════════════════════════════════════════════════════
# FOLDERS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_folders_tree(client, args):
    """Folder hierarchy."""
    try:
        data = client.get("folders/folder_tree/")
    except requests.HTTPError:
        data = client.get_all_pages("folders/")

    if args.format == "json":
        out_json(data)
        return

    folders = data if isinstance(data, list) else data.get("results", [])
    if folders and "subfolders" in folders[0]:
        _print_tree_nested(folders, args.depth)
    else:
        _print_tree_flat(folders, args.depth)


def _print_tree_nested(nodes, max_depth, depth=0):
    for f in sorted(nodes, key=lambda x: x.get("name", "")):
        if max_depth is not None and depth > max_depth:
            return
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        fc = f.get("file_count", 0)
        rfc = f.get("recursive_file_count", "")
        count_str = f"({fc} files"
        if rfc and rfc != fc:
            count_str += f", {rfc} recursive"
        count_str += ")"
        print(f"{indent}{prefix}{f['name']}/  [id={f['id']}]  {count_str}")
        for sub in f.get("subfolders", []):
            _print_tree_nested([sub], max_depth, depth + 1)


def _print_tree_flat(folders, max_depth):
    by_parent = {}
    by_id = {}
    for f in folders:
        by_id[f["id"]] = f
        by_parent.setdefault(f.get("parent"), []).append(f)

    def print_node(fid, depth=0):
        if max_depth is not None and depth > max_depth:
            return
        f = by_id[fid]
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        fc = f.get("file_count", 0)
        print(f"{indent}{prefix}{f['name']}/  [id={f['id']}]  ({fc} files)")
        for child in sorted(by_parent.get(fid, []), key=lambda x: x["name"]):
            print_node(child["id"], depth + 1)

    roots = sorted(by_parent.get(None, []), key=lambda x: x["name"])
    print(f"Folders: {len(folders)} total\n")
    for r in roots:
        print_node(r["id"])


def cmd_folders_create(client, args):
    """Create a folder."""
    payload = {"name": args.name}
    if args.parent:
        payload["parent"] = args.parent
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE folder: {payload}")
        return
    data = client.post("folders/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created folder '{data.get('name')}' (id={data.get('id')})")


def cmd_folders_rename(client, args):
    """Rename a folder."""
    if args.dry_run:
        print(f"Would RENAME folder {args.folder_id} to '{args.name}'")
        return
    data = client.patch(f"folders/{args.folder_id}/", {"name": args.name})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Renamed folder {args.folder_id} to '{data.get('name')}'")


def cmd_folders_move(client, args):
    """Move a folder."""
    if args.dry_run:
        target = f"folder {args.parent}" if args.parent else "root"
        print(f"Would MOVE folder {args.folder_id} to {target}")
        return
    data = client.post(f"folders/{args.folder_id}/move/", {"parent_id": args.parent})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Moved folder {args.folder_id}")


def cmd_folders_delete(client, args):
    """Delete folder and all contents."""
    if args.dry_run:
        print(f"Would DELETE folder {args.folder_id} and all contents")
        return
    data = client.delete(f"folders/{args.folder_id}/delete_with_contents/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Deleted folder {args.folder_id} and all contents")


def cmd_folders_subfolders(client, args):
    """Lightweight subfolder listing for path resolution.

    Hits POST /folders/{id}/subfolders/ which returns only [{id, name}] and
    is paginated. Walks all pages and returns a single JSON array. This is
    much faster than `folders contents` on large folders because the backend
    skips the file_count / subfolder_count Cartesian-product COUNT joins.
    """
    timeout = getattr(args, "timeout", None) or 180
    page_size = getattr(args, "page_size", None) or 1000
    page = 1
    out = []
    while True:
        resp = client._request(
            "GET",
            client._url(f"folders/{args.folder_id}/subfolders/"),
            params={"page": page, "page_size": page_size},
            timeout=timeout,
        )
        data = resp.json()
        results = data.get("results") if isinstance(data, dict) else data
        if results is None:
            results = []
        for s in results:
            sid = s.get("id")
            name = s.get("name")
            if sid is not None and name is not None:
                out.append({"id": int(sid), "name": str(name)})
        # DRF paginators expose `next` as the next URL or null.
        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        page += 1
    out_json({"subfolders": out, "count": len(out)})


def cmd_folders_contents(client, args):
    """Show folder contents."""
    # Large folders (e.g. project-4 ProjectSight Documents/QA-QC, RFI, Submittal)
    # can take 30-90s server-side. Override the 30s default to give the backend
    # room to respond for legitimately large enumerations.
    timeout = getattr(args, "timeout", None) or 180
    resp = client._request(
        "GET",
        client._url(f"folders/{args.folder_id}/contents/"),
        timeout=timeout,
    )
    data = resp.json()
    if args.format == "json":
        out_json(data)
        return
    folder = data.get("folder", data)
    print(f"Folder: {folder.get('name', '?')} (id={folder.get('id', args.folder_id)})\n")
    subs = data.get("subfolders", [])
    if subs:
        print(f"Subfolders ({len(subs)}):")
        for s in subs:
            print(f"  {s['name']}/  [id={s['id']}]  ({s.get('file_count', 0)} files)")
        print()
    files = data.get("files", [])
    if files:
        rows = [_file_row(f) for f in files]
        print(f"Files ({len(files)}):")
        out_table(rows, ["id", "title", "type", "size", "tags"])


# ═══════════════════════════════════════════════════════════════════════════════
# MOVE / CATEGORIZE / ARCHIVE (bulk file operations)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_move(client, args):
    """Move files to a folder."""
    if args.dry_run:
        target = f"folder {args.folder}" if args.folder else "root"
        print(f"Would MOVE {len(args.file_ids)} files to {target}")
        return
    data = client.post("files/bulk_move/", {"file_ids": args.file_ids, "folder_id": args.folder})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Moved {data.get('success_count', len(args.file_ids))} files")


def cmd_categorize(client, args):
    """Set category on files."""
    if args.dry_run:
        print(f"Would SET category '{args.category}' on {len(args.file_ids)} files")
        return
    data = client.post("files/bulk_update/", {"file_ids": args.file_ids, "category": args.category})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Set category '{args.category}' on {data.get('success_count', 0)} files")


def cmd_file_update(client, args):
    """Update single file metadata."""
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category
    if args.tags is not None:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.folder is not None:
        payload["folder"] = args.folder if args.folder != 0 else None
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE file {args.file_id}: {payload}")
        return
    data = client.patch(f"files/{args.file_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated file {data.get('id')}: '{data.get('title')}'")


# Tag used to mark files for deletion. Direct file deletion is not supported by
# the PCXA CLI; instead, files are tagged with this value and a separate cleanup
# process (out of scope for this CLI) handles the actual deletion.
DELETION_TAG = "to_delete"


def cmd_files_delete(client, args):
    """Tag files for deletion (soft-delete via the 'to_delete' tag).

    Direct deletion is intentionally not exposed. This command adds the
    DELETION_TAG to the listed files so they can be discovered and removed
    by an out-of-band cleanup process. Use `pcxa files restore` to undo.
    """
    file_ids = args.file_ids
    if args.dry_run:
        print(f"Would tag {len(file_ids)} files with '{DELETION_TAG}': {file_ids}")
        return
    if not args.yes:
        print(f"Tag {len(file_ids)} files with '{DELETION_TAG}' (mark for deletion)? "
              f"[y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("Aborted.")
            return
    data = client.post("files/bulk_update/",
                       {"file_ids": file_ids, "tags": [DELETION_TAG], "tag_mode": "add"})
    if args.format == "json":
        out_json(data)
    else:
        n = data.get("success_count", 0)
        print(f"Tagged {n} files with '{DELETION_TAG}'.")
        print(f"Actual removal is handled by a separate cleanup process.")
        print(f"List pending deletions:  pcxa files list --tags {DELETION_TAG}")


def cmd_files_restore(client, args):
    """Remove the 'to_delete' tag from files (undo a deletion mark)."""
    file_ids = args.file_ids
    if args.dry_run:
        print(f"Would remove '{DELETION_TAG}' tag from {len(file_ids)} files")
        return
    data = client.post("files/bulk_update/",
                       {"file_ids": file_ids, "tags": [DELETION_TAG], "tag_mode": "remove"})
    if args.format == "json":
        out_json(data)
    else:
        n = data.get("success_count", 0)
        print(f"Removed '{DELETION_TAG}' tag from {n} files.")


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════════════════════

PRIORITY_MAP = {0: "-", 1: "Low", 2: "Med", 3: "High", 4: "Critical"}


def _activity_row(a):
    assignees = a.get("assignees_details") or a.get("assignees") or []
    if isinstance(assignees, list) and assignees:
        if isinstance(assignees[0], dict):
            astr = ",".join(x.get("username", "") for x in assignees[:3])
        else:
            astr = ",".join(str(x) for x in assignees[:3])
    else:
        astr = ""
    return {
        "id": str(a.get("id", "")),
        "title": str(a.get("title", ""))[:45],
        "status": a.get("status", ""),
        "pct": f"{a.get('percent_complete', 0)}%",
        "priority": PRIORITY_MAP.get(a.get("priority", 0), "?"),
        "owner": str(a.get("owner_name", a.get("owner") or ""))[:12],
        "due": str(a.get("due_date") or "")[:10],
    }


def resolve_member_by_name(client, query):
    """Resolve a name query to a user ID via fuzzy matching on project members.

    Returns (user_id, message) where message describes the match outcome.
    On ambiguity or no match, user_id is None and message explains next steps.
    """
    data = client.get("memberships/", {"limit": 200})
    results = data.get("results", data) if isinstance(data, dict) else data
    # Filter out AI agents
    members = [m for m in results if not m.get("is_ai_agent")]
    if not members:
        return None, "No project members found."

    query_lower = query.lower().strip()

    # Build candidate list: (user_id, display_name, match_fields)
    candidates = []
    for m in members:
        uid = m.get("user")
        name = m.get("user_name", "")
        username = m.get("user_username", "")
        email = m.get("user_email", "")
        candidates.append((uid, name, username, email))

    # 1. Exact match on name, username, or email
    for uid, name, username, email in candidates:
        if query_lower in (name.lower(), username.lower(), email.lower()):
            return uid, f"Exact match: {name} (user {uid})"

    # 2. Substring match
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

    # 3. Fuzzy match using difflib
    scored = []
    for uid, name, username, email in candidates:
        # Score against name and username, take best
        s1 = difflib.SequenceMatcher(None, query_lower, name.lower()).ratio()
        s2 = difflib.SequenceMatcher(None, query_lower, username.lower()).ratio()
        best = max(s1, s2)
        scored.append((best, uid, name, username))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored[0][0] >= 0.6:
        top = scored[0]
        # Check if runner-up is close (within 0.1)
        if len(scored) > 1 and scored[1][0] >= top[0] - 0.1:
            # Return top matches
            close = [s for s in scored if s[0] >= top[0] - 0.1][:5]
            lines = [f"No exact match for '{query}'. Close matches:"]
            for score, uid, name, username in close:
                lines.append(f"  - user {uid}: {name} [{username}] ({score:.0%})")
            lines.append("Pass --assignee <user_id> with the correct ID.")
            return None, "\n".join(lines)
        # Clear winner
        _, uid, name, username = top
        return uid, f"Fuzzy match: {name} [{username}] (user {uid})"

    return None, f"No match found for '{query}'. Use `pcxa project members` to list all members."


def cmd_activities_list(client, args):
    """List activities."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    if args.priority:
        params["priority"] = args.priority
    if args.owner:
        owner = args.owner
        if not owner.isdigit():
            uid, msg = resolve_member_by_name(client, owner)
            print(msg, file=sys.stderr)
            if uid is None:
                sys.exit(1)
            owner = str(uid)
        params["owner"] = owner
    if args.assignee:
        # Accept user ID (integer) or name (fuzzy resolved)
        assignee = args.assignee
        if not assignee.isdigit():
            uid, msg = resolve_member_by_name(client, assignee)
            print(msg, file=sys.stderr)
            if uid is None:
                sys.exit(1)
            assignee = str(uid)
        params["assigned_to"] = assignee
    if args.type:
        params["activity_type"] = args.type
    if args.parent:
        params["parent"] = args.parent
    if args.root_only:
        params["parent__isnull"] = "true"
    if args.search:
        params["search"] = args.search
    if args.tags:
        params["tags"] = args.tags
        if getattr(args, "tags_mode", None):
            params["tags_mode"] = args.tags_mode
    if getattr(args, "after", None):
        params["updated_at__gte"] = args.after
    if getattr(args, "before", None):
        params["updated_at__lte"] = args.before
    if getattr(args, "created_after", None):
        params["created_at__gte"] = args.created_after
    if getattr(args, "created_before", None):
        params["created_at__lte"] = args.created_before
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("activities/", params)}))
        return

    data = client.get("activities/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_activity_row(a) for a in results]
    print(f"Activities: {len(rows)} of {total}\n")
    out_table(rows, ["id", "title", "status", "pct", "priority", "owner", "due"])


def cmd_activities_get(client, args):
    """Activity detail."""
    data = client.get(f"activities/{args.activity_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Activity {data.get('id')}: {data.get('title')}")
    print(f"  Status:    {data.get('status')} ({data.get('percent_complete', 0)}%)")
    print(f"  Priority:  {PRIORITY_MAP.get(data.get('priority', 0), '?')}")
    print(f"  Type:      {data.get('activity_type_name', data.get('activity_type') or '-')}")
    print(f"  Owner:     {data.get('owner_name', data.get('owner') or '-')}")
    print(f"  Due:       {data.get('due_date') or '-'}")
    ps, pf = str(data.get("planned_start") or "-")[:10], str(data.get("planned_finish") or "-")[:10]
    print(f"  Planned:   {ps} -> {pf}")
    acs, acf = str(data.get("actual_start") or "-")[:10], str(data.get("actual_finish") or "-")[:10]
    print(f"  Actual:    {acs} -> {acf}")
    if data.get("description"):
        print(f"  Desc:      {data['description'][:200]}")
    if data.get("wbs_code"):
        print(f"  WBS:       {data['wbs_code']}")
    tags = data.get("tags") or []
    if tags:
        print(f"  Tags:      {tag_names(tags)}")

    steps = data.get("steps") or []
    if steps:
        print(f"\n  Steps ({len(steps)}):")
        for s in steps:
            check = "x" if s.get("percent_complete", 0) == 100 else " "
            print(f"    [{check}] {s.get('name', '?')} ({s.get('percent_complete', 0)}%, w={s.get('progress_weight', '?')})")

    deps = data.get("activity_dependencies") or {}
    for label, key in [("Predecessors", "predecessors"), ("Successors", "successors")]:
        items = deps.get(key) or []
        if items:
            print(f"\n  {label} ({len(items)}):")
            for d in items:
                ref = d.get("predecessor") if key == "predecessors" else d.get("successor")
                print(f"    {d.get('dependency_type', '?')} #{ref} (lag={d.get('lag_days', 0)}d)")
    print()


def cmd_activities_create(client, args):
    """Create activity."""
    payload = {"title": args.title, "project": client.project_id}
    if args.description:
        payload["description"] = args.description
    if args.status:
        payload["status"] = args.status
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.due_date:
        payload["due_date"] = args.due_date
    if args.planned_start:
        payload["planned_start"] = args.planned_start
    if args.planned_finish:
        payload["planned_finish"] = args.planned_finish
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.type:
        payload["activity_type"] = args.type
    if args.parent:
        payload["parent"] = args.parent
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.wbs:
        payload["wbs_code"] = args.wbs

    if args.dry_run:
        print(f"Would CREATE activity: {json.dumps(payload, indent=2)}")
        return
    data = client.post("activities/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created activity {data.get('id')}: '{data.get('title')}'")


def cmd_activities_update(client, args):
    """Update activity."""
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description is not None:
        payload["description"] = args.description
    if args.status:
        payload["status"] = args.status
    if args.priority is not None:
        payload["priority"] = args.priority
    if args.percent is not None:
        payload["percent_complete"] = args.percent
    if args.due_date:
        payload["due_date"] = args.due_date
    if args.planned_start:
        payload["planned_start"] = args.planned_start
    if args.planned_finish:
        payload["planned_finish"] = args.planned_finish
    if args.actual_start:
        payload["actual_start"] = args.actual_start
    if args.actual_finish:
        payload["actual_finish"] = args.actual_finish
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.parent is not None:
        payload["parent"] = args.parent if args.parent != 0 else None
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]

    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE activity {args.activity_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"activities/{args.activity_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated activity {data.get('id')}: '{data.get('title')}'")


def cmd_activities_delete(client, args):
    """Delete activities."""
    if args.dry_run:
        print(f"Would DELETE activities {args.activity_ids}")
        return
    if len(args.activity_ids) == 1:
        client.post(f"activities/{args.activity_ids[0]}/soft_delete/")
        print(f"Deleted activity {args.activity_ids[0]}")
    else:
        data = client.post("activities/bulk_delete/", {"activity_ids": args.activity_ids})
        if args.format == "json":
            out_json(data)
        else:
            print(f"Deleted {data.get('success_count', len(args.activity_ids))} activities")


def cmd_activities_bulk_update(client, args):
    """Bulk update activities."""
    updates = {}
    if args.status:
        updates["status"] = args.status
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.owner:
        updates["owner"] = args.owner
    if not updates:
        print("No updates specified.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would BULK UPDATE {len(args.activity_ids)} activities: {updates}")
        return
    data = client.post("activities/bulk_update/", {"activity_ids": args.activity_ids, "updates": updates})
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated {data.get('success_count', '?')} activities")


def cmd_activities_types(client, args):
    """List activity types."""
    data = client.get_all_pages("activity-types/")
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for t in data:
        steps = t.get("template_steps") or t.get("steps") or []
        rows.append({
            "id": str(t.get("id", "")),
            "name": str(t.get("name", ""))[:35],
            "category": str(t.get("category", ""))[:20],
            "steps": str(len(steps)),
            "default": "yes" if t.get("is_default") else "",
        })
    print(f"Activity types: {len(rows)}\n")
    out_table(rows, ["id", "name", "category", "steps", "default"])


# ═══════════════════════════════════════════════════════════════════════════════
# STEPS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_steps_list(client, args):
    data = client.get(f"activities/{args.activity_id}/steps/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for s in results:
        check = "x" if s.get("percent_complete", 0) == 100 else " "
        rows.append({
            "id": str(s.get("id", "")),
            "order": str(s.get("order", "")),
            "done": f"[{check}]",
            "name": str(s.get("name", ""))[:40],
            "pct": f"{s.get('percent_complete', 0)}%",
            "weight": f"{s.get('progress_weight', 0)}%",
        })
    print(f"Steps for activity {args.activity_id}:\n")
    out_table(rows, ["id", "order", "done", "name", "pct", "weight"])


def cmd_steps_create(client, args):
    payload = {"name": args.name, "activity": args.activity_id}
    if args.description:
        payload["description"] = args.description
    if args.weight is not None:
        payload["progress_weight"] = args.weight
    if args.order is not None:
        payload["order"] = args.order
    if args.dry_run:
        print(f"Would CREATE step: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/steps/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created step {data.get('id')}: '{data.get('name')}' (weight={data.get('progress_weight')}%)")


def cmd_steps_update(client, args):
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.percent is not None:
        payload["percent_complete"] = args.percent
    if args.weight is not None:
        payload["progress_weight"] = args.weight
    if args.order is not None:
        payload["order"] = args.order
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE step {args.step_id}: {payload}")
        return
    data = client.patch(f"activities/{args.activity_id}/steps/{args.step_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated step {data.get('id')}: '{data.get('name')}' ({data.get('percent_complete')}%)")


def cmd_steps_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE step {args.step_id}")
        return
    client.delete(f"activities/{args.activity_id}/steps/{args.step_id}/")
    print(f"Deleted step {args.step_id}")


def cmd_steps_from_template(client, args):
    if args.dry_run:
        print(f"Would CREATE steps from template on activity {args.activity_id}")
        return
    data = client.post(f"activities/{args.activity_id}/create_steps_from_template/")
    if args.format == "json":
        out_json(data)
    else:
        steps = data if isinstance(data, list) else data.get("steps", [data])
        print(f"Created {len(steps)} steps from template")


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_progress_list(client, args):
    params = {}
    if args.source:
        params["source"] = args.source
    data = client.get(f"activities/{args.activity_id}/progress-entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("effective_date", ""))[:16],
            "pct": f"{e.get('percent_complete', 0)}%",
            "source": e.get("source", ""),
            "notes": str(e.get("notes", ""))[:40],
        })
    print(f"Progress for activity {args.activity_id}:\n")
    out_table(rows, ["id", "date", "pct", "source", "notes"])


def cmd_progress_add(client, args):
    payload = {"percent_complete": args.percent}
    if args.notes:
        payload["notes"] = args.notes
    payload["effective_date"] = args.date or datetime.date.today().isoformat()
    if args.dry_run:
        print(f"Would ADD progress: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/progress-entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added progress: {data.get('percent_complete', 0)}% at {str(data.get('effective_date', ''))[:16]}")


def cmd_progress_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE progress entry {args.entry_id}")
        return
    client.delete(f"activities/{args.activity_id}/progress-entries/{args.entry_id}/")
    print(f"Deleted progress entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMENTS
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_comments_list(client, args):
    data = client.get(f"activities/{args.activity_id}/comments/")
    results = data.get("results", data) if isinstance(data, dict) else data
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for c in results:
        user = c.get("user") or {}
        user_name = user.get("first_name", "") or user.get("username", "") or str(user.get("id", ""))
        rows.append({
            "id": str(c.get("id", "")),
            "user": user_name,
            "content": str(c.get("content", ""))[:60],
            "type": c.get("comment_type", ""),
            "created": str(c.get("created_at", ""))[:16],
        })
    print(f"Comments on activity {args.activity_id}: {len(rows)}\n")
    out_table(rows, ["id", "user", "content", "type", "created"])


def cmd_comments_add(client, args):
    payload = {"content": args.content}
    if args.dry_run:
        print(f"Would ADD comment on activity {args.activity_id}: {payload}")
        return
    data = client.post(f"activities/{args.activity_id}/comments/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Added comment {data.get('id')} on activity {args.activity_id}")


def cmd_comments_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE comment {args.comment_id} on activity {args.activity_id}")
        return
    client.delete(f"activities/{args.activity_id}/comments/{args.comment_id}/")
    print(f"Deleted comment {args.comment_id}")


def cmd_comments_bulk(client, args):
    """Bulk add comments from a JSON file."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(file_path.read_text())
    if isinstance(raw, dict):
        comments = raw.get("comments")
        if not isinstance(comments, list):
            print("JSON object must contain a 'comments' key with a list.", file=sys.stderr)
            sys.exit(1)
    elif isinstance(raw, list):
        comments = raw
    else:
        print("JSON file must contain a list or an object with a 'comments' key.", file=sys.stderr)
        sys.exit(1)

    created = 0
    errors = []
    for i, item in enumerate(comments):
        content = item.get("content")
        if not content:
            errors.append(f"[{i}] Missing 'content' field")
            continue

        payload = {"content": content}

        if args.dry_run:
            preview = content[:60] + ("..." if len(content) > 60 else "")
            print(f"  [{i}] Would ADD: {preview}")
            continue

        try:
            data = client.post(f"activities/{args.activity_id}/comments/", payload)
            created += 1
            if args.format != "json":
                print(f"  [{i}] Created comment {data.get('id')}")
        except Exception as e:
            errors.append(f"[{i}] {e}")

    if args.dry_run:
        print(f"\nDry run: {len(comments)} comments would be created on activity {args.activity_id}")
    else:
        print(f"\nBulk complete: {created} created, {len(errors)} errors")
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  {err}")


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_deps_list(client, args):
    params = {}
    if args.predecessor:
        params["predecessor"] = args.predecessor
    if args.successor:
        params["successor"] = args.successor
    if args.dep_type:
        params["dependency_type"] = args.dep_type
    data = client.get("dependencies/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [{"id": str(d.get("id", "")), "pred": str(d.get("predecessor", "")),
             "type": d.get("dependency_type", ""), "succ": str(d.get("successor", "")),
             "lag": str(d.get("lag_days", 0)), "notes": str(d.get("notes", ""))[:35]}
            for d in results]
    print(f"Dependencies: {len(rows)}\n")
    out_table(rows, ["id", "pred", "type", "succ", "lag", "notes"])


def cmd_deps_create(client, args):
    payload = {"predecessor": args.predecessor, "successor": args.successor, "dependency_type": args.dep_type}
    if args.lag is not None:
        payload["lag_days"] = args.lag
    if args.notes:
        payload["notes"] = args.notes
    if args.dry_run:
        print(f"Would CREATE dependency: {payload}")
        return
    data = client.post("dependencies/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created dependency {data.get('id')}: #{data.get('predecessor')} {data.get('dependency_type')} -> #{data.get('successor')}")


def cmd_deps_delete(client, args):
    if args.dry_run:
        print(f"Would DELETE dependency {args.dep_id}")
        return
    client.delete(f"dependencies/{args.dep_id}/")
    print(f"Deleted dependency {args.dep_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# GANTT / TREE
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_gantt(client, args):
    params = {}
    if args.status:
        params["status"] = args.status
    data = client.get("activities/gantt/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [{"id": str(a.get("id", "")), "title": str(a.get("title", ""))[:35],
             "start": str(a.get("planned_start") or "")[:10], "finish": str(a.get("planned_finish") or "")[:10],
             "pct": f"{a.get('percent_complete', a.get('progress', 0))}%",
             "critical": "YES" if a.get("is_critical") else "", "float": str(a.get("total_float", ""))[:5]}
            for a in results]
    print(f"Gantt: {len(rows)} activities\n")
    out_table(rows, ["id", "title", "start", "finish", "pct", "critical", "float"])


def cmd_tree(client, args):
    params = {"view": "tree"}
    if args.max_depth:
        params["max_depth"] = args.max_depth
    if args.status:
        params["status"] = args.status
    data = client.get("activities/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data

    def print_node(node, depth=0):
        indent = "  " * depth
        prefix = "|- " if depth > 0 else ""
        print(f"{indent}{prefix}[{node.get('id')}] {node.get('title', '?')} ({node.get('status', '')}, {node.get('percent_complete', 0)}%)")
        for child in node.get("children", []):
            print_node(child, depth + 1)

    for node in results:
        print_node(node)


# ═══════════════════════════════════════════════════════════════════════════════
# CHAT — AI conversation
# ═══════════════════════════════════════════════════════════════════════════════

CHAT_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "waiting_for_input"}


def _resolve_chat_conversation(client, args):
    """Pick the conversation to use for `chat send` based on args."""
    if getattr(args, "new", False):
        return client.post("conversations/new/", {"title": getattr(args, "title", "") or ""})
    if getattr(args, "conversation", None):
        return client.get(f"conversations/{args.conversation}/")
    return client.post("conversations/current/")


def _wait_for_agent_task(client, task_id, timeout, interval=1.0):
    """Poll AgentTask status until terminal or timeout. Returns final task dict."""
    start = time.time()
    last_status = None
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"status": last_status or "timeout", "_timed_out": True, "_elapsed": elapsed}
        try:
            task = client.get(f"agent-tasks/{task_id}/")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                time.sleep(interval)
                continue
            raise
        last_status = task.get("status")
        if last_status in CHAT_TERMINAL_STATUSES:
            task["_elapsed"] = elapsed
            return task
        time.sleep(interval)


def cmd_chat_send(client, args):
    conv = _resolve_chat_conversation(client, args)
    conv_id = conv["id"]

    payload = {"content": args.message}
    if args.research:
        payload["research_mode"] = True
    if args.model:
        payload["model"] = args.model
    if args.page_url:
        payload["page_url"] = args.page_url

    sent = client.post(f"conversations/{conv_id}/send/", payload)
    user_msg = sent.get("message") or {}
    task_id = sent.get("agent_task_id")

    if args.no_wait:
        out_json({
            "conversation_id": conv_id,
            "agent_task_id": task_id,
            "user_message": user_msg,
            "note": "Response streams via WebSocket. Re-run with `pcxa chat get %d` later." % conv_id,
        })
        return

    task = _wait_for_agent_task(client, task_id, args.timeout)
    final_status = task.get("status")
    elapsed = task.get("_elapsed", 0.0)

    detail = client.get(f"conversations/{conv_id}/")
    msgs = detail.get("messages", [])
    user_msg_id = user_msg.get("id")
    assistant_msg = None
    if user_msg_id is not None:
        for m in msgs:
            if m.get("role") == "assistant" and (m.get("id") or 0) > user_msg_id:
                assistant_msg = m
                break

    result = {
        "conversation_id": conv_id,
        "agent_task_id": task_id,
        "agent_task_status": final_status,
        "elapsed_seconds": round(elapsed, 2),
        "timed_out": bool(task.get("_timed_out")),
        "user_message": {"id": user_msg.get("id"), "content": user_msg.get("content")},
        "assistant_message": assistant_msg,
    }

    if args.format == "table":
        print(f"Conversation: {conv_id}  Task: {task_id}  Status: {final_status}  Elapsed: {result['elapsed_seconds']}s")
        if result["timed_out"]:
            print(f"(timed out after {args.timeout}s — task may still be running)")
        print()
        print(f"USER:\n{(user_msg.get('content') or '').strip()}\n")
        if assistant_msg:
            print(f"ASSISTANT:\n{(assistant_msg.get('content') or '').strip()}")
            tools = assistant_msg.get("tool_steps") or []
            thinking = assistant_msg.get("thinking_steps") or []
            cards = assistant_msg.get("action_cards") or []
            meta = []
            if tools:
                meta.append(f"{len(tools)} tool calls")
            if thinking:
                meta.append(f"{len(thinking)} thinking steps")
            if cards:
                meta.append(f"{len(cards)} action cards")
            if meta:
                print("\n[" + ", ".join(meta) + "]")
        else:
            print("(no assistant response yet)")
    else:
        out_json(result)

    if final_status == "failed":
        sys.exit(2)


def cmd_chat_ls(client, args):
    params = {"page_size": args.limit}
    if args.search:
        params["search"] = args.search
    data = client.get("conversations/", params)
    results = data.get("results", data) if isinstance(data, dict) else data
    if args.format == "json":
        out_json(results)
        return
    rows = []
    for c in results:
        last = c.get("last_message") or {}
        rows.append({
            "id": str(c.get("id", "")),
            "title": (c.get("title") or "(untitled)")[:40],
            "msgs": str(c.get("message_count", 0)),
            "last": (last.get("role") or "") + ": " + (last.get("content") or "")[:40],
            "updated": str(c.get("updated_at", ""))[:19],
        })
    out_table(rows, ["id", "title", "msgs", "last", "updated"])


def cmd_chat_get(client, args):
    if args.conversation_id:
        data = client.get(f"conversations/{args.conversation_id}/")
    else:
        data = client.post("conversations/current/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Conversation {data['id']}: {data.get('title') or '(untitled)'}")
    usage = data.get("context_usage") or {}
    print(
        f"Tokens: {usage.get('total_tokens', 0)}/{usage.get('threshold', 0)} "
        f"({usage.get('percent', 0)}%) | Active messages: {usage.get('message_count', 0)}"
        f" | Compacted: {usage.get('compacted_count', 0)}"
    )
    print()
    for m in data.get("messages", []):
        role = (m.get("role") or "?").upper()
        content = (m.get("content") or "").strip()
        print(f"[{m.get('id')}] {role}:")
        print(content if content else "(empty)")
        tools = m.get("tool_steps") or []
        if tools and args.show_tools:
            print(f"  -- {len(tools)} tool calls --")
            for t in tools:
                print(f"  - {t.get('tool_name', '?')}: {json.dumps(t.get('tool_input', {}))[:120]}")
        print()


def cmd_chat_new(client, args):
    payload = {"title": args.title or ""}
    data = client.post("conversations/new/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created conversation {data['id']}: {data.get('title') or '(untitled)'}")


def cmd_chat_delete(client, args):
    client.delete(f"conversations/{args.conversation_id}/archive/")
    print(f"Archived conversation {args.conversation_id}")


def cmd_chat_models(client, args):
    data = client.get("ai/models/")
    if args.format == "json":
        out_json(data)
        return
    rows = []
    for m in data.get("models", []):
        rows.append({
            "id": str(m.get("id", "")),
            "label": str(m.get("label", "")),
            "tier": str(m.get("tier", "")),
            "default": "*" if m.get("default") else "",
        })
    print(f"Default: {data.get('default_model', '?')}\n")
    out_table(rows, ["id", "label", "tier", "default"])


# ═══════════════════════════════════════════════════════════════════════════════
# CLI PARSER
# ═══════════════════════════════════════════════════════════════════════════════


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pcxa",
        description="PCXA construction intelligence platform CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", "-p", default=None, help="Config profile")
    parser.add_argument("--format", "-f", choices=["json", "table"], default="json", help="Output format")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--version", "-V", action="version", version=f"pcxa {__version__}")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # ── login (browser-based) ──
    p = sub.add_parser("login", help="Browser-based login (opens pcxa.app, no password needed)")
    p.add_argument("--url", default="https://api.pcxa.app", help="API URL")
    p.add_argument("--frontend-url", default="https://www.pcxa.app", help="Frontend URL")
    p.add_argument("--profile", help="Profile name (default: prod)")
    p.add_argument("--timeout", type=int, default=120, help="Seconds to wait for browser (default: 120)")

    # ── setup ──
    p = sub.add_parser("setup", help="Configure profile and login (password-based)")
    p.add_argument("--url", default="https://api.pcxa.app", help="API URL (default: https://api.pcxa.app)")
    p.add_argument("--username", "-u", required=True, help="Username/email")
    p.add_argument("--password", default=None, help="Password (omit to be prompted)")
    p.add_argument("--company", type=int, help="Company ID")
    p.add_argument("--project", type=int, help="Project ID")
    p.add_argument("--frontend-url", help="Frontend URL (defaults to --url)")

    # ── whoami ──
    sub.add_parser("whoami", help="Show current profile")

    # ── set-project ──
    p = sub.add_parser("set-project", help="Set default project (globally or per-repo)")
    p.add_argument("project_id", type=int, help="Project ID")
    p.add_argument("--company", type=int, help="Company ID (only used with --local)")
    p.add_argument("--user", help="Pin auth account by email for this repo (only used with --local)")
    p.add_argument("--local", action="store_true", help="Write to .pcxa in CWD instead of global config")

    # ── update ──
    p = sub.add_parser("update", help="Update pcxa to the latest release from GitHub")
    p.add_argument("--dry-run", action="store_true", help="Print what would be run without executing")

    # ── project ──
    proj_p = sub.add_parser("project", help="View/update project metadata")
    proj_sub = proj_p.add_subparsers(dest="project_command")

    proj_sub.add_parser("get", help="Show project details")

    p = proj_sub.add_parser("members", help="List project members (name → user ID)")
    p.add_argument("--search", "-s", help="Search by name or username")

    p = proj_sub.add_parser("update", help="Update project metadata")
    p.add_argument("--name", help="Project name")
    p.add_argument("--code", help="Short project code (max 20 chars)")
    p.add_argument("--description", help="Project description")
    p.add_argument("--scope-statement", dest="scope_statement", help="Scope statement (objectives, deliverables, boundaries)")
    p.add_argument("--industry", help="Industry")
    p.add_argument("--life-cycle", dest="life_cycle", help="Project lifecycle stage")
    p.add_argument("--start-date", dest="start_date", help="Start date (YYYY-MM-DD)")
    p.add_argument("--end-date", dest="end_date", help="End date (YYYY-MM-DD)")
    p.add_argument("--progress-input-method", dest="progress_input_method", choices=["status", "percentage"], help="How activities report progress")
    p.add_argument("--rollup-method", dest="rollup_method", choices=["equal", "duration", "cost", "labor"], help="Summary rollup weighting")

    # ── files ──
    files_p = sub.add_parser("files", help="File search & reading")
    files_sub = files_p.add_subparsers(dest="files_command")

    p = files_sub.add_parser("list", help="Filter files by metadata")
    p.add_argument("--ext", help="File types (comma-sep: PDF,DOCX)")
    p.add_argument("--tags", help="Tags filter (comma-sep)")
    p.add_argument("--tags-mode", dest="tags_mode", choices=["any", "all"], help="any=OR (default), all=AND")
    p.add_argument("--folder", type=int, help="Folder ID")
    p.add_argument("--category", help="Category")
    p.add_argument("--search", "-s", help="Title/description search")
    p.add_argument("--index-status", choices=["indexed", "pending", "processing", "failed"])
    p.add_argument("--sort", default="-created_at", help="Sort field")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = files_sub.add_parser("search", help="Semantic vector search")
    p.add_argument("query", help="Natural language query")
    p.add_argument("--types", help="Source types: file,drawing,photo")
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--show-content", action="store_true")

    p = files_sub.add_parser("content", help="Keyword search in file text")
    p.add_argument("query", help="Keyword/phrase")
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--folder", type=int)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = files_sub.add_parser("read", help="Read file content (windowed)")
    p.add_argument("file_id", type=int)
    p.add_argument("--start", type=int, default=None)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("--window", type=int, default=5)
    p.add_argument("--outline", action="store_true", help="Section map only")
    p.add_argument("--all", action="store_true")

    p = files_sub.add_parser("info", help="File metadata & versions")
    p.add_argument("file_id", type=int)

    p = files_sub.add_parser("stats", help="Project file statistics")

    p = files_sub.add_parser("aggregate", help="Group files by dimension")
    p.add_argument("group_by", choices=["file_type", "folder", "category", "search_status"])
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--tags", help="Tags filter")
    p.add_argument("--folder", type=int)
    p.add_argument("--search", "-s")
    p.add_argument("--top", type=int, default=50)

    p = files_sub.add_parser("recent", help="Recently uploaded files")
    p.add_argument("--ext", help="File type filter")
    p.add_argument("--folder", type=int)
    p.add_argument("--limit", type=int, default=20)

    p = files_sub.add_parser("download", help="Download a file to disk")
    p.add_argument("file_id", type=int, help="File ID to download")
    p.add_argument("--output", "-o", help="Output file path (default: original filename)")

    p = files_sub.add_parser("upload", help="Upload file(s) or directory contents")
    p.add_argument("paths", nargs="+", help="File path(s) or directory to upload")
    p.add_argument("--folder", type=int, help="Target folder ID")
    p.add_argument("--title", help="Override title (single file only; defaults to filename stem)")
    p.add_argument("--tags", help="Tags (comma-sep)")

    p = files_sub.add_parser("upload-version",
                             help="Upload a new version of an existing PCXA file")
    p.add_argument("file_id", type=int, help="Existing PCXA file id")
    p.add_argument("path", help="Local file path to upload as the new version")
    p.add_argument("--notes", help="Optional version_notes annotation")

    p = files_sub.add_parser("update", help="Update single file metadata")
    p.add_argument("file_id", type=int)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--tags", help="Tags (comma-sep, replaces all)")
    p.add_argument("--folder", type=int, help="Move to folder (0=root)")

    p = files_sub.add_parser("delete",
                             help=f"Tag files for deletion (adds '{DELETION_TAG}' tag; "
                                  "actual deletion handled out-of-band)")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    p = files_sub.add_parser("restore",
                             help=f"Remove '{DELETION_TAG}' tag from files (undo deletion mark)")
    p.add_argument("file_ids", nargs="+", type=int)

    # ── tags ──
    tags_p = sub.add_parser("tags", help="Tag management")
    tags_sub = tags_p.add_subparsers(dest="tags_command")
    tags_sub.add_parser("list", help="List tags with counts")

    p = tags_sub.add_parser("add", help="Add tags to files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True, help="Tags (comma-sep)")

    p = tags_sub.add_parser("remove", help="Remove tags from files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True)

    p = tags_sub.add_parser("set", help="Replace all tags on files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--tags", required=True)

    # ── folders ──
    folders_p = sub.add_parser("folders", help="Folder management")
    folders_sub = folders_p.add_subparsers(dest="folders_command")

    p = folders_sub.add_parser("tree", help="Folder hierarchy")
    p.add_argument("--depth", type=int)

    p = folders_sub.add_parser("create", help="Create folder")
    p.add_argument("name")
    p.add_argument("--parent", type=int)
    p.add_argument("--description")

    p = folders_sub.add_parser("rename", help="Rename folder")
    p.add_argument("folder_id", type=int)
    p.add_argument("name")

    p = folders_sub.add_parser("move", help="Move folder")
    p.add_argument("folder_id", type=int)
    p.add_argument("--parent", type=int, default=None)

    p = folders_sub.add_parser("delete", help="Delete folder + contents")
    p.add_argument("folder_id", type=int)

    p = folders_sub.add_parser("contents", help="Show folder contents")
    p.add_argument("folder_id", type=int)
    p.add_argument("--timeout", type=int, default=180,
                   help="HTTP read timeout in seconds (default: 180)")

    p = folders_sub.add_parser(
        "subfolders",
        help="Lightweight [{id, name}] subfolder listing for path resolution "
             "(paginated; faster than `contents` on large folders)",
    )
    p.add_argument("folder_id", type=int)
    p.add_argument("--page-size", type=int, default=1000,
                   help="Results per page (default: 1000, max enforced server-side)")
    p.add_argument("--timeout", type=int, default=180,
                   help="HTTP read timeout in seconds (default: 180)")

    # ── move / categorize (bulk file ops) ──
    p = sub.add_parser("move", help="Move files to folder")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--folder", type=int, default=None)

    p = sub.add_parser("categorize", help="Set category on files")
    p.add_argument("file_ids", nargs="+", type=int)
    p.add_argument("--category", required=True)

    # ── activities ──
    act_p = sub.add_parser("activities", help="Activity management")
    act_sub = act_p.add_subparsers(dest="activities_command")

    p = act_sub.add_parser("list", help="List activities")
    p.add_argument("--status", help="not_started,in_progress,completed")
    p.add_argument("--priority", help="0-4 (comma-sep)")
    p.add_argument("--owner", help="Owner user ID")
    p.add_argument("--assignee", help="Assignee user ID")
    p.add_argument("--type", help="Activity type ID")
    p.add_argument("--parent", type=int)
    p.add_argument("--root-only", action="store_true")
    p.add_argument("--search", "-s")
    p.add_argument("--tags")
    p.add_argument("--tags-mode", dest="tags_mode", choices=["any", "all"], help="any=OR (default), all=AND")
    p.add_argument("--after", help="Updated after date (YYYY-MM-DD or relative: last_7_days, this_month, etc.)")
    p.add_argument("--before", help="Updated before date (YYYY-MM-DD or relative: last_30_days, last_quarter, etc.)")
    p.add_argument("--created-after", help="Created after date (YYYY-MM-DD or relative)")
    p.add_argument("--created-before", help="Created before date (YYYY-MM-DD or relative)")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = act_sub.add_parser("get", help="Activity detail")
    p.add_argument("activity_id", type=int)

    p = act_sub.add_parser("create", help="Create activity")
    p.add_argument("--title", required=True)
    p.add_argument("--description")
    p.add_argument("--status", choices=["not_started", "in_progress", "completed"])
    p.add_argument("--priority", type=int, choices=[0, 1, 2, 3, 4])
    p.add_argument("--due-date")
    p.add_argument("--planned-start")
    p.add_argument("--planned-finish")
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees", help="User IDs (comma-sep)")
    p.add_argument("--type", type=int, help="Activity type ID")
    p.add_argument("--parent", type=int)
    p.add_argument("--tags")
    p.add_argument("--wbs")

    p = act_sub.add_parser("update", help="Update activity")
    p.add_argument("activity_id", type=int)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--status", choices=["not_started", "in_progress", "completed"])
    p.add_argument("--priority", type=int, choices=[0, 1, 2, 3, 4])
    p.add_argument("--percent", type=int)
    p.add_argument("--due-date")
    p.add_argument("--planned-start")
    p.add_argument("--planned-finish")
    p.add_argument("--actual-start")
    p.add_argument("--actual-finish")
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees")
    p.add_argument("--parent", type=int, help="0=root")
    p.add_argument("--tags")

    p = act_sub.add_parser("delete", help="Delete activities")
    p.add_argument("activity_ids", nargs="+", type=int)

    p = act_sub.add_parser("bulk-update", help="Bulk update activities")
    p.add_argument("activity_ids", nargs="+", type=int)
    p.add_argument("--status")
    p.add_argument("--priority", type=int)
    p.add_argument("--owner", type=int)

    act_sub.add_parser("types", help="List activity types")

    # ── steps ──
    steps_p = sub.add_parser("steps", help="Step (subtask) management")
    steps_sub = steps_p.add_subparsers(dest="steps_command")

    p = steps_sub.add_parser("list", help="List steps")
    p.add_argument("activity_id", type=int)

    p = steps_sub.add_parser("create", help="Create step")
    p.add_argument("activity_id", type=int)
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--weight", type=float)
    p.add_argument("--order", type=int)

    p = steps_sub.add_parser("update", help="Update step")
    p.add_argument("activity_id", type=int)
    p.add_argument("step_id", type=int)
    p.add_argument("--name")
    p.add_argument("--percent", type=int)
    p.add_argument("--weight", type=float)
    p.add_argument("--order", type=int)

    p = steps_sub.add_parser("delete", help="Delete step")
    p.add_argument("activity_id", type=int)
    p.add_argument("step_id", type=int)

    p = steps_sub.add_parser("from-template", help="Create steps from type template")
    p.add_argument("activity_id", type=int)

    # ── progress ──
    prog_p = sub.add_parser("progress", help="Progress tracking")
    prog_sub = prog_p.add_subparsers(dest="progress_command")

    p = prog_sub.add_parser("list", help="Progress history")
    p.add_argument("activity_id", type=int)
    p.add_argument("--source", choices=["automatic", "manual"])

    p = prog_sub.add_parser("add", help="Add manual progress")
    p.add_argument("activity_id", type=int)
    p.add_argument("--percent", type=int, required=True)
    p.add_argument("--notes")
    p.add_argument("--date", help="Effective date (ISO, for backdating)")

    p = prog_sub.add_parser("delete", help="Delete manual entry")
    p.add_argument("activity_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── deps ──
    deps_p = sub.add_parser("deps", help="Dependencies (CPM)")
    deps_sub = deps_p.add_subparsers(dest="deps_command")

    p = deps_sub.add_parser("list", help="List dependencies")
    p.add_argument("--predecessor", type=int)
    p.add_argument("--successor", type=int)
    p.add_argument("--type", dest="dep_type", choices=["FS", "SS", "FF", "SF"])

    p = deps_sub.add_parser("create", help="Create dependency")
    p.add_argument("--predecessor", type=int, required=True)
    p.add_argument("--successor", type=int, required=True)
    p.add_argument("--type", dest="dep_type", choices=["FS", "SS", "FF", "SF"], default="FS")
    p.add_argument("--lag", type=float)
    p.add_argument("--notes")

    p = deps_sub.add_parser("delete", help="Delete dependency")
    p.add_argument("dep_id", type=int)

    # ── gantt / tree ──
    p = sub.add_parser("gantt", help="Gantt view")
    p.add_argument("--status")

    p = sub.add_parser("tree", help="Activity WBS tree")
    p.add_argument("--max-depth", type=int)
    p.add_argument("--status")

    # ── forms ──
    forms_p = sub.add_parser("forms", help="Form template management")
    forms_sub = forms_p.add_subparsers(dest="forms_command")

    p = forms_sub.add_parser("list", help="List form templates")
    p.add_argument("--category", help="Filter by category")
    p.add_argument("--scope", choices=["company", "project"])
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = forms_sub.add_parser("get", help="Form detail with fields")
    p.add_argument("form_id", type=int)

    p = forms_sub.add_parser("create", help="Create form template")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--scope", choices=["company", "project"], default="project")
    p.add_argument("--category", help="Grouping label (e.g. Safety, Quality)")
    p.add_argument("--form-type", dest="form_type", help="Type identifier (e.g. safety_incident)")
    p.add_argument("--code-prefix", dest="code_prefix", help="Submission code prefix (e.g. RFI, SI)")
    p.add_argument("--code-scope", dest="code_scope", choices=["project", "company"])
    p.add_argument("--code-separator", dest="code_separator", help="Separator (default: -)")
    p.add_argument("--code-padding", dest="code_padding", type=int, help="Zero-padding (3=001)")
    p.add_argument("--private-default", dest="private_default", action="store_true")
    p.add_argument("--reviewers", help="Default reviewer user IDs (comma-sep)")

    p = forms_sub.add_parser("update", help="Update form template")
    p.add_argument("form_id", type=int)
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--category")
    p.add_argument("--form-type", dest="form_type")
    p.add_argument("--code-prefix", dest="code_prefix")
    p.add_argument("--code-scope", dest="code_scope", choices=["project", "company"])
    p.add_argument("--code-separator", dest="code_separator")
    p.add_argument("--code-padding", dest="code_padding", type=int)
    p.add_argument("--private-default", dest="private_default", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--reviewers", help="Default reviewer IDs (comma-sep, empty=clear)")

    p = forms_sub.add_parser("delete", help="Delete form template")
    p.add_argument("form_id", type=int)

    # ── fields ──
    fields_p = sub.add_parser("fields", help="Form field management")
    fields_sub = fields_p.add_subparsers(dest="fields_command")

    p = fields_sub.add_parser("list", help="List fields for a form")
    p.add_argument("form_id", type=int)

    p = fields_sub.add_parser("create", help="Create field on form")
    p.add_argument("form_id", type=int)
    p.add_argument("--label", required=True)
    p.add_argument("--type", dest="field_type", required=True, help="text, textarea, date, select, checkbox, number, email, phone, url, file, signature, etc.")
    p.add_argument("--required", action="store_true")
    p.add_argument("--order", type=int)
    p.add_argument("--placeholder")
    p.add_argument("--help-text", dest="help_text")
    p.add_argument("--options", help='JSON options (e.g. \'{"choices":["A","B","C"]}\')')
    p.add_argument("--column-span", dest="column_span", type=int)
    p.add_argument("--section", type=int, help="Section ID")

    p = fields_sub.add_parser("update", help="Update form field")
    p.add_argument("form_id", type=int)
    p.add_argument("field_id", type=int)
    p.add_argument("--label")
    p.add_argument("--type", dest="field_type")
    p.add_argument("--required", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--order", type=int)
    p.add_argument("--placeholder")
    p.add_argument("--help-text", dest="help_text")
    p.add_argument("--options", help="JSON options")
    p.add_argument("--column-span", dest="column_span", type=int)

    p = fields_sub.add_parser("delete", help="Delete form field")
    p.add_argument("form_id", type=int)
    p.add_argument("field_id", type=int)

    # ── submissions ──
    subs_p = sub.add_parser("submissions", help="Form submission management")
    subs_sub = subs_p.add_subparsers(dest="submissions_command")

    p = subs_sub.add_parser("list", help="List submissions")
    p.add_argument("--form", type=int, help="Filter by form ID")
    p.add_argument("--status", help="draft, submitted, closed")
    p.add_argument("--owner", type=int)
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-submitted_at")
    p.add_argument("--count-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = subs_sub.add_parser("get", help="Submission detail with values")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)

    p = subs_sub.add_parser("create", help="Create submission")
    p.add_argument("form_id", type=int)
    p.add_argument("--code", required=True, help="Submission code (e.g. RFI-001)")
    p.add_argument("--values", help='JSON field values (e.g. \'{"1":"text","2":"2026-01-01"}\')')
    p.add_argument("--owner", type=int)
    p.add_argument("--assignees", help="User IDs (comma-sep)")
    p.add_argument("--distribution", help="Distribution list user IDs (comma-sep)")
    p.add_argument("--private", action="store_true")
    p.add_argument("--tags")
    p.add_argument("--location-name", dest="location_name")

    p = subs_sub.add_parser("update", help="Update submission")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)
    p.add_argument("--code")
    p.add_argument("--values", help="JSON field values")
    p.add_argument("--owner", type=int, help="Owner user ID (0=clear)")
    p.add_argument("--assignees", help="User IDs (comma-sep, empty=clear)")
    p.add_argument("--distribution", help="Distribution list IDs (comma-sep, empty=clear)")
    p.add_argument("--private", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--tags", help="Tags (comma-sep, empty=clear)")
    p.add_argument("--location-name", dest="location_name")

    p = subs_sub.add_parser("delete", help="Delete submission")
    p.add_argument("form_id", type=int)
    p.add_argument("submission_id", type=int)

    # ── resources ──
    res_p = sub.add_parser("resources", help="Resource management")
    res_sub = res_p.add_subparsers(dest="resources_command")

    p = res_sub.add_parser("list", help="List resources")
    p.add_argument("--type", dest="resource_type", choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--user", type=int, help="Linked user ID")
    p.add_argument("--name", help="Name filter (substring)")
    p.add_argument("--search", "-s")
    p.add_argument("--sort", default="-created_at")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = res_sub.add_parser("get", help="Resource detail with rates")
    p.add_argument("resource_id", type=int)

    p = res_sub.add_parser("create", help="Create resource")
    p.add_argument("--name", required=True)
    p.add_argument("--type", dest="resource_type", required=True, choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--description")
    p.add_argument("--user", type=int, help="Link to user ID (personnel only)")
    p.add_argument("--unit", choices=["hours", "days", "each", "lump_sum"], default="hours")
    p.add_argument("--capacity", type=float, help="Default capacity per day (default: 8)")

    p = res_sub.add_parser("update", help="Update resource")
    p.add_argument("resource_id", type=int)
    p.add_argument("--name")
    p.add_argument("--type", dest="resource_type", choices=["personnel", "equipment", "consumable", "subcontractor"])
    p.add_argument("--description")
    p.add_argument("--user", type=int, help="User ID (0=unlink)")
    p.add_argument("--unit", choices=["hours", "days", "each", "lump_sum"])
    p.add_argument("--capacity", type=float)
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")

    p = res_sub.add_parser("delete", help="Delete resource")
    p.add_argument("resource_id", type=int)

    # ── rates ──
    rates_p = sub.add_parser("rates", help="Resource rate management")
    rates_sub = rates_p.add_subparsers(dest="rates_command")

    p = rates_sub.add_parser("list", help="List rates for a resource")
    p.add_argument("resource_id", type=int)

    p = rates_sub.add_parser("create", help="Create rate for a resource")
    p.add_argument("resource_id", type=int)
    p.add_argument("--effective-date", dest="effective_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--standard-rate", dest="standard_rate", type=float, required=True)
    p.add_argument("--cost-rate", dest="cost_rate", type=float, required=True)
    p.add_argument("--bill-rate", dest="bill_rate", type=float, required=True)
    p.add_argument("--overtime-rate", dest="overtime_rate", type=float)
    p.add_argument("--currency", default="USD", help="3-letter code (default: USD)")
    p.add_argument("--notes")

    # ── assignments ──
    assign_p = sub.add_parser("assignments", help="Resource assignments on activities")
    assign_sub = assign_p.add_subparsers(dest="assignments_command")

    p = assign_sub.add_parser("list", help="List assignments for an activity")
    p.add_argument("activity_id", type=int)

    p = assign_sub.add_parser("create", help="Create assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("--resource", dest="resource_id", type=int, required=True)
    p.add_argument("--planned-units", dest="planned_units", type=float)
    p.add_argument("--planned-per-day", dest="planned_per_day", type=float)
    p.add_argument("--curve", choices=["uniform", "front_loaded", "back_loaded", "bell"])
    p.add_argument("--driving", action="store_true", help="Resource drives activity duration")
    p.add_argument("--role", help="Role label (e.g. 'Lead Engineer')")
    p.add_argument("--start", help="Assignment start (YYYY-MM-DD)")
    p.add_argument("--end", help="Assignment end (YYYY-MM-DD)")

    p = assign_sub.add_parser("update", help="Update assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("assignment_id", type=int)
    p.add_argument("--planned-units", dest="planned_units", type=float)
    p.add_argument("--planned-per-day", dest="planned_per_day", type=float)
    p.add_argument("--remaining", type=float)
    p.add_argument("--at-completion", dest="at_completion", type=float)
    p.add_argument("--curve", choices=["uniform", "front_loaded", "back_loaded", "bell"])
    p.add_argument("--driving", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--role")
    p.add_argument("--start", help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD")

    p = assign_sub.add_parser("delete", help="Delete assignment")
    p.add_argument("activity_id", type=int)
    p.add_argument("assignment_id", type=int)

    # ── cost-codes ──
    cc_p = sub.add_parser("cost-codes", help="Cost code management (company-wide)")
    cc_sub = cc_p.add_subparsers(dest="costcodes_command")

    p = cc_sub.add_parser("list", help="List cost codes")
    p.add_argument("--code", help="Code filter (substring)")
    p.add_argument("--name", help="Name filter (substring)")
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")
    p.add_argument("--parent", type=int)
    p.add_argument("--root-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = cc_sub.add_parser("get", help="Cost code detail")
    p.add_argument("costcode_id", type=int)

    p = cc_sub.add_parser("create", help="Create cost code")
    p.add_argument("--code", required=True, help="Cost code (e.g. 03.300)")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.add_argument("--parent", type=int)
    p.add_argument("--sort-order", dest="sort_order", type=int)

    p = cc_sub.add_parser("update", help="Update cost code")
    p.add_argument("costcode_id", type=int)
    p.add_argument("--code")
    p.add_argument("--name")
    p.add_argument("--description")
    p.add_argument("--parent", type=int, help="Parent ID (0=root)")
    p.add_argument("--sort-order", dest="sort_order", type=int)
    p.add_argument("--active", type=lambda x: x.lower() in ("true", "1", "yes"), metavar="BOOL")

    p = cc_sub.add_parser("delete", help="Delete cost code")
    p.add_argument("costcode_id", type=int)

    # ── budgets ──
    bud_p = sub.add_parser("budgets", help="Cost code budgets")
    bud_sub = bud_p.add_subparsers(dest="budgets_command")

    p = bud_sub.add_parser("list", help="List budgets")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)

    p = bud_sub.add_parser("create", help="Create budget")
    p.add_argument("--cost-code", dest="cost_code", type=int, required=True)
    p.add_argument("--amount", type=float, help="Budgeted amount")
    p.add_argument("--units", type=float, help="Budgeted units")

    p = bud_sub.add_parser("update", help="Update budget")
    p.add_argument("budget_id", type=int)
    p.add_argument("--amount", type=float)
    p.add_argument("--units", type=float)

    p = bud_sub.add_parser("delete", help="Delete budget")
    p.add_argument("budget_id", type=int)

    # ── timesheets ──
    ts_p = sub.add_parser("timesheets", help="Timesheet management")
    ts_sub = ts_p.add_subparsers(dest="timesheets_command")

    p = ts_sub.add_parser("list", help="List timesheets")
    p.add_argument("--status", choices=["draft", "submitted", "approved", "rejected"])
    p.add_argument("--resource", type=int)
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])
    p.add_argument("--after", help="Period start >= date")
    p.add_argument("--before", help="Period start <= date")
    p.add_argument("--sort", default="-period_start")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = ts_sub.add_parser("get", help="Timesheet detail with entries")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("create", help="Create timesheet")
    p.add_argument("--resource", type=int, required=True, help="Resource ID")
    p.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", dest="period_end", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])

    p = ts_sub.add_parser("update", help="Update timesheet")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--period-start", dest="period_start")
    p.add_argument("--period-end", dest="period_end")
    p.add_argument("--period-type", dest="period_type", choices=["weekly", "biweekly", "monthly"])

    p = ts_sub.add_parser("delete", help="Delete timesheet")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("submit", help="Submit for approval")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("approve", help="Approve timesheet")
    p.add_argument("timesheet_id", type=int)

    p = ts_sub.add_parser("reject", help="Reject timesheet")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--reason", help="Rejection reason")

    p = ts_sub.add_parser("reopen", help="Reopen rejected timesheet")
    p.add_argument("timesheet_id", type=int)

    # ── entries (time entries) ──
    ent_p = sub.add_parser("entries", help="Time entry management")
    ent_sub = ent_p.add_subparsers(dest="entries_command")

    p = ent_sub.add_parser("list", help="List time entries")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--date", help="Exact date filter")
    p.add_argument("--after", help="Date >= filter")
    p.add_argument("--before", help="Date <= filter")
    p.add_argument("--activity", type=int)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])

    p = ent_sub.add_parser("create", help="Create time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--activity", type=int, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--hours", type=float, required=True)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])
    p.add_argument("--cost-code", dest="cost_code", type=int)
    p.add_argument("--description")

    p = ent_sub.add_parser("update", help="Update time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)
    p.add_argument("--date")
    p.add_argument("--hours", type=float)
    p.add_argument("--type", dest="entry_type", choices=["regular", "overtime", "double_time"])
    p.add_argument("--cost-code", dest="cost_code", type=int, help="Cost code ID (0=clear)")
    p.add_argument("--description")

    p = ent_sub.add_parser("delete", help="Delete time entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── cost-entries ──
    ce_p = sub.add_parser("cost-entries", help="Cost entry management")
    ce_sub = ce_p.add_subparsers(dest="costentries_command")

    p = ce_sub.add_parser("list", help="List cost entries")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--date")
    p.add_argument("--activity", type=int)
    p.add_argument("--resource", type=int)

    p = ce_sub.add_parser("create", help="Create cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("--resource", type=int, required=True)
    p.add_argument("--activity", type=int, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--quantity", type=float, required=True)
    p.add_argument("--unit-cost", dest="unit_cost", type=float, required=True)
    p.add_argument("--cost-code", dest="cost_code", type=int)
    p.add_argument("--description")

    p = ce_sub.add_parser("update", help="Update cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)
    p.add_argument("--date")
    p.add_argument("--quantity", type=float)
    p.add_argument("--unit-cost", dest="unit_cost", type=float)
    p.add_argument("--cost-code", dest="cost_code", type=int, help="0=clear")
    p.add_argument("--description")

    p = ce_sub.add_parser("delete", help="Delete cost entry")
    p.add_argument("timesheet_id", type=int)
    p.add_argument("entry_id", type=int)

    # ── locks ──
    locks_p = sub.add_parser("locks", help="Time period locks")
    locks_sub = locks_p.add_subparsers(dest="locks_command")

    locks_sub.add_parser("list", help="List time period locks")

    p = locks_sub.add_parser("create", help="Create lock")
    p.add_argument("--period-start", dest="period_start", required=True, help="YYYY-MM-DD")
    p.add_argument("--period-end", dest="period_end", required=True, help="YYYY-MM-DD")
    p.add_argument("--reason")

    p = locks_sub.add_parser("delete", help="Delete lock")
    p.add_argument("lock_id", type=int)

    # ── links ──
    # ── comments ──
    comments_p = sub.add_parser("comments", help="Activity comment management")
    comments_sub = comments_p.add_subparsers(dest="comments_command")

    p = comments_sub.add_parser("list", help="List comments on an activity")
    p.add_argument("activity_id", type=int, help="Activity ID")

    p = comments_sub.add_parser("add", help="Add a comment to an activity")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("--content", required=True, help="Comment text")

    p = comments_sub.add_parser("delete", help="Delete a comment")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("comment_id", type=int, help="Comment ID")

    p = comments_sub.add_parser("bulk", help="Bulk add comments from JSON file")
    p.add_argument("activity_id", type=int, help="Activity ID")
    p.add_argument("--file", required=True, help="JSON file with list of {content} objects")

    # ── links ──
    links_p = sub.add_parser("links", help="Entity link management")
    links_sub = links_p.add_subparsers(dest="links_command")

    p = links_sub.add_parser("list", help="List links for an object")
    p.add_argument("--source", help="Source object ref (type:id, e.g. file:170106)")
    p.add_argument("--target", help="Target object ref (type:id, e.g. activity:3710)")
    p.add_argument("--project-id", type=int, help="Project ID (default: current)")

    p = links_sub.add_parser("create", help="Create a link between two objects")
    p.add_argument("--source", required=True, help="Source object ref (type:id)")
    p.add_argument("--target", required=True, help="Target object ref (type:id)")
    p.add_argument("--type", dest="link_type", help="Link type (used as description, e.g. attachment, deliverable)")
    p.add_argument("--description", help="Description (overrides --type)")

    p = links_sub.add_parser("delete", help="Delete a link")
    p.add_argument("link_id", type=int, help="Link ID")

    p = links_sub.add_parser("bulk", help="Bulk create links from JSON file")
    p.add_argument("--file", required=True, help="JSON file with list of link objects")

    # ── chat ──
    chat_p = sub.add_parser("chat", help="AI chat (project-scoped conversation)")
    chat_sub = chat_p.add_subparsers(dest="chat_command")

    p = chat_sub.add_parser("send", help="Send a message and wait for the AI response")
    p.add_argument("message", help="User message content")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--conversation", type=int, help="Use existing conversation ID (default: current)")
    g.add_argument("--new", action="store_true", help="Start a fresh conversation for this message")
    p.add_argument("--title", help="Title for new conversation (with --new)")
    p.add_argument("--model", help="Override AI model id (see `pcxa chat models`)")
    p.add_argument("--research", action="store_true", help="Enable research_mode (file search tools)")
    p.add_argument("--page-url", help="Optional context URL passed to the agent")
    p.add_argument("--no-wait", action="store_true", help="Return immediately with agent_task_id; don't poll")
    p.add_argument("--timeout", type=int, default=180, help="Polling timeout in seconds (default: 180)")

    p = chat_sub.add_parser("ls", help="List conversations")
    p.add_argument("--search", help="Filter by title or message content")
    p.add_argument("--limit", type=int, default=20)

    p = chat_sub.add_parser("get", help="Show conversation detail (default: current)")
    p.add_argument("conversation_id", type=int, nargs="?", help="Conversation ID (default: current)")
    p.add_argument("--show-tools", action="store_true", help="Include tool calls in output")

    p = chat_sub.add_parser("new", help="Create a new conversation")
    p.add_argument("--title", help="Optional title")

    p = chat_sub.add_parser("delete", help="Archive (soft-delete) a conversation")
    p.add_argument("conversation_id", type=int)

    chat_sub.add_parser("models", help="List available AI models")

    return parser


# ─── Resolve IDs ─────────────────────────────────────────────────────────────


def resolve_ids(client):
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


# ─── Entrypoint ──────────────────────────────────────────────────────────────

# Commands that don't need a client
AUTH_FREE = {"login", "setup", "whoami", "set-project", "update"}

# ═══════════════════════════════════════════════════════════════════════════════
# FORMS — Form template management
# ═══════════════════════════════════════════════════════════════════════════════


def _form_row(f):
    return {
        "id": str(f.get("id", "")),
        "name": str(f.get("name", ""))[:40],
        "scope": f.get("scope", ""),
        "category": str(f.get("category") or "")[:15],
        "type": str(f.get("form_type") or "")[:15],
        "prefix": str(f.get("code_prefix") or ""),
        "subs": str(f.get("submissions_count", 0)),
        "created": str(f.get("created_at", ""))[:10],
    }


def cmd_forms_list(client, args):
    """List form templates."""
    params = client.paginate_params(args.limit, args.offset)
    if args.category:
        params["category"] = args.category
    if args.scope:
        params["scope"] = args.scope
    if args.search:
        params["search"] = args.search
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("forms/", params)}))
        return

    data = client.get("forms/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_form_row(f) for f in results]
    print(f"Forms: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "scope", "category", "type", "prefix", "subs", "created"])


def cmd_forms_get(client, args):
    """Show form detail with fields."""
    data = client.get(f"forms/{args.form_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Form {data.get('id')}: {data.get('name')}")
    print(f"  Scope:       {data.get('scope')}")
    print(f"  Category:    {data.get('category') or '-'}")
    print(f"  Type:        {data.get('form_type') or '-'}")
    print(f"  Code prefix: {data.get('code_prefix') or '-'} (pad={data.get('code_padding')}, sep='{data.get('code_separator', '-')}')")
    print(f"  Code scope:  {data.get('code_scope', '-')}")
    print(f"  Private def: {data.get('is_private_default', False)}")
    print(f"  Submissions: {data.get('submissions_count', 0)}")
    if data.get("description"):
        print(f"  Description: {data['description'][:200]}")
    wf = data.get("workflow_template_detail")
    if wf:
        print(f"  Workflow:    {wf.get('name', wf)}")
    reviewers = data.get("default_reviewers_details") or []
    if reviewers:
        names = ", ".join(r.get("username", str(r)) for r in reviewers)
        print(f"  Reviewers:   {names}")

    # Fetch and display fields
    try:
        fields_data = client.get(f"forms/{args.form_id}/fields/")
        fields = fields_data.get("results", fields_data) if isinstance(fields_data, dict) else fields_data
        if fields:
            print(f"\n  Fields ({len(fields)}):")
            for f in fields:
                req = "*" if f.get("is_required") else " "
                opts = ""
                if f.get("options"):
                    choices = f["options"].get("choices", [])
                    if choices:
                        opts = f" [{', '.join(str(c) for c in choices[:5])}]"
                print(f"    {req} {f.get('order', '-'):>2}. [{f.get('id')}] {f.get('label')} ({f.get('field_type')}){opts}")
    except Exception:
        pass
    print()


def cmd_forms_create(client, args):
    """Create form template."""
    payload = {
        "name": args.name,
        "scope": args.scope or "project",
        "company": client.company_id,
    }
    if (args.scope or "project") == "project":
        payload["project"] = client.project_id
    if args.description:
        payload["description"] = args.description
    if args.category:
        payload["category"] = args.category
    if args.form_type:
        payload["form_type"] = args.form_type
    if args.code_prefix:
        payload["code_prefix"] = args.code_prefix
    if args.code_scope:
        payload["code_scope"] = args.code_scope
    if args.code_separator is not None:
        payload["code_separator"] = args.code_separator
    if args.code_padding is not None:
        payload["code_padding"] = args.code_padding
    if args.private_default:
        payload["is_private_default"] = True
    if args.reviewers:
        payload["default_reviewers"] = [int(x) for x in args.reviewers.split(",")]

    if args.dry_run:
        print(f"Would CREATE form: {json.dumps(payload, indent=2)}")
        return
    data = client.post("forms/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created form {data.get('id')}: '{data.get('name')}'")


def cmd_forms_update(client, args):
    """Update form template."""
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.category is not None:
        payload["category"] = args.category or None
    if args.form_type is not None:
        payload["form_type"] = args.form_type or None
    if args.code_prefix is not None:
        payload["code_prefix"] = args.code_prefix or None
    if args.code_scope:
        payload["code_scope"] = args.code_scope
    if args.code_separator is not None:
        payload["code_separator"] = args.code_separator
    if args.code_padding is not None:
        payload["code_padding"] = args.code_padding
    if args.private_default is not None:
        payload["is_private_default"] = args.private_default
    if args.reviewers is not None:
        payload["default_reviewers"] = [int(x) for x in args.reviewers.split(",")] if args.reviewers else []
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated form {data.get('id')}: '{data.get('name')}'")


def cmd_forms_delete(client, args):
    """Delete form template."""
    if args.dry_run:
        print(f"Would DELETE form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/")
    print(f"Deleted form {args.form_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# FIELDS — Form field management
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_fields_list(client, args):
    """List fields for a form."""
    data = client.get(f"forms/{args.form_id}/fields/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for f in results:
        opts = ""
        if f.get("options"):
            choices = f["options"].get("choices", [])
            if choices:
                opts = ", ".join(str(c) for c in choices[:4])
                if len(choices) > 4:
                    opts += f" (+{len(choices)-4})"
        rows.append({
            "id": str(f.get("id", "")),
            "order": str(f.get("order", "")),
            "label": str(f.get("label", ""))[:35],
            "type": f.get("field_type", ""),
            "req": "yes" if f.get("is_required") else "",
            "options": opts[:30],
        })
    print(f"Fields for form {args.form_id}: {len(rows)}\n")
    out_table(rows, ["id", "order", "label", "type", "req", "options"])


def cmd_fields_create(client, args):
    """Create a field on a form."""
    payload = {"label": args.label, "field_type": args.field_type}
    if args.order is not None:
        payload["order"] = args.order
    if args.required:
        payload["is_required"] = True
    if args.placeholder:
        payload["placeholder"] = args.placeholder
    if args.help_text:
        payload["help_text"] = args.help_text
    if args.options:
        payload["options"] = json.loads(args.options)
    if args.column_span is not None:
        payload["column_span"] = args.column_span
    if args.section is not None:
        payload["section_id"] = args.section

    if args.dry_run:
        print(f"Would CREATE field on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"forms/{args.form_id}/fields/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created field {data.get('id')}: '{data.get('label')}' ({data.get('field_type')})")


def cmd_fields_update(client, args):
    """Update a form field."""
    payload = {}
    if args.label:
        payload["label"] = args.label
    if args.field_type:
        payload["field_type"] = args.field_type
    if args.order is not None:
        payload["order"] = args.order
    if args.required is not None:
        payload["is_required"] = args.required
    if args.placeholder is not None:
        payload["placeholder"] = args.placeholder
    if args.help_text is not None:
        payload["help_text"] = args.help_text
    if args.options:
        payload["options"] = json.loads(args.options)
    if args.column_span is not None:
        payload["column_span"] = args.column_span
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE field {args.field_id} on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/fields/{args.field_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated field {data.get('id')}: '{data.get('label')}' ({data.get('field_type')})")


def cmd_fields_delete(client, args):
    """Delete a form field."""
    if args.dry_run:
        print(f"Would DELETE field {args.field_id} from form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/fields/{args.field_id}/")
    print(f"Deleted field {args.field_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBMISSIONS — Form submission management
# ═══════════════════════════════════════════════════════════════════════════════


def _submission_row(s):
    return {
        "id": str(s.get("id", "")),
        "code": str(s.get("code", ""))[:15],
        "form": str(s.get("form_name") or s.get("form", ""))[:25],
        "status": s.get("status", ""),
        "by": str(s.get("submitted_by_username") or s.get("submitted_by") or "")[:12],
        "owner": str(s.get("owner_name") or s.get("owner") or "")[:12],
        "submitted": str(s.get("submitted_at") or "")[:10],
        "tags": tag_names(s.get("tags"))[:20],
    }


def cmd_submissions_list(client, args):
    """List form submissions."""
    params = client.paginate_params(args.limit, args.offset)
    if args.form:
        params["form"] = args.form
    if args.status:
        params["status"] = args.status
    if args.search:
        params["search"] = args.search
    if args.owner:
        params["owner"] = args.owner
    if args.sort:
        params["ordering"] = args.sort

    if args.count_only:
        print(json.dumps({"count": client.get_count("form-submissions/", params)}))
        return

    data = client.get("form-submissions/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_submission_row(s) for s in results]
    print(f"Submissions: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "form", "status", "by", "owner", "submitted", "tags"])


def cmd_submissions_get(client, args):
    """Show submission detail with values."""
    data = client.get(f"forms/{args.form_id}/submissions/{args.submission_id}/")
    if args.format == "json":
        out_json(data)
        return

    print(f"Submission {data.get('id')}: {data.get('code')}")
    print(f"  Form:      {data.get('form_name')} (id={data.get('form')})")
    print(f"  Status:    {data.get('status')}")
    print(f"  Owner:     {data.get('owner_name') or data.get('owner') or '-'}")
    print(f"  By:        {data.get('submitted_by_username') or '-'}")
    print(f"  Submitted: {str(data.get('submitted_at') or '-')[:19]}")
    if data.get("first_submitted_at"):
        print(f"  First sub: {str(data['first_submitted_at'])[:19]}")
    if data.get("closed_at"):
        print(f"  Closed:    {str(data['closed_at'])[:19]}")
    print(f"  Private:   {data.get('is_private', False)}")
    print(f"  Revision:  {data.get('current_revision_number') or '-'} ({data.get('revision_count', 0)} total)")

    assignees = data.get("assignees_details") or data.get("assignees") or []
    if assignees:
        names = ", ".join(
            a.get("username", str(a)) if isinstance(a, dict) else str(a)
            for a in assignees
        )
        print(f"  Assignees: {names}")

    tags = data.get("tags") or []
    if tags:
        print(f"  Tags:      {tag_names(tags)}")

    loc = data.get("location_name")
    if loc:
        print(f"  Location:  {loc}")

    values = data.get("values") or {}
    if values:
        print(f"\n  Values ({len(values)}):")
        # Try to fetch field labels for display
        field_labels = {}
        try:
            fields_data = client.get(f"forms/{data.get('form', args.form_id)}/fields/")
            fields = fields_data.get("results", fields_data) if isinstance(fields_data, dict) else fields_data
            field_labels = {str(f["id"]): f.get("label", f"field_{f['id']}") for f in fields}
        except Exception:
            pass
        for fid, val in values.items():
            label = field_labels.get(str(fid), f"field_{fid}")
            print(f"    {label}: {val}")
    print()


def cmd_submissions_create(client, args):
    """Create a form submission."""
    payload = {
        "form": args.form_id,
        "company": client.company_id,
        "project": client.project_id,
        "code": args.code,
    }
    if args.values:
        payload["values"] = json.loads(args.values)
    if args.owner:
        payload["owner"] = args.owner
    if args.assignees:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")]
    if args.distribution:
        payload["distribution_list"] = [int(x) for x in args.distribution.split(",")]
    if args.private:
        payload["is_private"] = True
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.location_name:
        payload["location_name"] = args.location_name

    if args.dry_run:
        print(f"Would CREATE submission on form {args.form_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"forms/{args.form_id}/submissions/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created submission {data.get('id')}: code='{data.get('code')}' status={data.get('status')}")


def cmd_submissions_update(client, args):
    """Update a form submission."""
    payload = {}
    if args.values:
        payload["values"] = json.loads(args.values)
    if args.code:
        payload["code"] = args.code
    if args.owner is not None:
        payload["owner"] = args.owner if args.owner != 0 else None
    if args.assignees is not None:
        payload["assignees"] = [int(x) for x in args.assignees.split(",")] if args.assignees else []
    if args.distribution is not None:
        payload["distribution_list"] = [int(x) for x in args.distribution.split(",")] if args.distribution else []
    if args.private is not None:
        payload["is_private"] = args.private
    if args.tags is not None:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    if args.location_name is not None:
        payload["location_name"] = args.location_name
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE submission {args.submission_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"forms/{args.form_id}/submissions/{args.submission_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated submission {data.get('id')}: code='{data.get('code')}' status={data.get('status')}")


def cmd_submissions_delete(client, args):
    """Delete a form submission."""
    if args.dry_run:
        print(f"Would DELETE submission {args.submission_id} from form {args.form_id}")
        return
    client.delete(f"forms/{args.form_id}/submissions/{args.submission_id}/")
    print(f"Deleted submission {args.submission_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# RESOURCES — People, equipment, consumables, subcontractors
# ═══════════════════════════════════════════════════════════════════════════════


def _resource_row(r):
    return {
        "id": str(r.get("id", "")),
        "name": str(r.get("name", ""))[:35],
        "type": r.get("resource_type", ""),
        "unit": r.get("unit_of_measure", ""),
        "cap/day": str(r.get("default_capacity_per_day", "")),
        "user": str(r.get("user") or "-"),
        "active": "yes" if r.get("is_active") else "no",
        "assigns": str(r.get("assignments_count", 0)),
    }


def cmd_resources_list(client, args):
    """List resources."""
    params = client.paginate_params(args.limit, args.offset)
    if args.resource_type:
        params["resource_type"] = args.resource_type
    if args.active is not None:
        params["is_active"] = str(args.active).lower()
    if args.user:
        params["user"] = args.user
    if args.name:
        params["name"] = args.name
    if args.search:
        params["search"] = args.search
    if args.sort:
        params["ordering"] = args.sort
    data = client.get("resources/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_resource_row(r) for r in results]
    print(f"Resources: {len(rows)} of {total}\n")
    out_table(rows, ["id", "name", "type", "unit", "cap/day", "user", "active", "assigns"])


def cmd_resources_get(client, args):
    """Resource detail with rates."""
    data = client.get(f"resources/{args.resource_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Resource {data.get('id')}: {data.get('name')}")
    print(f"  Type:     {data.get('resource_type')}")
    print(f"  Unit:     {data.get('unit_of_measure')}")
    print(f"  Cap/Day:  {data.get('default_capacity_per_day')}")
    print(f"  Active:   {data.get('is_active')}")
    print(f"  User:     {data.get('user') or '-'}")
    if data.get("description"):
        print(f"  Desc:     {data['description'][:200]}")
    rates = data.get("rates") or []
    if rates:
        print(f"\n  Rates ({len(rates)}):")
        for r in rates:
            print(f"    [{r.get('id')}] {r.get('effective_date')}: std={r.get('standard_rate')} ot={r.get('overtime_rate', '-')} cost={r.get('cost_rate')} bill={r.get('bill_rate')} {r.get('currency', 'USD')}")
    print()


def cmd_resources_create(client, args):
    """Create resource."""
    payload = {"name": args.name, "resource_type": args.resource_type}
    if args.description:
        payload["description"] = args.description
    if args.user:
        payload["user"] = args.user
    if args.unit:
        payload["unit_of_measure"] = args.unit
    if args.capacity is not None:
        payload["default_capacity_per_day"] = str(args.capacity)
    if args.dry_run:
        print(f"Would CREATE resource: {json.dumps(payload, indent=2)}")
        return
    data = client.post("resources/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created resource {data.get('id')}: '{data.get('name')}' ({data.get('resource_type')})")


def cmd_resources_update(client, args):
    """Update resource."""
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.resource_type:
        payload["resource_type"] = args.resource_type
    if args.description is not None:
        payload["description"] = args.description
    if args.user is not None:
        payload["user"] = args.user if args.user != 0 else None
    if args.unit:
        payload["unit_of_measure"] = args.unit
    if args.capacity is not None:
        payload["default_capacity_per_day"] = str(args.capacity)
    if args.active is not None:
        payload["is_active"] = args.active
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE resource {args.resource_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"resources/{args.resource_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated resource {data.get('id')}: '{data.get('name')}'")


def cmd_resources_delete(client, args):
    """Delete resource."""
    if args.dry_run:
        print(f"Would DELETE resource {args.resource_id}")
        return
    client.delete(f"resources/{args.resource_id}/")
    print(f"Deleted resource {args.resource_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# RATES — Resource rate history (append-only)
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_rates_list(client, args):
    """List rates for a resource."""
    data = client.get(f"resources/{args.resource_id}/rates/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for r in results:
        rows.append({
            "id": str(r.get("id", "")),
            "effective": str(r.get("effective_date", "")),
            "standard": str(r.get("standard_rate", "")),
            "overtime": str(r.get("overtime_rate") or "-"),
            "cost": str(r.get("cost_rate", "")),
            "bill": str(r.get("bill_rate", "")),
            "currency": r.get("currency", "USD"),
            "notes": str(r.get("notes", ""))[:30],
        })
    print(f"Rates for resource {args.resource_id}: {len(rows)}\n")
    out_table(rows, ["id", "effective", "standard", "overtime", "cost", "bill", "currency", "notes"])


def cmd_rates_create(client, args):
    """Create a new rate for a resource."""
    payload = {
        "effective_date": args.effective_date,
        "standard_rate": str(args.standard_rate),
        "cost_rate": str(args.cost_rate),
        "bill_rate": str(args.bill_rate),
    }
    if args.overtime_rate is not None:
        payload["overtime_rate"] = str(args.overtime_rate)
    if args.currency:
        payload["currency"] = args.currency
    if args.notes:
        payload["notes"] = args.notes
    if args.dry_run:
        print(f"Would CREATE rate on resource {args.resource_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"resources/{args.resource_id}/rates/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created rate {data.get('id')}: effective={data.get('effective_date')} std={data.get('standard_rate')}")


# ═══════════════════════════════════════════════════════════════════════════════
# ASSIGNMENTS — Resource assignments on activities
# ═══════════════════════════════════════════════════════════════════════════════


def _assignment_row(a):
    return {
        "id": str(a.get("id", "")),
        "resource": str(a.get("resource_name") or a.get("resource", ""))[:25],
        "type": a.get("resource_type", ""),
        "planned": str(a.get("planned_units", "")),
        "actual": str(a.get("actual_units", 0)),
        "remaining": str(a.get("remaining_units") or "-"),
        "role": str(a.get("role_label") or "")[:20],
        "driving": "yes" if a.get("is_driving") else "",
    }


def cmd_assignments_list(client, args):
    """List resource assignments for an activity."""
    data = client.get(f"activities/{args.activity_id}/resource-assignments/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = [_assignment_row(a) for a in results]
    print(f"Assignments for activity {args.activity_id}: {len(rows)}\n")
    out_table(rows, ["id", "resource", "type", "planned", "actual", "remaining", "role", "driving"])


def cmd_assignments_create(client, args):
    """Create resource assignment."""
    payload = {"resource": args.resource_id}
    if args.planned_units is not None:
        payload["planned_units"] = str(args.planned_units)
    if args.planned_per_day is not None:
        payload["planned_units_per_day"] = str(args.planned_per_day)
    if args.curve:
        payload["resource_curve"] = args.curve
    if args.driving:
        payload["is_driving"] = True
    if args.role:
        payload["role_label"] = args.role
    if args.start:
        payload["assignment_start"] = args.start
    if args.end:
        payload["assignment_end"] = args.end
    if args.dry_run:
        print(f"Would CREATE assignment on activity {args.activity_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"activities/{args.activity_id}/resource-assignments/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created assignment {data.get('id')}: resource={data.get('resource')} planned={data.get('planned_units')}")


def cmd_assignments_update(client, args):
    """Update resource assignment."""
    payload = {}
    if args.planned_units is not None:
        payload["planned_units"] = str(args.planned_units)
    if args.planned_per_day is not None:
        payload["planned_units_per_day"] = str(args.planned_per_day)
    if args.remaining is not None:
        payload["remaining_units"] = str(args.remaining)
    if args.at_completion is not None:
        payload["at_completion_units"] = str(args.at_completion)
    if args.curve:
        payload["resource_curve"] = args.curve
    if args.driving is not None:
        payload["is_driving"] = args.driving
    if args.role is not None:
        payload["role_label"] = args.role
    if args.start:
        payload["assignment_start"] = args.start
    if args.end:
        payload["assignment_end"] = args.end
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE assignment {args.assignment_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"activities/{args.activity_id}/resource-assignments/{args.assignment_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated assignment {data.get('id')}")


def cmd_assignments_delete(client, args):
    """Delete resource assignment."""
    if args.dry_run:
        print(f"Would DELETE assignment {args.assignment_id}")
        return
    client.delete(f"activities/{args.activity_id}/resource-assignments/{args.assignment_id}/")
    print(f"Deleted assignment {args.assignment_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# COST CODES — Hierarchical cost tracking codes (company-scoped)
# ═══════════════════════════════════════════════════════════════════════════════


def _costcode_row(c):
    return {
        "id": str(c.get("id", "")),
        "code": str(c.get("code", "")),
        "name": str(c.get("name", ""))[:35],
        "parent": str(c.get("parent") or "-"),
        "active": "yes" if c.get("is_active") else "no",
        "children": str(c.get("children_count", 0)),
    }


def cmd_costcodes_list(client, args):
    """List cost codes."""
    params = client.paginate_params(args.limit, args.offset)
    if args.code:
        params["code"] = args.code
    if args.name:
        params["name"] = args.name
    if args.active is not None:
        params["is_active"] = str(args.active).lower()
    if args.parent:
        params["parent"] = args.parent
    if args.root_only:
        params["root_only"] = "true"
    data = client.get("cost-codes/", params, project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_costcode_row(c) for c in results]
    print(f"Cost codes: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "name", "parent", "active", "children"])


def cmd_costcodes_get(client, args):
    """Cost code detail."""
    data = client.get(f"cost-codes/{args.costcode_id}/", project_scoped=False)
    if args.format == "json":
        out_json(data)
        return
    print(f"Cost Code {data.get('id')}: {data.get('code')} — {data.get('name')}")
    print(f"  Parent:   {data.get('parent') or '-'}")
    print(f"  Active:   {data.get('is_active')}")
    if data.get("description"):
        print(f"  Desc:     {data['description'][:200]}")
    children = data.get("children") or []
    if children:
        print(f"\n  Children ({len(children)}):")
        for c in children:
            print(f"    [{c.get('id')}] {c.get('code')} — {c.get('name')}")
    print()


def cmd_costcodes_create(client, args):
    """Create cost code."""
    payload = {"code": args.code, "name": args.name}
    if args.description:
        payload["description"] = args.description
    if args.parent:
        payload["parent"] = args.parent
    if args.sort_order is not None:
        payload["sort_order"] = args.sort_order
    if args.dry_run:
        print(f"Would CREATE cost code: {json.dumps(payload, indent=2)}")
        return
    data = client.post("cost-codes/", payload, project_scoped=False)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created cost code {data.get('id')}: {data.get('code')} — {data.get('name')}")


def cmd_costcodes_update(client, args):
    """Update cost code."""
    payload = {}
    if args.code:
        payload["code"] = args.code
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if args.parent is not None:
        payload["parent"] = args.parent if args.parent != 0 else None
    if args.sort_order is not None:
        payload["sort_order"] = args.sort_order
    if args.active is not None:
        payload["is_active"] = args.active
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE cost code {args.costcode_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"cost-codes/{args.costcode_id}/", payload, project_scoped=False)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated cost code {data.get('id')}: {data.get('code')}")


def cmd_costcodes_delete(client, args):
    """Delete cost code."""
    if args.dry_run:
        print(f"Would DELETE cost code {args.costcode_id}")
        return
    client.delete(f"cost-codes/{args.costcode_id}/", project_scoped=False)
    print(f"Deleted cost code {args.costcode_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# BUDGETS — Project cost code budgets
# ═══════════════════════════════════════════════════════════════════════════════


def _budget_row(b):
    return {
        "id": str(b.get("id", "")),
        "code": str(b.get("cost_code_code") or "")[:15],
        "name": str(b.get("cost_code_name") or "")[:30],
        "amount": str(b.get("budgeted_amount", 0)),
        "units": str(b.get("budgeted_units", 0)),
    }


def cmd_budgets_list(client, args):
    """List cost code budgets."""
    params = client.paginate_params(args.limit, args.offset)
    data = client.get("cost-code-budgets/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_budget_row(b) for b in results]
    print(f"Budgets: {len(rows)} of {total}\n")
    out_table(rows, ["id", "code", "name", "amount", "units"])


def cmd_budgets_create(client, args):
    """Create cost code budget."""
    payload = {"cost_code": args.cost_code}
    if args.amount is not None:
        payload["budgeted_amount"] = str(args.amount)
    if args.units is not None:
        payload["budgeted_units"] = str(args.units)
    if args.dry_run:
        print(f"Would CREATE budget: {json.dumps(payload, indent=2)}")
        return
    data = client.post("cost-code-budgets/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created budget {data.get('id')}: cost_code={data.get('cost_code')} amount={data.get('budgeted_amount')}")


def cmd_budgets_update(client, args):
    """Update cost code budget."""
    payload = {}
    if args.amount is not None:
        payload["budgeted_amount"] = str(args.amount)
    if args.units is not None:
        payload["budgeted_units"] = str(args.units)
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE budget {args.budget_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"cost-code-budgets/{args.budget_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated budget {data.get('id')}")


def cmd_budgets_delete(client, args):
    """Delete cost code budget."""
    if args.dry_run:
        print(f"Would DELETE budget {args.budget_id}")
        return
    client.delete(f"cost-code-budgets/{args.budget_id}/")
    print(f"Deleted budget {args.budget_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# TIMESHEETS — Time & cost tracking with approval workflow
# ═══════════════════════════════════════════════════════════════════════════════


def _timesheet_row(t):
    return {
        "id": str(t.get("id", "")),
        "resource": str(t.get("resource_name") or t.get("resource", ""))[:20],
        "period": f"{str(t.get('period_start', ''))[:10]}..{str(t.get('period_end', ''))[:10]}",
        "type": t.get("period_type", ""),
        "status": t.get("status", ""),
        "hours": str(t.get("total_hours", 0)),
        "cost": str(t.get("total_cost", 0)),
        "entries": str(t.get("entries_count", 0)),
    }


def cmd_timesheets_list(client, args):
    """List timesheets."""
    params = client.paginate_params(args.limit, args.offset)
    if args.status:
        params["status"] = args.status
    if args.resource:
        params["resource"] = args.resource
    if args.period_type:
        params["period_type"] = args.period_type
    if args.after:
        params["period_start_after"] = args.after
    if args.before:
        params["period_start_before"] = args.before
    if args.sort:
        params["ordering"] = args.sort
    data = client.get("timesheets/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    total = data.get("count", len(results)) if isinstance(data, dict) else len(results)
    rows = [_timesheet_row(t) for t in results]
    print(f"Timesheets: {len(rows)} of {total}\n")
    out_table(rows, ["id", "resource", "period", "type", "status", "hours", "cost", "entries"])


def cmd_timesheets_get(client, args):
    """Timesheet detail with entries."""
    data = client.get(f"timesheets/{args.timesheet_id}/")
    if args.format == "json":
        out_json(data)
        return
    print(f"Timesheet {data.get('id')}")
    print(f"  Resource:  {data.get('resource_name') or data.get('resource')}")
    print(f"  Period:    {data.get('period_start')} to {data.get('period_end')} ({data.get('period_type')})")
    print(f"  Status:    {data.get('status')} {'(editable)' if data.get('is_editable') else '(locked)'}")
    print(f"  Hours:     {data.get('total_regular_hours', 0)} regular + {data.get('total_overtime_hours', 0)} OT = {data.get('total_hours', 0)} total")
    print(f"  Cost:      {data.get('total_cost', 0)}  Billable: {data.get('total_billable', 0)}")
    if data.get("submitted_by"):
        print(f"  Submitted: {str(data.get('submitted_at', ''))[:19]} by {data.get('submitted_by')}")
    if data.get("approved_by"):
        print(f"  Approved:  {str(data.get('approved_at', ''))[:19]} by {data.get('approved_by')}")
    if data.get("rejection_reason"):
        print(f"  Rejected:  {data['rejection_reason'][:200]}")

    entries = data.get("entries") or []
    if entries:
        print(f"\n  Time Entries ({len(entries)}):")
        for e in entries:
            desc = f" ({e['description']})" if e.get("description") else ""
            print(f"    [{e.get('id')}] {e.get('date')} {e.get('hours')}h {e.get('entry_type')} — {e.get('activity_title') or e.get('activity')}{desc}")

    cost_entries = data.get("cost_entries") or []
    if cost_entries:
        print(f"\n  Cost Entries ({len(cost_entries)}):")
        for e in cost_entries:
            print(f"    [{e.get('id')}] {e.get('date')} qty={e.get('quantity')} @{e.get('unit_cost')} = {e.get('total_cost')} — {e.get('activity_title') or e.get('activity')}")
    print()


def cmd_timesheets_create(client, args):
    """Create timesheet."""
    payload = {
        "resource": args.resource,
        "period_start": args.period_start,
        "period_end": args.period_end,
    }
    if args.period_type:
        payload["period_type"] = args.period_type
    if args.dry_run:
        print(f"Would CREATE timesheet: {json.dumps(payload, indent=2)}")
        return
    data = client.post("timesheets/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created timesheet {data.get('id')}: {data.get('period_start')} to {data.get('period_end')} ({data.get('status')})")


def cmd_timesheets_update(client, args):
    """Update timesheet."""
    payload = {}
    if args.period_start:
        payload["period_start"] = args.period_start
    if args.period_end:
        payload["period_end"] = args.period_end
    if args.period_type:
        payload["period_type"] = args.period_type
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE timesheet {args.timesheet_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated timesheet {data.get('id')}")


def cmd_timesheets_delete(client, args):
    """Delete timesheet."""
    if args.dry_run:
        print(f"Would DELETE timesheet {args.timesheet_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/")
    print(f"Deleted timesheet {args.timesheet_id}")


def cmd_timesheets_submit(client, args):
    """Submit timesheet for approval."""
    if args.dry_run:
        print(f"Would SUBMIT timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/submit/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Submitted timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_approve(client, args):
    """Approve timesheet."""
    if args.dry_run:
        print(f"Would APPROVE timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/approve/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Approved timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_reject(client, args):
    """Reject timesheet."""
    payload = {}
    if args.reason:
        payload["rejection_reason"] = args.reason
    if args.dry_run:
        print(f"Would REJECT timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/reject/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Rejected timesheet {data.get('id')} (status={data.get('status')})")


def cmd_timesheets_reopen(client, args):
    """Reopen rejected timesheet."""
    if args.dry_run:
        print(f"Would REOPEN timesheet {args.timesheet_id}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/reopen/")
    if args.format == "json":
        out_json(data)
    else:
        print(f"Reopened timesheet {data.get('id')} (status={data.get('status')})")


# ═══════════════════════════════════════════════════════════════════════════════
# TIME ENTRIES — Hours logged against activities
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_entries_list(client, args):
    """List time entries for a timesheet."""
    params = {}
    if args.date:
        params["date"] = args.date
    if args.after:
        params["date_after"] = args.after
    if args.before:
        params["date_before"] = args.before
    if args.activity:
        params["activity"] = args.activity
    if args.entry_type:
        params["entry_type"] = args.entry_type
    data = client.get(f"timesheets/{args.timesheet_id}/entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("date", "")),
            "hours": str(e.get("hours", "")),
            "type": e.get("entry_type", ""),
            "activity": str(e.get("activity_title") or e.get("activity", ""))[:25],
            "cost_code": str(e.get("cost_code") or "-"),
            "desc": str(e.get("description", ""))[:30],
        })
    print(f"Time entries for timesheet {args.timesheet_id}: {len(rows)}\n")
    out_table(rows, ["id", "date", "hours", "type", "activity", "cost_code", "desc"])


def cmd_entries_create(client, args):
    """Create time entry."""
    payload = {
        "activity": args.activity,
        "date": args.date,
        "hours": str(args.hours),
    }
    if args.entry_type:
        payload["entry_type"] = args.entry_type
    if args.cost_code:
        payload["cost_code"] = args.cost_code
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE time entry: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created entry {data.get('id')}: {data.get('date')} {data.get('hours')}h on activity {data.get('activity')}")


def cmd_entries_update(client, args):
    """Update time entry."""
    payload = {}
    if args.date:
        payload["date"] = args.date
    if args.hours is not None:
        payload["hours"] = str(args.hours)
    if args.entry_type:
        payload["entry_type"] = args.entry_type
    if args.cost_code is not None:
        payload["cost_code"] = args.cost_code if args.cost_code != 0 else None
    if args.description is not None:
        payload["description"] = args.description
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE entry {args.entry_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/entries/{args.entry_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated entry {data.get('id')}")


def cmd_entries_delete(client, args):
    """Delete time entry."""
    if args.dry_run:
        print(f"Would DELETE entry {args.entry_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/entries/{args.entry_id}/")
    print(f"Deleted entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# COST ENTRIES — Non-labor costs logged against activities
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_costentries_list(client, args):
    """List cost entries for a timesheet."""
    params = {}
    if args.date:
        params["date"] = args.date
    if args.activity:
        params["activity"] = args.activity
    if args.resource:
        params["resource"] = args.resource
    data = client.get(f"timesheets/{args.timesheet_id}/cost-entries/", params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for e in results:
        rows.append({
            "id": str(e.get("id", "")),
            "date": str(e.get("date", "")),
            "resource": str(e.get("resource_name") or e.get("resource", ""))[:15],
            "activity": str(e.get("activity_title") or e.get("activity", ""))[:20],
            "qty": str(e.get("quantity", "")),
            "unit_cost": str(e.get("unit_cost", "")),
            "total": str(e.get("total_cost", "")),
            "desc": str(e.get("description", ""))[:25],
        })
    print(f"Cost entries for timesheet {args.timesheet_id}: {len(rows)}\n")
    out_table(rows, ["id", "date", "resource", "activity", "qty", "unit_cost", "total", "desc"])


def cmd_costentries_create(client, args):
    """Create cost entry."""
    payload = {
        "resource": args.resource,
        "activity": args.activity,
        "date": args.date,
        "quantity": str(args.quantity),
        "unit_cost": str(args.unit_cost),
    }
    if args.cost_code:
        payload["cost_code"] = args.cost_code
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        print(f"Would CREATE cost entry: {json.dumps(payload, indent=2)}")
        return
    data = client.post(f"timesheets/{args.timesheet_id}/cost-entries/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created cost entry {data.get('id')}: qty={data.get('quantity')} @{data.get('unit_cost')} = {data.get('total_cost')}")


def cmd_costentries_update(client, args):
    """Update cost entry."""
    payload = {}
    if args.date:
        payload["date"] = args.date
    if args.quantity is not None:
        payload["quantity"] = str(args.quantity)
    if args.unit_cost is not None:
        payload["unit_cost"] = str(args.unit_cost)
    if args.cost_code is not None:
        payload["cost_code"] = args.cost_code if args.cost_code != 0 else None
    if args.description is not None:
        payload["description"] = args.description
    if not payload:
        print("No fields to update.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print(f"Would UPDATE cost entry {args.entry_id}: {json.dumps(payload, indent=2)}")
        return
    data = client.patch(f"timesheets/{args.timesheet_id}/cost-entries/{args.entry_id}/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Updated cost entry {data.get('id')}")


def cmd_costentries_delete(client, args):
    """Delete cost entry."""
    if args.dry_run:
        print(f"Would DELETE cost entry {args.entry_id}")
        return
    client.delete(f"timesheets/{args.timesheet_id}/cost-entries/{args.entry_id}/")
    print(f"Deleted cost entry {args.entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# TIME PERIOD LOCKS — Prevent edits to specific date ranges
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_locks_list(client, args):
    """List time period locks."""
    data = client.get("time-period-locks/")
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for lk in results:
        rows.append({
            "id": str(lk.get("id", "")),
            "start": str(lk.get("period_start", "")),
            "end": str(lk.get("period_end", "")),
            "locked_by": str(lk.get("locked_by") or "-"),
            "reason": str(lk.get("reason", ""))[:40],
            "created": str(lk.get("created_at", ""))[:10],
        })
    print(f"Time period locks: {len(rows)}\n")
    out_table(rows, ["id", "start", "end", "locked_by", "reason", "created"])


def cmd_locks_create(client, args):
    """Create time period lock."""
    payload = {
        "period_start": args.period_start,
        "period_end": args.period_end,
    }
    if args.reason:
        payload["reason"] = args.reason
    if args.dry_run:
        print(f"Would CREATE lock: {json.dumps(payload, indent=2)}")
        return
    data = client.post("time-period-locks/", payload)
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created lock {data.get('id')}: {data.get('period_start')} to {data.get('period_end')}")


def cmd_locks_delete(client, args):
    """Delete time period lock."""
    if args.dry_run:
        print(f"Would DELETE lock {args.lock_id}")
        return
    client.delete(f"time-period-locks/{args.lock_id}/")
    print(f"Deleted lock {args.lock_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# LINKS (Generic Links — not project-scoped)
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_object_ref(ref):
    """Parse 'type:id' reference into (type_string, object_id). E.g. 'file:170106' -> ('file', 170106)."""
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


def _links_url(client, path=""):
    """Build URL for generic-links endpoint (top-level, not project-scoped)."""
    return f"{client.base_url}/api/generic-links/{path}"


def cmd_links_list(client, args):
    """List links filtered by source or target object."""
    params = {}
    if args.source:
        src_type, src_id = _parse_object_ref(args.source)
        params["source_type"] = src_type
        params["source_object_id"] = src_id
    if args.target:
        tgt_type, tgt_id = _parse_object_ref(args.target)
        params["target_type"] = tgt_type
        params["target_object_id"] = tgt_id
    if args.project_id:
        params["project_id"] = args.project_id
    else:
        # Default to current project
        params["project_id"] = client.project_id

    data = client.get_raw(_links_url(client), params=params)
    if args.format == "json":
        out_json(data)
        return
    results = data.get("results", data) if isinstance(data, dict) else data
    rows = []
    for lnk in results:
        src = lnk.get("source_object") or {}
        tgt = lnk.get("target_object") or {}
        rows.append({
            "id": str(lnk.get("id", "")),
            "source": f"{src.get('type', '?')}:{src.get('id', '?')}",
            "source_name": str(src.get("title") or src.get("name", ""))[:30],
            "target": f"{tgt.get('type', '?')}:{tgt.get('id', '?')}",
            "target_name": str(tgt.get("title") or tgt.get("name", ""))[:30],
            "description": str(lnk.get("description") or "")[:35],
        })
    print(f"Links: {len(rows)}\n")
    out_table(rows, ["id", "source", "source_name", "target", "target_name", "description"])


def cmd_links_create(client, args):
    """Create a single link between two objects."""
    src_type, src_id = _parse_object_ref(args.source)
    tgt_type, tgt_id = _parse_object_ref(args.target)
    description = args.description or args.link_type or ""
    payload = {
        "source_type": src_type,
        "source_id": src_id,
        "target_type": tgt_type,
        "target_id": tgt_id,
    }
    if description:
        payload["description"] = description
    if args.dry_run:
        print(f"Would CREATE link: {payload}")
        return
    resp = client._request("POST", _links_url(client, "create-attachment/"), json=payload)
    data = resp.json()
    if args.format == "json":
        out_json(data)
    else:
        print(f"Created link {data.get('id')}: {src_type}:{src_id} -> {tgt_type}:{tgt_id} ({description})")


def cmd_links_delete(client, args):
    """Delete a link by ID."""
    if args.dry_run:
        print(f"Would DELETE link {args.link_id}")
        return
    client._request("DELETE", _links_url(client, f"{args.link_id}/"))
    print(f"Deleted link {args.link_id}")


def cmd_links_bulk(client, args):
    """Bulk create links from a JSON file."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(file_path.read_text())
    # Support both bare list and wrapper object with "links" key
    if isinstance(raw, dict):
        links = raw.get("links")
        if not isinstance(links, list):
            print("JSON object must contain a 'links' key with a list of link objects.", file=sys.stderr)
            sys.exit(1)
    elif isinstance(raw, list):
        links = raw
    else:
        print("JSON file must contain a list or an object with a 'links' key.", file=sys.stderr)
        sys.exit(1)

    created = 0
    errors = []
    for i, link in enumerate(links):
        # Support both "source": "file:123" shorthand and explicit "source_type"/"source_id" fields
        if "source" in link and isinstance(link["source"], str):
            src_type, src_id = _parse_object_ref(link["source"])
        else:
            src_type = link.get("source_type")
            src_id = link.get("source_id")

        if "target" in link and isinstance(link["target"], str):
            tgt_type, tgt_id = _parse_object_ref(link["target"])
        else:
            tgt_type = link.get("target_type")
            tgt_id = link.get("target_id")

        if not all([src_type, src_id, tgt_type, tgt_id]):
            errors.append(f"[{i}] Missing source/target fields")
            continue

        description = link.get("description") or link.get("type") or ""
        payload = {
            "source_type": src_type,
            "source_id": src_id,
            "target_type": tgt_type,
            "target_id": tgt_id,
        }
        if description:
            payload["description"] = description

        if args.dry_run:
            print(f"  [{i}] Would CREATE: {src_type}:{src_id} -> {tgt_type}:{tgt_id} ({description})")
            continue

        try:
            resp = client._request("POST", _links_url(client, "create-attachment/"), json=payload)
            data = resp.json()
            created += 1
            if args.format != "json":
                print(f"  [{i}] Created link {data.get('id')}: {src_type}:{src_id} -> {tgt_type}:{tgt_id}")
        except Exception as e:
            errors.append(f"[{i}] {src_type}:{src_id} -> {tgt_type}:{tgt_id}: {e}")

    if args.dry_run:
        print(f"\nDry run: {len(links)} links would be created")
    else:
        print(f"\nBulk complete: {created} created, {len(errors)} errors")
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  {err}")


HANDLERS = {
    "whoami": lambda c, a: cmd_whoami(c, a),
    "move": cmd_move,
    "categorize": cmd_categorize,
    "gantt": cmd_gantt,
    "tree": cmd_tree,
}

SUB_HANDLERS = {
    "project": {"get": cmd_project_get, "update": cmd_project_update, "members": cmd_project_members},
    "files": {
        "list": cmd_files_list, "search": cmd_files_search, "content": cmd_files_content,
        "read": cmd_files_read, "info": cmd_files_info, "stats": cmd_files_stats,
        "aggregate": cmd_files_aggregate, "recent": cmd_files_recent, "download": cmd_files_download,
        "upload": cmd_files_upload, "upload-version": cmd_files_upload_version,
        "update": cmd_file_update,
        "delete": cmd_files_delete, "restore": cmd_files_restore,
    },
    "tags": {"list": cmd_tags_list, "add": cmd_tags_add, "remove": cmd_tags_remove, "set": cmd_tags_set},
    "folders": {
        "tree": cmd_folders_tree, "create": cmd_folders_create, "rename": cmd_folders_rename,
        "move": cmd_folders_move, "delete": cmd_folders_delete, "contents": cmd_folders_contents,
        "subfolders": cmd_folders_subfolders,
    },
    "activities": {
        "list": cmd_activities_list, "get": cmd_activities_get, "create": cmd_activities_create,
        "update": cmd_activities_update, "delete": cmd_activities_delete,
        "bulk-update": cmd_activities_bulk_update, "types": cmd_activities_types,
    },
    "steps": {
        "list": cmd_steps_list, "create": cmd_steps_create, "update": cmd_steps_update,
        "delete": cmd_steps_delete, "from-template": cmd_steps_from_template,
    },
    "progress": {"list": cmd_progress_list, "add": cmd_progress_add, "delete": cmd_progress_delete},
    "deps": {"list": cmd_deps_list, "create": cmd_deps_create, "delete": cmd_deps_delete},
    "forms": {
        "list": cmd_forms_list, "get": cmd_forms_get, "create": cmd_forms_create,
        "update": cmd_forms_update, "delete": cmd_forms_delete,
    },
    "fields": {
        "list": cmd_fields_list, "create": cmd_fields_create,
        "update": cmd_fields_update, "delete": cmd_fields_delete,
    },
    "submissions": {
        "list": cmd_submissions_list, "get": cmd_submissions_get, "create": cmd_submissions_create,
        "update": cmd_submissions_update, "delete": cmd_submissions_delete,
    },
    "resources": {
        "list": cmd_resources_list, "get": cmd_resources_get, "create": cmd_resources_create,
        "update": cmd_resources_update, "delete": cmd_resources_delete,
    },
    "rates": {"list": cmd_rates_list, "create": cmd_rates_create},
    "assignments": {
        "list": cmd_assignments_list, "create": cmd_assignments_create,
        "update": cmd_assignments_update, "delete": cmd_assignments_delete,
    },
    "cost-codes": {
        "list": cmd_costcodes_list, "get": cmd_costcodes_get, "create": cmd_costcodes_create,
        "update": cmd_costcodes_update, "delete": cmd_costcodes_delete,
    },
    "budgets": {
        "list": cmd_budgets_list, "create": cmd_budgets_create,
        "update": cmd_budgets_update, "delete": cmd_budgets_delete,
    },
    "timesheets": {
        "list": cmd_timesheets_list, "get": cmd_timesheets_get, "create": cmd_timesheets_create,
        "update": cmd_timesheets_update, "delete": cmd_timesheets_delete,
        "submit": cmd_timesheets_submit, "approve": cmd_timesheets_approve,
        "reject": cmd_timesheets_reject, "reopen": cmd_timesheets_reopen,
    },
    "entries": {
        "list": cmd_entries_list, "create": cmd_entries_create,
        "update": cmd_entries_update, "delete": cmd_entries_delete,
    },
    "cost-entries": {
        "list": cmd_costentries_list, "create": cmd_costentries_create,
        "update": cmd_costentries_update, "delete": cmd_costentries_delete,
    },
    "locks": {"list": cmd_locks_list, "create": cmd_locks_create, "delete": cmd_locks_delete},
    "links": {
        "list": cmd_links_list, "create": cmd_links_create,
        "delete": cmd_links_delete, "bulk": cmd_links_bulk,
    },
    "comments": {
        "list": cmd_comments_list, "add": cmd_comments_add,
        "delete": cmd_comments_delete, "bulk": cmd_comments_bulk,
    },
    "chat": {
        "send": cmd_chat_send, "ls": cmd_chat_ls, "get": cmd_chat_get,
        "new": cmd_chat_new, "delete": cmd_chat_delete, "models": cmd_chat_models,
    },
}

SUB_COMMAND_KEYS = {
    "project": "project_command",
    "files": "files_command", "tags": "tags_command", "folders": "folders_command",
    "activities": "activities_command", "steps": "steps_command",
    "progress": "progress_command", "deps": "deps_command",
    "forms": "forms_command", "fields": "fields_command",
    "submissions": "submissions_command",
    "resources": "resources_command", "rates": "rates_command",
    "assignments": "assignments_command", "cost-codes": "costcodes_command",
    "budgets": "budgets_command", "timesheets": "timesheets_command",
    "entries": "entries_command", "cost-entries": "costentries_command",
    "locks": "locks_command",
    "links": "links_command",
    "comments": "comments_command",
    "chat": "chat_command",
}


# ─── Update check & self-update ──────────────────────────────────────────────


def _parse_version(s):
    """Parse '1.2.3' or 'v1.2.3' into a tuple of ints. Returns () on failure."""
    s = (s or "").strip().lstrip("v")
    parts = s.split(".")
    out = []
    for p in parts:
        digits = "".join(c for c in p if c.isdigit())
        if not digits:
            return tuple(out)
        out.append(int(digits))
    return tuple(out)


def _detect_install_mode():
    """Return ('editable', repo_path) | ('site-packages', None) | ('script', None).

    Editable install: pcxa.py lives inside a git checkout (has .git nearby).
    """
    here = Path(__file__).resolve().parent
    if (here / ".git").exists():
        return ("editable", here)
    parent_git = here.parent / ".git"
    if parent_git.exists():
        return ("editable", here.parent)
    if "site-packages" in str(here):
        return ("site-packages", None)
    return ("script", None)


def _check_for_update():
    """Once per day, check GitHub releases for a newer version. Returns latest tag or None."""
    if os.environ.get("PCXA_NO_UPDATE_CHECK"):
        return None
    now = time.time()
    try:
        if UPDATE_CHECK_FILE.exists():
            data = json.loads(UPDATE_CHECK_FILE.read_text())
            if now - data.get("ts", 0) < UPDATE_CHECK_INTERVAL_SECONDS:
                latest = data.get("latest")
                return latest if latest and _parse_version(latest) > _parse_version(__version__) else None
    except Exception:
        pass
    try:
        resp = requests.get(GITHUB_RELEASES_URL, timeout=2,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return None
        latest = resp.json().get("tag_name", "")
    except Exception:
        return None
    try:
        UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_CHECK_FILE.write_text(json.dumps({"ts": now, "latest": latest}))
    except Exception:
        pass
    return latest if latest and _parse_version(latest) > _parse_version(__version__) else None


def _print_update_notice(latest):
    """Yellow-ish, non-blocking, single line on stderr."""
    if not latest:
        return
    msg = f"pcxa: {latest} available (current {__version__}) — run `pcxa update`"
    if sys.stderr.isatty():
        msg = f"\033[33m{msg}\033[0m"
    print(msg, file=sys.stderr)


def cmd_update(args):
    """Self-update from GitHub. Detects editable installs and prints git-pull instead."""
    mode, repo_path = _detect_install_mode()
    if mode == "editable":
        print(f"Editable install detected at: {repo_path}")
        print(f"To update, run:")
        print(f"  cd {repo_path} && git pull")
        return
    import subprocess
    target = f"git+{GITHUB_REPO_URL[:-4] if GITHUB_REPO_URL.endswith('.git') else GITHUB_REPO_URL}.git"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
    print(f"Running: {' '.join(cmd)}")
    if args.dry_run:
        return
    raise SystemExit(subprocess.call(cmd))


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command in AUTH_FREE:
        try:
            if args.command == "login":
                cmd_login(args)
            elif args.command == "setup":
                cmd_setup(args)
            elif args.command == "whoami":
                cmd_whoami(None, args)
            elif args.command == "set-project":
                cmd_set_project(args)
            elif args.command == "update":
                cmd_update(args)
        finally:
            if args.command != "update":
                _print_update_notice(_check_for_update())
        return

    config = load_config()
    profile_name = args.profile or config.get("default_profile")
    _, profile = get_profile(config, profile_name)
    client = APIClient(profile, profile_name, config)
    resolve_ids(client)

    try:
        if args.command in HANDLERS:
            HANDLERS[args.command](client, args)
        elif args.command in SUB_HANDLERS:
            sub_key = SUB_COMMAND_KEYS[args.command]
            sub_cmd = getattr(args, sub_key, None)
            if not sub_cmd:
                avail = ", ".join(SUB_HANDLERS[args.command].keys())
                print(f"Usage: pcxa {args.command} {{{avail}}}", file=sys.stderr)
                sys.exit(1)
            SUB_HANDLERS[args.command][sub_cmd](client, args)
        else:
            parser.print_help()
    except requests.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        if e.response is not None:
            try:
                print(json.dumps(e.response.json(), indent=2), file=sys.stderr)
            except Exception:
                print(e.response.text[:500], file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        _print_update_notice(_check_for_update())


if __name__ == "__main__":
    main()
