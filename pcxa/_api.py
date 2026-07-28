"""APIClient: HTTP wrapper for the pcxa REST API with JWT auth + auto-refresh.

The refresh path is thread-safe and tolerates many concurrent workers. Three
mechanisms cooperate to prevent the thundering-herd race where N workers
simultaneously hit a 401 and race on the rotating refresh endpoint, ending
with N-1 of them blacklisting their refresh_token (issue #550):

  1. **Proactive refresh** — before each request we decode the access_token's
     ``exp`` claim and refresh early if it's within ``REFRESH_LEEWAY_SECONDS``
     of expiring. This eliminates the burst of simultaneous 401s entirely.
  2. **Single-flight lock** — actual refresh is serialized behind
     ``_refresh_lock``. Workers that arrive while a refresh is in flight wait
     for it and reuse the freshly written token instead of double-rotating.
  3. **Reload-on-blacklist** — if the refresh endpoint reports our refresh
     token is blacklisted (another process beat us), we re-read
     ``credentials.json`` from disk and retry with whatever was just written.
"""

import base64
import json
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

from pcxa._config import load_config, save_config
from pcxa._http import requests


class AuthExpiredError(RuntimeError):
    """Access token is expired and the refresh token cannot renew it.

    Retrying will not help — the user must run ``pcxa login`` to get a
    fresh token pair before continuing.
    """


# Refresh the access_token when its remaining lifetime drops below this many
# seconds. Tokens typically live ~15-90 min, so a 5-min leeway gives plenty of
# headroom even under clock skew without burning refreshes for short scripts.
REFRESH_LEEWAY_SECONDS = 300

# Cooldown after a successful refresh. Concurrent workers that enter the lock
# within this window assume the already-written token is fresh and skip the
# extra network round-trip.
REFRESH_REUSE_SECONDS = 10


def _decode_jwt_exp(token):
    """Return the ``exp`` claim (epoch seconds) from a JWT, or None.

    Doesn't verify the signature — the server does that on every request.
    Tolerant of malformed tokens: returns None instead of raising so callers
    can fall back to reactive (401-driven) refresh.
    """
    if not token or not isinstance(token, str):
        return None
    try:
        payload_b64 = token.split(".")[1]
        # JWT uses unpadded base64url; pad back to a multiple of 4.
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        exp = claims.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


