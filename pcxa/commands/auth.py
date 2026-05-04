"""Authentication and profile management commands."""

import json
import sys
from pathlib import Path

from pcxa._config import (
    LOCAL_CONFIG_NAME,
    find_git_root,
    find_local_config,
    find_local_config_path,
    get_config_file,
    load_config,
    resolve_credentials_path,
    save_config,
)
from pcxa._http import requests
from pcxa._api import APIClient
from urllib.parse import parse_qs, urlparse


def _setup_repo_config(api_url, access_token, username, local_path):
    """Auto-detect and set company/project in .pcxa file with menu if multiple options."""
    try:
        local_cfg = json.loads(local_path.read_text()) if local_path.exists() else {}
        if not isinstance(local_cfg, dict):
            local_cfg = {}
    except Exception:
        local_cfg = {}

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {access_token}"
    base = api_url.rstrip("/")

    # Fetch companies
    try:
        resp = session.get(f"{base}/api/companies/", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        companies = data.get("results", data) if isinstance(data, dict) else data
    except Exception:
        print(f"  Could not fetch companies for .pcxa setup", file=sys.stderr)
        return

    if not companies:
        print(f"  No companies found", file=sys.stderr)
        return

    # Select company
    company_id = None
    if len(companies) == 1:
        company_id = companies[0]["id"]
        print(f"  Company auto-detected: {companies[0].get('name', '?')} (id={company_id})")
    else:
        print(f"\n  Multiple companies found. Select one:")
        for i, c in enumerate(companies, 1):
            print(f"    {i}. {c.get('name', '?')} (id={c['id']})")
        while True:
            try:
                choice = input(f"  Enter choice (1-{len(companies)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(companies):
                    company_id = companies[idx]["id"]
                    print(f"  Selected: {companies[idx].get('name', '?')} (id={company_id})")
                    break
            except (ValueError, IndexError):
                pass
            print(f"  Invalid choice. Try again.")

    if not company_id:
        return

    # Fetch projects in company
    try:
        resp = session.get(f"{base}/api/companies/{company_id}/projects/", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        projects = data.get("results", data) if isinstance(data, dict) else data
    except Exception:
        print(f"  Could not fetch projects for .pcxa setup", file=sys.stderr)
        return

    if not projects:
        print(f"  No projects found in this company", file=sys.stderr)
        return

    # Select project
    project_id = None
    if len(projects) == 1:
        project_id = projects[0]["id"]
        print(f"  Project auto-detected: {projects[0].get('name', '?')} (id={project_id})")
    else:
        print(f"\n  Multiple projects found. Select one:")
        for i, p in enumerate(projects, 1):
            print(f"    {i}. {p.get('name', '?')} (id={p['id']})")
        while True:
            try:
                choice = input(f"  Enter choice (1-{len(projects)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(projects):
                    project_id = projects[idx]["id"]
                    print(f"  Selected: {projects[idx].get('name', '?')} (id={project_id})")
                    break
            except (ValueError, IndexError):
                pass
            print(f"  Invalid choice. Try again.")

    if not project_id:
        return

    # Write to .pcxa with company, project, and user
    local_cfg["company"] = company_id
    local_cfg["project"] = project_id
    if username:
        local_cfg["user"] = username

    try:
        local_path.write_text(json.dumps(local_cfg, indent=2))
        print(f"\n  Repo config saved to {local_path}")
        print(f"    Company: {company_id}")
        print(f"    Project: {project_id}")
        print(f"    User:    {username}")
    except Exception as e:
        print(f"  Could not write repo config: {e}", file=sys.stderr)


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

    print("Opening browser to authenticate...", flush=True)
    print(f"  If your browser does not open automatically, visit:\n  {auth_url}", flush=True)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass  # WSL / headless / no BROWSER set — URL is printed above

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

    # If in a git repo, auto-detect and set company/project, then write .pcxa.
    # Use existing .pcxa if found, otherwise create one at the git root.
    # Skipped when --no-setup is passed (agent-driven login: agent will run
    # `pcxa projects` + `pcxa set-project` after login completes).
    if getattr(args, "no_setup", False):
        print("\n  Project not set. Run `pcxa projects` to list, "
              "then `pcxa set-project <id> [--local]`.")
        return
    local_path = find_local_config_path()
    if local_path is None:
        git_root = find_git_root()
        if git_root is not None:
            local_path = git_root / LOCAL_CONFIG_NAME
    if local_path is not None:
        _setup_repo_config(api_url, result.get("access"), result.get("username"), local_path)


def cmd_projects(args):
    """List all (company, project) pairs the active profile has access to.

    Used by Claude Code (and other agents) after `pcxa login --no-setup` to
    let the user pick a project without an interactive stdin prompt inside
    the login flow.
    """
    from pcxa._output import out_json, out_table

    config = load_config()
    name = getattr(args, "profile", None) or config.get("default_profile")
    profiles = config.get("profiles", {})
    if not name or name not in profiles:
        print("No active profile. Run: pcxa login", file=sys.stderr)
        sys.exit(1)
    profile = profiles[name]
    if not profile.get("access_token"):
        print(f"Profile '{name}' not authenticated. Run: pcxa login", file=sys.stderr)
        sys.exit(1)

    base = profile["url"].rstrip("/")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {profile['access_token']}"

    try:
        resp = session.get(f"{base}/api/companies/", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        companies = data.get("results", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"Could not list companies: {e}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for c in companies or []:
        cid = c.get("id")
        cname = c.get("name", "")
        try:
            resp = session.get(f"{base}/api/companies/{cid}/projects/", timeout=15)
            resp.raise_for_status()
            d = resp.json()
            projects = d.get("results", d) if isinstance(d, dict) else d
        except Exception:
            projects = []
        for p in projects or []:
            rows.append({
                "company_id": cid,
                "company_name": cname,
                "project_id": p.get("id"),
                "project_name": p.get("name", ""),
                "code": p.get("code", "") or "",
            })

    if args.format == "json":
        out_json(rows)
    else:
        if not rows:
            print("No projects found across your companies.")
            return
        out_table(rows, ["company_id", "company_name", "project_id", "project_name", "code"])


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

    creds_path, _ = resolve_credentials_path()
    pin_path = find_local_config_path()

    print(f"Active profile: {name}{profile_src}")
    print(f"  URL:     {p.get('url')}")
    print(f"  Auth:    {p.get('auth')}")
    if p.get("username"):
        print(f"  User:    {p.get('username')}")
    print(f"  Company: {company}{company_src}")
    print(f"  Project: {project}{project_src}")
    print(f"  Token:   {'cached' if p.get('access_token') else 'none'}")
    print(f"  Creds:   {creds_path}")
    if pin_path is not None:
        print(f"  Repo pin: {pin_path}")

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
    """Set the default project — globally in user config, or locally in .pcxa.

    If project_id not provided and multiple projects exist, show interactive menu.
    """
    project_id = getattr(args, "project_id", None)
    company_id = getattr(args, "company", None)

    # If no project_id provided, try to detect it interactively
    if not project_id:
        config = load_config()
        name = getattr(args, "profile", None) or config.get("default_profile", "local")
        profiles = config.get("profiles", {})

        if name not in profiles:
            print(f"Profile '{name}' not found. Run: pcxa setup -u YOUR_EMAIL", file=sys.stderr)
            sys.exit(1)

        profile = profiles[name]
        if not profile.get("access_token"):
            print(f"Profile '{name}' not authenticated. Run: pcxa login", file=sys.stderr)
            sys.exit(1)

        # Determine company for project fetch
        if not company_id:
            company_id = getattr(args, "company", None)
            if not company_id and getattr(args, "local", False):
                try:
                    local_cfg = json.loads((Path.cwd() / LOCAL_CONFIG_NAME).read_text())
                    company_id = local_cfg.get("company")
                except Exception:
                    pass
            if not company_id:
                company_id = profile.get("company")

        if not company_id:
            print("Company not set. Use: pcxa set-project PROJECT_ID --company COMPANY_ID", file=sys.stderr)
            sys.exit(1)

        # Fetch projects
        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {profile['access_token']}"
        base = profile["url"].rstrip("/")

        try:
            resp = session.get(f"{base}/api/companies/{company_id}/projects/", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            projects = data.get("results", data) if isinstance(data, dict) else data
        except Exception as e:
            print(f"Could not fetch projects: {e}", file=sys.stderr)
            sys.exit(1)

        if not projects:
            print("No projects found in this company", file=sys.stderr)
            sys.exit(1)

        if len(projects) == 1:
            project_id = projects[0]["id"]
            print(f"Project auto-selected: {projects[0].get('name', '?')} (id={project_id})")
        else:
            print(f"Select project:")
            for i, p in enumerate(projects, 1):
                print(f"  {i}. {p.get('name', '?')} (id={p['id']})")
            while True:
                try:
                    choice = input(f"Enter choice (1-{len(projects)}): ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(projects):
                        project_id = projects[idx]["id"]
                        print(f"Selected: {projects[idx].get('name', '?')} (id={project_id})")
                        break
                except (ValueError, IndexError):
                    pass
                print(f"Invalid choice. Try again.")

    if getattr(args, "local", False):
        local_file = Path.cwd() / LOCAL_CONFIG_NAME
        local_cfg = {}
        if local_file.exists():
            try:
                local_cfg = json.loads(local_file.read_text())
            except Exception:
                pass
        local_cfg["project"] = project_id
        if company_id:
            local_cfg["company"] = company_id
        if getattr(args, "user", None):
            local_cfg["user"] = args.user
        local_file.write_text(json.dumps(local_cfg, indent=2))
        print(f"Repo-level config written to {local_file}")
        print(f"  Project: {project_id}")
        if local_cfg.get("company"):
            print(f"  Company: {local_cfg['company']}")
        if local_cfg.get("user"):
            print(f"  User:    {local_cfg['user']}")
    else:
        config = load_config()
        name = getattr(args, "profile", None) or config.get("default_profile", "local")
        if name not in config.get("profiles", {}):
            print(f"Profile '{name}' not found. Run: pcxa setup -u YOUR_EMAIL", file=sys.stderr)
            sys.exit(1)
        config["profiles"][name]["project"] = project_id
        if company_id:
            config["profiles"][name]["company"] = company_id
        save_config(config)
        print(f"Default project set to {project_id} (global)")
