"""Stdlib-only HTTP client compatible with a small subset of `requests`.

Exports:
    HTTPError, EdgeBlockedError, ConnectionError, Response, Session,
    RequestsCompat
    requests   — singleton (== RequestsCompat) used like `requests.get(...)`

Connection pooling:
    Connections are pooled per (scheme, host, port, proxy) and reused via
    HTTP/1.1 keep-alive. This eliminates ~50-150 ms of TCP+TLS handshake
    per request and is the dominant perf win for high-concurrency upload
    workloads. See issue #661.

    Tuning knobs (env vars):
        PCXA_HTTP_POOL_SIZE   per-target connection cap (default 32)
        PCXA_HTTP_IDLE_SECS   reap idle conns older than this (default 60)
        PCXA_HTTP_POOL_OFF    set to "1" to disable pooling (fall back to
                              fresh-conn-per-request — emergency switch)
        PCXA_HTTP_DEBUG       set to "1" to print pool stats at exit
        PCXA_HTTP_TIMEOUT     default read timeout in seconds (default 30)

Timeouts:
    Any request that doesn't pass an explicit ``timeout`` gets
    ``get_default_timeout()``. That default is 30s, overridable per-shell
    with ``PCXA_HTTP_TIMEOUT`` or per-run with ``pcxa --timeout <seconds>``
    (which calls ``set_default_timeout()`` before the client is built).
    Keeping the fallback here — rather than hard-coding 30 at each call
    site — is what makes one knob cover every path, including the ones
    that never grew a timeout argument of their own.
"""

import atexit
import http.client
import json as _json
import os
import socket
import threading
import time
import uuid
from urllib.parse import urlencode, urlparse, urlunparse

from pcxa import __version__


class HTTPError(Exception):
    """HTTP error carrying the response, compatible with requests.HTTPError use."""

    def __init__(self, response):
        self.response = response
        super().__init__(f"{response.status_code} {response.reason}: {response.url}")


class EdgeBlockedError(HTTPError):
    """The CDN/WAF in front of the API rejected the request — the app never saw it.

    Subclasses ``HTTPError`` so existing ``except HTTPError`` handlers keep
    working; catch this first when the distinction matters.

    Cloudflare answers a blocked request with an HTML interstitial and a 403,
    which is indistinguishable from a genuine permission denial unless you
    look at the body. That cost real debugging time on #1946, where a managed
    WAF rule matched an SQLi/XSS signature *inside* the bytes of ordinary
    PDF/XLSX uploads and rejected 62 of 110,864 files — deterministically, on
    content, forever. Naming the failure is what lets a caller tell "you are
    not allowed to do this" apart from "the edge ate your request".
    """


def is_edge_block(response):
    """True when ``response`` is a CDN/WAF interstitial rather than an API reply.

    Deliberately narrow. The API is JSON-only, so an HTML body on a 403/406/503
    already means something upstream answered instead of Django. Requiring the
    HTML *and* the status keeps a legitimate DRF ``403 {"detail": ...}`` from
    ever being misreported as an edge block.
    """
    if response.status_code not in (403, 406, 503):
        return False
    content_type = (response.headers.get("content-type") or "").lower()
    if "html" not in content_type:
        return False
    return True


def describe_edge_block(response):
    """One-line, actionable summary of an edge block — never raw HTML."""
    ray = response.headers.get("cf-ray") or ""
    server = (response.headers.get("server") or "").lower()
    vendor = "Cloudflare" if ("cloudflare" in server or ray) else "The CDN/WAF"
    detail = (
        f"{vendor} blocked this request before it reached the API "
        f"({response.status_code} at {response.url}). This is an edge rule, "
        f"not a permission denial — retrying will fail identically."
    )
    if ray:
        detail += f" cf-ray={ray}"
    return detail


class ConnectionError(Exception):
    """Raised when a network connection cannot be established."""


# --------------------------------------------------------------------------
# Default timeout
# --------------------------------------------------------------------------

