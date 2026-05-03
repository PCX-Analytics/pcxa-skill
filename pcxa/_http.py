"""Stdlib-only HTTP client compatible with a small subset of `requests`.

Exports:
    HTTPError, ConnectionError, Response, Session, RequestsCompat
    requests   — singleton (== RequestsCompat) used like `requests.get(...)`
"""

import http.client
import json as _json
import os
import socket
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

from pcxa import __version__


class HTTPError(Exception):
    """HTTP error carrying the response, compatible with requests.HTTPError use."""

    def __init__(self, response):
        self.response = response
        super().__init__(f"{response.status_code} {response.reason}: {response.url}")


class ConnectionError(Exception):
    """Raised when a network connection cannot be established."""


class Response:
    def __init__(self, status_code, reason, headers, body, url, raw=None, connection=None):
        self.status_code = status_code
        self.reason = reason
        self.headers = headers
        self._body = body
        self.url = url
        self._raw = raw
        self._connection = connection

    @property
    def content(self):
        if self._body is None and self._raw is not None:
            self._body = self._raw.read()
            self.close()
        return self._body or b""

    @property
    def text(self):
        charset = "utf-8"
        content_type = self.headers.get("content-type", "")
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1]
                break
        return self.content.decode(charset, errors="replace")

    def json(self):
        return _json.loads(self.text)

    def iter_content(self, chunk_size=8192):
        if self._raw is None:
            data = self.content
            for i in range(0, len(data), chunk_size):
                yield data[i:i + chunk_size]
            return
        try:
            while True:
                chunk = self._raw.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(self)

    def close(self):
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:
                pass
            self._raw = None
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None


def _encode_multipart(fields, files):
    boundary = f"----pcxa-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in (fields or {}).items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b"\r\n")
    for name, file_value in (files or {}).items():
        filename, fh, content_type = file_value
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(fh.read())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _body_length(body):
    if body is None:
        return 0
    if isinstance(body, (bytes, bytearray)):
        return len(body)
    if isinstance(body, str):
        return len(body.encode())
    if hasattr(body, "fileno"):
        try:
            stat = os.fstat(body.fileno())
            return max(0, stat.st_size - body.tell())
        except OSError:
            return None
    return None


def _request_stdlib(method, url, *, headers=None, params=None, json=None, data=None, files=None, timeout=None, stream=False):
    parsed = urlparse(url)
    if params:
        query = urlencode(params, doseq=True)
        existing = parsed.query
        parsed = parsed._replace(query=f"{existing}&{query}" if existing else query)
        url = urlunparse(parsed)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    request_headers = {k: v for k, v in (headers or {}).items()}
    request_headers.setdefault("User-Agent", f"pcxa-cli/{__version__}")
    body = None
    if json is not None:
        body = _json.dumps(json).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif files is not None:
        body, content_type = _encode_multipart(data or {}, files)
        request_headers.setdefault("Content-Type", content_type)
    elif isinstance(data, dict):
        body = urlencode(data, doseq=True).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        body = data.encode("utf-8")
    else:
        body = data

    length = _body_length(body)
    if body is not None and length is not None:
        request_headers.setdefault("Content-Length", str(length))

    host = parsed.hostname or ""
    port = parsed.port
    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = connection_cls(host, port=port, timeout=timeout)
    try:
        conn.request(method.upper(), path, body=body, headers=request_headers)
        raw = conn.getresponse()
        response_headers = {k.lower(): v for k, v in raw.getheaders()}
        if stream:
            return Response(raw.status, raw.reason, response_headers, None, url, raw=raw, connection=conn)
        body_bytes = raw.read()
        conn.close()
        return Response(raw.status, raw.reason, response_headers, body_bytes, url)
    except (OSError, socket.timeout, http.client.HTTPException) as exc:
        try:
            conn.close()
        except Exception:
            pass
        raise ConnectionError(str(exc)) from exc


class Session:
    def __init__(self):
        self.headers = {}

    def request(self, method, url, **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}) or {})
        return _request_stdlib(method, url, headers=headers, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


class RequestsCompat:
    HTTPError = HTTPError
    ConnectionError = ConnectionError
    Session = Session

    @staticmethod
    def request(method, url, **kwargs):
        return _request_stdlib(method, url, **kwargs)

    @staticmethod
    def get(url, **kwargs):
        return _request_stdlib("GET", url, **kwargs)

    @staticmethod
    def post(url, **kwargs):
        return _request_stdlib("POST", url, **kwargs)

    @staticmethod
    def put(url, **kwargs):
        return _request_stdlib("PUT", url, **kwargs)


requests = RequestsCompat


__all__ = [
    "HTTPError",
    "ConnectionError",
    "Response",
    "Session",
    "RequestsCompat",
    "requests",
]