class APIClient:
    """HTTP client for pcxa REST API with JWT auth and auto-refresh."""

    def __init__(self, profile, profile_name, config, timeout=None):
        self.profile = profile
        self.profile_name = profile_name
        self.config = config
        # Per-run read timeout (``pcxa --timeout``). ``None`` defers to
        # ``_http.get_default_timeout()`` so PCXA_HTTP_TIMEOUT and the
        # built-in 30s fallback stay in one place instead of being
        # re-hard-coded here (PCX-Analytics/pcxa#1689, #1454).
        self.timeout = timeout
        self.base_url = profile["url"].rstrip("/")
        self.company_id = profile.get("company")
        self.project_id = profile.get("project")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        # Serializes concurrent refresh attempts so multiple workers don't
        # race the rotating endpoint and blacklist each other's tokens.
        self._refresh_lock = threading.Lock()
        self._last_refresh_at = 0.0
        self._set_auth()

    def _set_auth(self):
        auth_mode = self.profile.get("auth", "jwt")
        if auth_mode == "jwt":
            token = self.profile.get("access_token")
            if token:
                self.session.headers["Authorization"] = f"Bearer {token}"
            else:
                print("No access token. Run: pcxa setup", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Unsupported auth mode '{auth_mode}'. Run: pcxa login", file=sys.stderr)
            sys.exit(1)

    def access_token_expires_in(self):
        """Seconds until the cached access_token expires, or None if unknown."""
        exp = _decode_jwt_exp(self.profile.get("access_token"))
        if exp is None:
            return None
        return exp - time.time()

    def _reload_credentials_from_disk(self):
        """Re-read credentials.json so we pick up a token rotated by another
        process. Mutates ``self.profile`` and re-applies the Authorization
        header. No-op (returns False) if the file no longer has our profile.
        """
        try:
            fresh_config = load_config()
        except Exception:
            return False
        fresh_profile = (fresh_config.get("profiles") or {}).get(self.profile_name)
        if not isinstance(fresh_profile, dict) or not fresh_profile.get("access_token"):
            return False
        for key in ("access_token", "refresh_token"):
            if fresh_profile.get(key):
                self.profile[key] = fresh_profile[key]
        self.config = fresh_config
        self.session.headers["Authorization"] = f"Bearer {self.profile['access_token']}"
        return True

    def _refresh_token(self):
        """Single-flight refresh. Safe under concurrent callers.

        The first caller into the lock performs the network refresh and
        persists the new tokens. Concurrent callers that arrive within
        ``REFRESH_REUSE_SECONDS`` see the cached timestamp and reuse the
        already-rotated token without a second network call — this is what
        prevents the thundering-herd blacklist race documented in issue #550.

        If the backend reports the refresh_token is blacklisted (another
        *process* — not just another thread — has already rotated it), reload
        credentials.json from disk and treat that as success.
        """
        if not self.profile.get("refresh_token"):
            return False
        with self._refresh_lock:
            # Fast path: another thread refreshed moments ago. The Authorization
            # header was already updated by that thread; just signal success.
            if time.time() - self._last_refresh_at < REFRESH_REUSE_SECONDS:
                return True
            refresh = self.profile.get("refresh_token")
            try:
                resp = requests.post(
                    f"{self.base_url}/api/accounts/token/refresh/",
                    json={"refresh": refresh},
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
            except Exception as e:
                print(f"Token refresh error: {e}", file=sys.stderr)
                return False

            if resp.status_code == 200:
                data = resp.json()
                self.profile["access_token"] = data["access"]
                if "refresh" in data:
                    self.profile["refresh_token"] = data["refresh"]
                self.config["profiles"][self.profile_name] = self.profile
                save_config(self.config)
                self.session.headers["Authorization"] = f"Bearer {data['access']}"
                self._last_refresh_at = time.time()
                return True

            # Blacklisted refresh_token usually means a sibling process won the
            # rotation race. Re-read credentials.json — if a fresh token is
            # waiting on disk, adopt it instead of failing the run.
            try:
                body = resp.json()
            except Exception:
                body = {}
            if resp.status_code in (400, 401) and body.get("code") == "token_not_valid":
                if self._reload_credentials_from_disk():
                    self._last_refresh_at = time.time()
                    return True

            print(
                f"Token refresh failed ({resp.status_code}). Run: pcxa setup -u YOUR_EMAIL",
                file=sys.stderr,
            )
            return False

    def _maybe_proactive_refresh(self):
        """Refresh if the access_token is about to expire.

        Cheap on the hot path: a base64 decode + a clock read. The network
        refresh only happens when ``exp`` is within ``REFRESH_LEEWAY_SECONDS``,
        and that call is itself gated by the single-flight lock.
        """
        if self.profile.get("auth", "jwt") != "jwt":
            return
        remaining = self.access_token_expires_in()
        if remaining is None:
            return  # opaque/unparseable token — fall back to reactive refresh
        if remaining < REFRESH_LEEWAY_SECONDS:
            self._refresh_token()

    def _url(self, path, project_scoped=True):
        if project_scoped:
            return (
                f"{self.base_url}/api/companies/{self.company_id}"
                f"/projects/{self.project_id}/{path}"
            )
        return f"{self.base_url}/api/companies/{self.company_id}/{path}"

    def _request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout  # None → _http applies the default
        self._maybe_proactive_refresh()
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code in (401, 403) and self.profile.get("auth") == "jwt":
            try:
                body = resp.json()
            except Exception:
                body = {}
            if body.get("code") == "token_not_valid" or resp.status_code == 401:
                if self._refresh_token():
                    resp = self.session.request(method, url, **kwargs)
                elif body.get("code") == "token_not_valid":
                    raise AuthExpiredError(
                        "Access token expired and refresh failed — "
                        "run `pcxa login` and retry"
                    )
        resp.raise_for_status()
        return resp

    def get(self, path, params=None, project_scoped=True, timeout=None):
        url = self._url(path, project_scoped=project_scoped)
        return self._request("GET", url, params=params, timeout=timeout).json()

    def post(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._request(
            "POST", self._url(path, project_scoped=project_scoped),
            json=json_data, timeout=timeout,
        ).json()

    def patch(self, path, json_data=None, project_scoped=True, timeout=None):
        return self._request(
            "PATCH", self._url(path, project_scoped=project_scoped),
            json=json_data, timeout=timeout,
        ).json()

    def delete(self, path, json_data=None, project_scoped=True, timeout=None):
        resp = self._request(
            "DELETE", self._url(path, project_scoped=project_scoped),
            json=json_data, timeout=timeout,
        )
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    def get_raw(self, url, params=None):
        return self._request("GET", url, params=params).json()

    def bulk_call(self, path, ids_key, ids, base_payload=None,
                  chunk=500, method="POST", project_scoped=True, on_chunk=None,
                  timeout=None):
        """Call a bulk endpoint in chunks, aggregating ``success_count``,
        ``error_count``, and ``errors`` across chunks.

        Always routes through ``_request`` so long-running jobs get JWT
        auto-refresh — use this instead of ``c.session.*`` for any bulk
        operation that may exceed the access-token TTL (issue #562).

        ``timeout`` overrides the client default for these chunks; leave it
        None to inherit ``pcxa --timeout`` / ``PCXA_HTTP_TIMEOUT``. Bulk
        mutations are the slowest calls the CLI makes, and until this
        existed there was no way to give them more than the 30s default
        (PCX-Analytics/pcxa#1454).

        ``on_chunk(start_index, batch_size, total, response_data)`` fires
        after each chunk, useful for progress output.
        """
        ids = list(ids)
        base = dict(base_payload or {})
        url = self._url(path, project_scoped=project_scoped)
        agg = {"success_count": 0, "error_count": 0, "errors": [], "chunks": 0}
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            payload = dict(base, **{ids_key: batch})
            resp = self._request(method, url, json=payload, timeout=timeout)
            try:
                data = {} if resp.status_code == 204 else resp.json()
            except Exception:
                data = {}
            agg["success_count"] += data.get("success_count", 0)
            agg["error_count"] += data.get("error_count", 0)
            errs = data.get("errors")
            if errs:
                agg["errors"].extend(errs)
            agg["chunks"] += 1
            if on_chunk is not None:
                on_chunk(i, len(batch), len(ids), data)
        return agg

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


__all__ = ["APIClient"]
