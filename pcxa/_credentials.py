"""Non-interactive credential lookup for unattended re-login.

JWT refresh keeps a session alive only while the *refresh* token is valid.
Once that expires (or is missing), the CLI has nothing left to present and an
agent/cron run dies on ``AuthExpiredError`` waiting for a human to type
``pcxa login``. Storing an account password lets those runs re-authenticate
themselves.

Resolution order — first non-empty value per field wins:

  1. ``PCXA_EMAIL`` (or ``PCXA_USERNAME``) / ``PCXA_PASSWORD`` in the process
     environment.
  2. The same keys in a ``.env`` file: ``$PCXA_ENV_FILE`` if set, else the
     nearest ``.env`` walking up from CWD (stopping at ``$HOME``), else
     ``~/.pcxa/.env``.

Set ``PCXA_AUTO_LOGIN=0`` to disable the whole path regardless of what's
configured.

This trades a token-only footprint for a plaintext password on disk. Keep the
``.env`` gitignored and mode 600, and prefer a dedicated low-privilege account
over a personal or staff one — ``_api.APIClient`` refuses to auto-login as a
user other than the one the profile was created for, but that check only helps
if the password itself is scoped.
"""

import os
from pathlib import Path

ENV_FILE_NAME = ".env"
ENV_FILE_OVERRIDE = "PCXA_ENV_FILE"
USERNAME_KEYS = ("PCXA_EMAIL", "PCXA_USERNAME")
PASSWORD_KEY = "PCXA_PASSWORD"
DISABLE_KEY = "PCXA_AUTO_LOGIN"

_FALSEY = {"0", "false", "no", "off"}


def auto_login_disabled():
    """True when ``PCXA_AUTO_LOGIN`` is set to a falsey value."""
    return os.environ.get(DISABLE_KEY, "").strip().lower() in _FALSEY


def _clean(raw):
    """Strip surrounding whitespace and one layer of matching quotes.

    A stray space after ``=`` in a ``.env`` (``PCXA_EMAIL= user@x``) otherwise
    ships as part of the value and the API rejects the login with a
    non-obvious error — the same trap ``staff-api.sh`` had to work around.
    """
    if raw is None:
        return ""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def find_env_file():
    """Return the ``.env`` Path to read, or None.

    ``$PCXA_ENV_FILE`` wins outright — if it's set but missing, that's treated
    as "no env file" rather than silently falling back, so a typo'd path can't
    quietly pick up some unrelated ``.env`` up-tree.
    """
    override = os.environ.get(ENV_FILE_OVERRIDE, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None

    try:
        current = Path.cwd()
    except OSError:
        current = None
    home = Path.home()
    while current is not None:
        candidate = current / ENV_FILE_NAME
        if candidate.is_file():
            return candidate
        if current == home or current == current.parent:
            break
        current = current.parent

    fallback = home / ".pcxa" / ENV_FILE_NAME
    return fallback if fallback.is_file() else None


def parse_env_file(path, keys):
    """Read ``keys`` out of a dotenv-style file. First occurrence wins.

    Deliberately minimal: ``KEY=value`` lines, optional ``export`` prefix,
    ``#`` comments, quoted values. No interpolation, no multiline values — a
    password needing those doesn't belong in a flat file.
    """
    found = {}
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key in keys and key not in found:
            found[key] = _clean(value)
    return found


def load_credentials():
    """Return ``(username, password, source)``, or None if not configured.

    ``source`` is a human-readable origin ("environment" or the ``.env`` path)
    for error messages — it never contains the password.
    """
    if auto_login_disabled():
        return None

    username = ""
    for key in USERNAME_KEYS:
        username = _clean(os.environ.get(key))
        if username:
            break
    password = _clean(os.environ.get(PASSWORD_KEY))
    source = "environment"

    if not (username and password):
        env_path = find_env_file()
        if env_path is not None:
            from_file = parse_env_file(env_path, set(USERNAME_KEYS) | {PASSWORD_KEY})
            if not username:
                for key in USERNAME_KEYS:
                    if from_file.get(key):
                        username = from_file[key]
                        break
            if not password:
                password = from_file.get(PASSWORD_KEY, "")
            source = str(env_path)

    if username and password:
        return username, password, source
    return None


__all__ = [
    "DISABLE_KEY",
    "ENV_FILE_NAME",
    "ENV_FILE_OVERRIDE",
    "PASSWORD_KEY",
    "USERNAME_KEYS",
    "auto_login_disabled",
    "find_env_file",
    "load_credentials",
    "parse_env_file",
]