# Fallback read timeout for requests that don't pass one. 30s is fine for
# ordinary reads but too tight for write endpoints on large projects —
# `POST folders/` alone measured ~9s on project 4, and a single call
# crossing the ceiling used to abort a whole `files sync` run
# (PCX-Analytics/pcxa#1689, #1454).
FALLBACK_TIMEOUT = 30.0

TIMEOUT_ENV_VAR = "PCXA_HTTP_TIMEOUT"


def _coerce_timeout(value):
    """Return ``value`` as a positive float, or None if it isn't usable."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


_DEFAULT_TIMEOUT = _coerce_timeout(os.environ.get(TIMEOUT_ENV_VAR)) or FALLBACK_TIMEOUT


def get_default_timeout():
    """Read timeout applied to requests that don't pass one explicitly."""
    return _DEFAULT_TIMEOUT


def set_default_timeout(seconds):
    """Set the process-wide default read timeout. Returns the value applied.

    Ignores non-positive / unparseable input so a bad ``--timeout 0`` can't
    silently turn every request into an unbounded block.
    """
    global _DEFAULT_TIMEOUT
    coerced = _coerce_timeout(seconds)
    if coerced is not None:
        _DEFAULT_TIMEOUT = coerced
    return _DEFAULT_TIMEOUT


# --------------------------------------------------------------------------
# Connection pool
# --------------------------------------------------------------------------

_POOL_LOCK = threading.Lock()
_POOL = {}  # key -> list[(conn, last_used_monotonic)]
_POOL_SIZE = int(os.environ.get("PCXA_HTTP_POOL_SIZE", "32"))
_IDLE_SECS = float(os.environ.get("PCXA_HTTP_IDLE_SECS", "60"))
_POOL_OFF = os.environ.get("PCXA_HTTP_POOL_OFF") == "1"
_DEBUG = os.environ.get("PCXA_HTTP_DEBUG") == "1"

_STATS = {
    "new_conns": 0,
    "reused_conns": 0,
    "handshake_ms_total": 0.0,
    "requests": 0,
    "retries_on_disconnect": 0,
}
_STATS_LOCK = threading.Lock()


def _stat_incr(key, by=1):
    if not _DEBUG:
        return
    with _STATS_LOCK:
        _STATS[key] = _STATS[key] + by


def _print_stats():
    if not _DEBUG:
        return
    with _STATS_LOCK:
        total = _STATS["new_conns"] + _STATS["reused_conns"]
        reuse_pct = (100.0 * _STATS["reused_conns"] / total) if total else 0.0
        new_count = _STATS["new_conns"]
        avg_hs = (_STATS["handshake_ms_total"] / new_count) if new_count else 0.0
        import sys
        sys.stderr.write(
            f"\n[pcxa-http] requests={_STATS['requests']} "
            f"new_conns={new_count} reused={_STATS['reused_conns']} "
            f"reuse_rate={reuse_pct:.1f}% "
            f"avg_handshake_ms={avg_hs:.1f} "
            f"retries_on_disconnect={_STATS['retries_on_disconnect']}\n"
        )


atexit.register(_print_stats)


def _pool_key(scheme, host, port, proxy_url):
    return (scheme, host, port or (443 if scheme == "https" else 80), proxy_url or "")


def _make_connection(scheme, host, port, proxy_url, timeout):
    """Create a fresh http.client connection (un-connected — handshake on first use)."""
    if proxy_url:
        proxy_parsed = urlparse(proxy_url)
        proxy_host = proxy_parsed.hostname or "localhost"
        proxy_port = proxy_parsed.port or (
            443 if proxy_parsed.scheme == "https" else 80
        )
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                proxy_host, port=proxy_port, timeout=timeout
            )
            conn.set_tunnel(host, port=port or 443)
        else:
            conn = http.client.HTTPConnection(
                proxy_host, port=proxy_port, timeout=timeout
            )
    else:
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        conn = cls(host, port=port, timeout=timeout)
    return conn


