"""APIClient: HTTP wrapper for the pcxa REST API with JWT auth + auto-refresh."""

import sys
from urllib.parse import parse_qs, quote, urlparse

from pcxa._config import save_config
from pcxa._http import requests


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

    def _refresh_token(self):
        refresh = self.profile.get("refresh_token")
        if not refresh:
            return False
        try:
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


__all__ = ["APIClient"]
