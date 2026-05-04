"""Self-update + update notification for the pcxa CLI.

Once per day, the CLI hits the GitHub releases API in a background-friendly
manner (2 second timeout, swallowed errors) and prints a yellow stderr line
when a newer version is published. Disable with `PCXA_NO_UPDATE_CHECK=1`.

`pcxa update` upgrades a pipx install via pip; for editable checkouts it just
prints the `git pull` command instead.
"""

import json
import os
import sys
import time
from pathlib import Path

from pcxa import __version__
from pcxa._config import UPDATE_CHECK_FILE
from pcxa._http import requests


UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
GITHUB_RELEASES_URL = "https://api.github.com/repos/PCX-Analytics/pcxa-skill/releases/latest"
GITHUB_REPO_URL = "https://github.com/PCX-Analytics/pcxa-skill.git"


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
    """Return ('plugin', None) | ('editable', repo_path) | ('site-packages', None) | ('script', None).

    Plugin install:   running from Claude Code's marketplace cache (no `pcxa update` self-upgrade — Claude Code manages the install).
    Editable install: package lives inside a git checkout (has .git nearby).
    """
    here = Path(__file__).resolve().parent
    parts = here.parts
    if ".claude" in parts and "plugins" in parts and "cache" in parts:
        return ("plugin", None)
    if (here / ".git").exists():
        return ("editable", here)
    parent_git = here.parent / ".git"
    if parent_git.exists():
        return ("editable", here.parent)
    if "site-packages" in parts:
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
    """Yellow-ish, non-blocking, single line on stderr.

    The "how to update" hint depends on install mode: plugin installs are
    managed by Claude Code (`/plugin update`), pipx installs use `pcxa update`,
    editable checkouts use `git pull`.
    """
    if not latest:
        return
    mode, _ = _detect_install_mode()
    if mode == "plugin":
        action = "in Claude Code run `/plugin update pcxa@pcxa-skill` and restart"
    elif mode == "editable":
        action = "run `git pull` in the pcxa-skill checkout"
    else:
        action = "run `pcxa update`"
    msg = f"pcxa: {latest} available (current {__version__}) — {action}"
    if sys.stderr.isatty():
        msg = f"\033[33m{msg}\033[0m"
    print(msg, file=sys.stderr)


def cmd_update(args):
    """Self-update from GitHub. Detects install mode and routes accordingly."""
    mode, repo_path = _detect_install_mode()
    if mode == "plugin":
        print("Plugin install detected (Claude Code manages this).")
        print("To update, in Claude Code run:")
        print("  /plugin marketplace update pcxa-skill")
        print("  /plugin update pcxa@pcxa-skill")
        print("Then restart Claude Code so the new SKILL.md and bin/pcxa load.")
        print("Tip: toggle auto-update for the pcxa-skill marketplace via /plugin")
        print("     to pick up new versions on every session start.")
        return
    if mode == "editable":
        print(f"Editable install detected at: {repo_path}")
        print("To update, run:")
        print(f"  cd {repo_path} && git pull")
        return
    import subprocess
    target = f"git+{GITHUB_REPO_URL[:-4] if GITHUB_REPO_URL.endswith('.git') else GITHUB_REPO_URL}.git"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
    print(f"Running: {' '.join(cmd)}")
    if args.dry_run:
        return
    raise SystemExit(subprocess.call(cmd))


__all__ = [
    "UPDATE_CHECK_INTERVAL_SECONDS",
    "GITHUB_RELEASES_URL",
    "GITHUB_REPO_URL",
    "_check_for_update",
    "_print_update_notice",
    "cmd_update",
]