def _acquire_conn(scheme, host, port, proxy_url, timeout):
    """Pop a live conn from the pool, or create a new one. Returns (conn, was_reused)."""
    if _POOL_OFF:
        return _make_connection(scheme, host, port, proxy_url, timeout), False

    key = _pool_key(scheme, host, port, proxy_url)
    now = time.monotonic()
    with _POOL_LOCK:
        bucket = _POOL.get(key)
        while bucket:
            conn, last_used = bucket.pop()
            if (now - last_used) > _IDLE_SECS:
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            # Refresh timeout on the existing conn; underlying socket retains it.
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
            except Exception:
                pass
            return conn, True
    # No live conn — make new
    return _make_connection(scheme, host, port, proxy_url, timeout), False


def _release_conn(conn, scheme, host, port, proxy_url, keep_alive=True):
    """Return conn to the pool, or close it."""
    if not keep_alive or _POOL_OFF:
        try:
            conn.close()
        except Exception:
            pass
        return
    if conn.sock is None:
        # Already closed.
        return
    key = _pool_key(scheme, host, port, proxy_url)
    with _POOL_LOCK:
        bucket = _POOL.setdefault(key, [])
        if len(bucket) >= _POOL_SIZE:
            # Pool full — close oldest, push newest.
            try:
                old_conn, _ = bucket.pop(0)
                old_conn.close()
            except Exception:
                pass
        bucket.append((conn, time.monotonic()))


def _close_all():
    with _POOL_LOCK:
        for bucket in _POOL.values():
            for conn, _ in bucket:
                try:
                    conn.close()
                except Exception:
                    pass
        _POOL.clear()


atexit.register(_close_all)


# --------------------------------------------------------------------------
# Response — must hold (conn, pool key) so it can return conn to pool on close
# --------------------------------------------------------------------------


class Response:
    def __init__(self, status_code, reason, headers, body, url, raw=None,
                 connection=None, _pool_key_args=None, _keep_alive=True):
        self.status_code = status_code
        self.reason = reason
        self.headers = headers
        self._body = body
        self.url = url
        self._raw = raw
        self._connection = connection
        # (scheme, host, port, proxy_url) — used to return conn to its bucket
        self._pool_key_args = _pool_key_args
        self._keep_alive = _keep_alive
        self._released = False

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
            if is_edge_block(self):
                raise EdgeBlockedError(self)
            raise HTTPError(self)

    def close(self):
        # Drain raw, then either return conn to pool or hard-close.
        raw = self._raw
        self._raw = None
        if raw is not None:
            try:
                raw.read()  # drain body so socket is clean for reuse
            except Exception:
                self._keep_alive = False
            try:
                raw.close()
            except Exception:
                pass
        conn = self._connection
        self._connection = None
        if conn is None or self._released:
            return
        self._released = True
        if self._pool_key_args and self._keep_alive:
            scheme, host, port, proxy_url = self._pool_key_args
            _release_conn(conn, scheme, host, port, proxy_url, keep_alive=True)
        else:
            try:
                conn.close()
            except Exception:
                pass


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


def _resolve_proxy(scheme, target_host):
    """Return the proxy URL for ``target_host`` or ``None`` to connect direct.

    Honors the standard env-var contract used by curl/requests/etc.:
    ``HTTP_PROXY`` / ``HTTPS_PROXY`` per scheme, with ``NO_PROXY`` as a
    comma-separated bypass list (``*`` matches all).

    The Claude Agent SDK exports ``HTTP_PROXY=http://localhost:<port>``
    and ``HTTPS_PROXY=http://localhost:<port>`` whenever the Bash
    subprocess is sandboxed, then strips the sandbox's network
    namespace; without honoring those vars, every outbound request
    dies with ``socket.gaierror`` because there's no route to the
    target host. Surfaces in pmapp2 as "pcxa can't reach
    http://django:8000".
    """
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    if no_proxy.strip() == "*":
        return None
    if no_proxy:
        for entry in no_proxy.split(","):
            entry = entry.strip().lstrip(".")
            if not entry:
                continue
            if target_host == entry or target_host.endswith("." + entry):
                return None
    if scheme == "https":
        return (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or None
        )
    return (
        os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )


