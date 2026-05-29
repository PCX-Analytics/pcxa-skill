"""Credential and per-repo project-pin configuration.

Storage:
    <repo>/.pcxa-credentials.json — per-repo credentials (secrets, gitignored).
    ~/.pcxa/credentials.json      — global fallback shared across repos.
    <repo>/.pcxa                  — committed per-repo file pinning {company, project, user}.

Credentials resolve folder-first: a `.pcxa-credentials.json` found by walking
up from the current directory is used for both reads and writes (including
token refresh), so a login from one repo can't clobber another repo's tokens.
The global file is used only when no per-repo credentials file is present.
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
LOCAL_CREDENTIALS_NAME = ".pcxa-credentials.json"  # per-repo credentials (secrets, gitignored)
UPDATE_CHECK_FILE = GLOBAL_CONFIG_DIR / "last_update_check.json"

# Legacy location kept only for one-shot migration into GLOBAL_CONFIG_FILE.
LEGACY_GLOBAL_CONFIG_FILE = Path.home() / ".file_explorer" / "config.json"


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


def find_local_credentials_path():
    """Walk up from CWD looking for a per-repo .pcxa-credentials.json file.

    Returns the Path to the credentials file, or None if not found. Stops at
    $HOME or the filesystem root, mirroring find_local_config_path().
    """
    current = Path.cwd()
    home = Path.home()
    while True:
        candidate = current / LOCAL_CREDENTIALS_NAME
        if candidate.is_file():
            return candidate
        if current == home or current == current.parent:
            return None
        current = current.parent


def resolve_credentials_path():
    """Return (path, source) for the ACTIVE credentials file.

    A per-repo .pcxa-credentials.json found by walking up from CWD wins
    ("local"); otherwise the global ~/.pcxa/credentials.json ("global"). Both
    reads and passive writes (token refresh) go through here, so a rotated
    token is written back to whichever file it was loaded from.
    """
    local = find_local_credentials_path()
    if local is not None:
        return local, "local"
    return GLOBAL_CONFIG_FILE, "global"


def resolve_login_path(use_global=False):
    """Return the path where `pcxa login` / `pcxa setup` should persist creds.

    Defaults to folder-local so a login from one repo can't clobber another
    repo's tokens. Resolution order:
      1. use_global (--global)                  -> global file
      2. existing .pcxa-credentials.json up-tree -> reuse it in place
      3. inside a git repo                       -> <git_root>/.pcxa-credentials.json
      4. a .pcxa pin exists up-tree              -> next to it
      5. otherwise (no repo context)             -> global file
    """
    if use_global:
        return GLOBAL_CONFIG_FILE
    existing = find_local_credentials_path()
    if existing is not None:
        return existing
    git_root = find_git_root()
    if git_root is not None:
        return git_root / LOCAL_CREDENTIALS_NAME
    pin = find_local_config_path()
    if pin is not None:
        return pin.parent / LOCAL_CREDENTIALS_NAME
    return GLOBAL_CONFIG_FILE


def get_config_file():
    """Return the active credentials path (local-if-present, else global)."""
    return resolve_credentials_path()[0]


def _migrate_legacy_credentials():
    """One-shot migration from pre-0.3 credential locations.

    Older versions stored the global credentials at ~/.file_explorer/config.json.
    On first run after upgrade, merge it into the new ~/.pcxa/credentials.json.
    Profiles are de-duplicated by name (first wins). The legacy file is left in
    place; a one-shot notice is printed to stderr.

    Per-repo .pcxa-credentials.json files are NOT scavenged here — they are now
    live, folder-local credential files (see resolve_credentials_path).
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


def load_config(path=None):
    """Read the credentials config.

    With no ``path``, runs the one-shot legacy migration and reads the active
    file (local-if-present, else global). Pass an explicit ``path`` to read a
    specific file — used by login to target the chosen destination directly.
    """
    if path is None:
        _migrate_legacy_credentials()
        path, _ = resolve_credentials_path()
    if path.exists():
        return json.loads(path.read_text())
    return {"default_profile": "local", "profiles": {}}


def save_config(config, path=None):
    """Atomically persist the credentials config.

    With no ``path``, writes the active file (local-if-present, else global) so
    a token rotated during a run is written back to wherever it was loaded
    from. Pass an explicit ``path`` to target a specific file (login).

    Writes to a sibling tmp file then ``os.replace()`` so a crash or
    concurrent reader never sees a half-written credentials.json. The
    in-process lock prevents two threads from interleaving their writes,
    which can otherwise lose a freshly rotated refresh_token under sync's
    parallel workers (see issue #550).
    """
    if path is None:
        path, _ = resolve_credentials_path()
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
    "LOCAL_CREDENTIALS_NAME",
    "UPDATE_CHECK_FILE",
    "LEGACY_GLOBAL_CONFIG_FILE",
    "KNOWN_FILE_TYPES",
    "find_local_config_path",
    "find_local_credentials_path",
    "find_git_root",
    "resolve_credentials_path",
    "resolve_login_path",
    "get_config_file",
    "load_config",
    "save_config",
    "find_local_config",
    "get_profile",
]
