"""A CDN/WAF interstitial must not masquerade as a permission denial (#1946).

Cloudflare answers a blocked request with `403` and an HTML page. Dumping that
HTML at the user — which is what the CLI used to do — reads exactly like the
API saying "you may not do this", and sent the #1946 investigation down the
permissions path for two days. `EdgeBlockedError` names the failure instead.

The risk on the other side is over-triggering: a real DRF 403 must never be
reported as an edge block, or we would teach users to ignore genuine
permission errors. Both directions are pinned here.
"""

import pytest

from pcxa._http import (
    EdgeBlockedError,
    HTTPError,
    describe_edge_block,
    is_edge_block,
)


class FakeResponse:
    def __init__(self, status_code, headers, body=b"", url="https://api.pcxa.app/api/x/"):
        self.status_code = status_code
        self.reason = "Forbidden"
        self.headers = headers
        self.content = body
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            if is_edge_block(self):
                raise EdgeBlockedError(self)
            raise HTTPError(self)


CF_BLOCK_BODY = (
    b'<!DOCTYPE html><html lang="en"><head><title>Blocked</title></head>'
    b"<body>Sorry, you have been blocked</body></html>"
)


def _cf_block(status=403):
    return FakeResponse(
        status,
        {
            "content-type": "text/html; charset=UTF-8",
            "server": "cloudflare",
            "cf-ray": "9a1b2c3d4e5f6789-IAD",
        },
        CF_BLOCK_BODY,
    )


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def test_cloudflare_html_403_is_an_edge_block():
    assert is_edge_block(_cf_block()) is True


@pytest.mark.parametrize("status", [403, 406, 503])
def test_edge_statuses_with_html_are_detected(status):
    assert is_edge_block(_cf_block(status)) is True


def test_genuine_drf_permission_denial_is_not_an_edge_block():
    """The false-positive case that would matter most if we got it wrong."""
    resp = FakeResponse(
        403,
        {"content-type": "application/json"},
        b'{"detail":"You do not have permission to perform this action."}',
    )
    assert is_edge_block(resp) is False


def test_html_on_a_non_edge_status_is_not_an_edge_block():
    # A 500 HTML debug page is an application fault, not an edge rejection.
    resp = FakeResponse(500, {"content-type": "text/html"}, b"<html>oops</html>")
    assert is_edge_block(resp) is False


def test_missing_content_type_is_not_an_edge_block():
    assert is_edge_block(FakeResponse(403, {}, b"")) is False


# --------------------------------------------------------------------------
# Raising
# --------------------------------------------------------------------------


def test_raise_for_status_raises_the_typed_error():
    with pytest.raises(EdgeBlockedError):
        _cf_block().raise_for_status()


def test_edge_blocked_error_is_still_an_http_error():
    """Existing `except HTTPError` handlers must keep catching it."""
    assert issubclass(EdgeBlockedError, HTTPError)
    with pytest.raises(HTTPError):
        _cf_block().raise_for_status()


def test_permission_denial_still_raises_plain_http_error():
    resp = FakeResponse(403, {"content-type": "application/json"}, b'{"detail":"nope"}')
    with pytest.raises(HTTPError) as exc:
        resp.raise_for_status()
    assert not isinstance(exc.value, EdgeBlockedError)


# --------------------------------------------------------------------------
# Message
# --------------------------------------------------------------------------


def test_description_names_the_vendor_and_ray_and_omits_html():
    msg = describe_edge_block(_cf_block())
    assert "Cloudflare" in msg
    assert "9a1b2c3d4e5f6789-IAD" in msg
    assert "not a permission denial" in msg
    assert "<html" not in msg.lower()
    assert "doctype" not in msg.lower()


def test_description_falls_back_when_the_vendor_is_unidentified():
    resp = FakeResponse(403, {"content-type": "text/html"}, b"<html>blocked</html>")
    msg = describe_edge_block(resp)
    assert "CDN/WAF" in msg
    assert "cf-ray" not in msg
