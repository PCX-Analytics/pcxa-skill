"""Credential and per-repo project-pin configuration.

Storage:
    ~/.pcxa/credentials.json — single global file with all named profiles.
    <repo>/.pcxa             — committed per-repo file pinning {company, project, user}.

Per-repo isolation across accounts is achieved via the .pcxa file's `user`
field selecting which profile from the credentials file to use. No per-repo
credential file exists.
"""

import json
import os
import sys
import threading
from pathlib import Path


# Cross-thread serialization for credential writes. A long-running sync can
# trigger many parallel JWT refreshes — without this, two threads could race
# to write credentials.json and one would truncate the other's update.
_SAVE_LOCK = threading.Lock()


GLOBAL_CONFIG_DIR = Path.home() / ".pcxa"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "credentials.json"
LOCAL_CONFIG_NAME = ".pcxa"  # repo-level project pin (committed, no secrets)
UPDATE_CHECK_FILE = GLOBAL_CONFIG_DIR / "last_update_check.json"

# Legacy locations kept only for one-shot migration into GLOBAL_CONFIG_FILE.
LEGACY_GLOBAL_CONFIG_FILE = Path.home() / ".file_explorer" / "config.json"
LEGACY_LOCAL_CREDENTIALS_NAME = ".pcxa-credentials.json"


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
        if candidate.is_file():
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
    """Return the credentials file path.

    Always ~/.pcxa/credentials.json. Per-repo isolation across accounts is
    achieved via the .pcxa file's `user` field selecting which profile to use;
    no per-repo credential file exists. The second tuple element is retained
    for callers that displayed the source label.
    """
    return GLOBAL_CONFIG_FILE, "global"


def get_config_file():
    """Return the credentials config path."""
    return GLOBAL_CONFIG_FILE


def _migrate_legacy_credentials():
    """One-shot migration from pre-0.3 credential locations.

    Older versions stored credentials at:
      - ~/.file_explorer/config.json   (global)
      - <repo>/.pcxa-credentials.json  (per-repo)

    On first run after upgrade, merge any legacy files we find into the new
    ~/.pcxa/credentials.json. Profiles are de-duplicated by name (first wins).
    Legacy files are left in place; a one-shot notice is printed to stderr.
    """
    if GLOBAL_CONFIG_FILE.exists():
        return

    candidates = []
    seen = set()

    def _add(path):
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        candidates.append(resolved)

    _add(LEGACY_GLOBAL_CONFIG_FILE)
    _add(Path.cwd() / LEGACY_LOCAL_CREDENTIALS_NAME)

    git_root = find_git_root()
    if git_root is not None:
        _add(git_root / LEGACY_LOCAL_CREDENTIALS_NAME)

    local_marker = find_local_config_path()
    if local_marker is not None:
        _add(local_marker.parent / LEGACY_LOCAL_CREDENTIALS_NAME)

    if not candidates:
        return

    merged = {"default_profile": "local", "profiles": {}}
    for path in candidates:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for pname, prof in (data.get("profiles") or {}).items():
            if pname not in merged["profiles"] and isinstance(prof, dict):
                merged["profiles"][pname] = prof
        default = data.get("default_profile")
        if default and merged["default_profile"] == "local":
            merged["default_profile"] = default

    if not merged["profiles"]:
        return

    GLOBAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG_FILE.write_text(json.dumps(merged, indent=2))
    try:
        GLOBAL_CONFIG_FILE.chmod(0o600)
    except OSError:
        pass

    sources_str = "\n  ".join(str(p) for p in candidates)
    print(
        f"pcxa: migrated credentials to {GLOBAL_CONFIG_FILE}\n"
        f"  Merged from:\n  {sources_str}\n"
        f"  Legacy files were left in place; you can delete them now.",
        file=sys.stderr,
    )


def load_config():
    _migrate_legacy_credentials()
    path = get_config_file()
    if path.exists():
        return json.loads(path.read_text())
    return {"default_profile": "local", "profiles": {}}


def save_config(config):
    """Atomically persist the credentials config.

    Writes to a sibling tmp file then ``os.replace()`` so a crash or
    concurrent reader never sees a half-written credentials.json. The
    in-process lock prevents two threads from interleaving their writes,
    which can otherwise lose a freshly rotated refresh_token under sync's
    parallel workers (see issue #550).
    """
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(config, indent=2))
        try:
            tmp.chmod(0o600)
        except OSError:
            pass  # Windows NTFS doesn't support Unix permissions
        os.replace(tmp, path)


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


__all__ = [
    "GLOBAL_CONFIG_DIR",
    "GLOBAL_CONFIG_FILE",
    "LOCAL_CONFIG_NAME",
    "UPDATE_CHECK_FILE",
    "LEGACY_GLOBAL_CONFIG_FILE",
    "LEGACY_LOCAL_CREDENTIALS_NAME",
    "KNOWN_FILE_TYPES",
    "find_local_config_path",
    "find_git_root",
    "resolve_credentials_path",
    "get_config_file",
    "load_config",
    "save_config",
    "find_local_config",
    "get_profile",
]