def _do_request(conn, was_reused, method, request_target, body, request_headers,
                stream, url, pool_key_args):
    """Issue a single request on conn. Caller handles retry on disconnect."""
    # If new conn, measure handshake (first request triggers connect()).
    if not was_reused:
        t0 = time.monotonic()
        conn.connect()
        if _DEBUG:
            with _STATS_LOCK:
                _STATS["handshake_ms_total"] += (time.monotonic() - t0) * 1000.0
                _STATS["new_conns"] += 1
    else:
        _stat_incr("reused_conns")

    _stat_incr("requests")

    conn.request(method, request_target, body=body, headers=request_headers)
    raw = conn.getresponse()
    response_headers = {k.lower(): v for k, v in raw.getheaders()}
    conn_header = response_headers.get("connection", "").lower()
    keep_alive = "close" not in conn_header
    if stream:
        return Response(
            raw.status, raw.reason, response_headers, None, url,
            raw=raw, connection=conn,
            _pool_key_args=pool_key_args, _keep_alive=keep_alive,
        )
    body_bytes = raw.read()
    # Return conn to pool now since body fully drained.
    if pool_key_args:
        scheme, host, port, proxy_url = pool_key_args
        _release_conn(conn, scheme, host, port, proxy_url, keep_alive=keep_alive)
    else:
        try:
            conn.close()
        except Exception:
            pass
    return Response(raw.status, raw.reason, response_headers, body_bytes, url)


def _request_stdlib(method, url, *, headers=None, params=None, json=None, data=None,
                    files=None, timeout=None, stream=False):
    if timeout is None:
        timeout = get_default_timeout()
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
    # Explicit keep-alive — http.client defaults to HTTP/1.1 which is keep-alive
    # by default, but some intermediaries treat the absence of this header as
    # ambiguous. Cheap to add, eliminates a class of "connection: close" races.
    request_headers.setdefault("Connection", "keep-alive")

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
    proxy_url = _resolve_proxy(parsed.scheme, host)
    if proxy_url and parsed.scheme == "http":
        # Plain HTTP proxy: send absolute URL in request line.
        request_target = url
    else:
        request_target = path

    pool_key_args = (parsed.scheme, host, port, proxy_url) if not _POOL_OFF else None

    # Retry once on RemoteDisconnected / BadStatusLine — these happen when
    # the server side has already closed a pooled conn we just popped.
    # Body must be re-sendable; for file-like bodies we'd need to seek(0).
    # In practice JSON/multipart/bytes bodies are always re-sendable.
    last_exc = None
    for attempt in range(2):
        conn, was_reused = _acquire_conn(parsed.scheme, host, port, proxy_url, timeout)
        try:
            return _do_request(
                conn, was_reused, method.upper(), request_target, body,
                request_headers, stream, url, pool_key_args,
            )
        except (http.client.RemoteDisconnected, http.client.BadStatusLine,
                ConnectionResetError, BrokenPipeError) as exc:
            try:
                conn.close()
            except Exception:
                pass
            last_exc = exc
            if was_reused and attempt == 0:
                # Stale pooled conn — try once more with a fresh one.
                _stat_incr("retries_on_disconnect")
                continue
            raise ConnectionError(str(exc)) from exc
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            try:
                conn.close()
            except Exception:
                pass
            raise ConnectionError(str(exc)) from exc
    # Unreachable
    raise ConnectionError(str(last_exc) if last_exc else "unknown error")


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
    EdgeBlockedError = EdgeBlockedError
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
    "EdgeBlockedError",
    "is_edge_block",
    "describe_edge_block",
    "ConnectionError",
    "Response",
    "Session",
    "RequestsCompat",
    "requests",
    "FALLBACK_TIMEOUT",
    "TIMEOUT_ENV_VAR",
    "get_default_timeout",
    "set_default_timeout",
]
