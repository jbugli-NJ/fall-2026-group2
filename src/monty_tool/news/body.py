"""Download publisher HTML and extract article text; do not verify Events."""

import os
from ipaddress import ip_address
from socket import SOCK_STREAM, getaddrinfo
from urllib.parse import urlsplit

from trafilatura import extract, fetch_response
from trafilatura.settings import use_config

_MAX_HTML_BYTES = 2_000_000

_DOWNLOAD_CONFIG = use_config()
_DOWNLOAD_CONFIG["DEFAULT"].update({
    "DOWNLOAD_TIMEOUT": "10",
    "MAX_FILE_SIZE": str(_MAX_HTML_BYTES),
    "MAX_REDIRECTS": "3",
    "SSRF_PROTECTION": "on",
    "INSECURE_SSL_FALLBACK": "off",
})

_PROXY_VARS = (
    "http_proxy", "https_proxy", "HTTP_PROXY",
    "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
)


def _is_public_web_url(url: str) -> bool:
    """Reject non-web and private-network destinations, including redirects."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False

        port = 443 if parsed.scheme == "https" else 80
        if parsed.port not in (None, port):
            return False

        addresses = getaddrinfo(host, port, type=SOCK_STREAM)
        return bool(addresses) and all(
            ip_address(address[4][0]).is_global for address in addresses
        )
    except (OSError, ValueError):
        return False


def fetch_publisher_html(url: str) -> tuple[str, str] | None:
    """Return (HTML, final URL), or None when the page cannot be read."""
    requested_url = url.strip()
    if not _is_public_web_url(requested_url):
        return None

    # Trafilatura's private-address protection is disabled with a proxy.
    if any(os.environ.get(name) for name in _PROXY_VARS):
        return None

    try:
        response = fetch_response(
            requested_url, decode=True, config=_DOWNLOAD_CONFIG
        )
    except Exception:
        return None
    if response is None or response.status != 200 or not response.html:
        return None
    if not _is_public_web_url(response.url):
        return None

    content_type = (
        (response.headers or {})
        .get("content-type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return None
    if len(response.data) > _MAX_HTML_BYTES:
        return None

    return response.html, response.url


def extract_publisher_body(html: str, *, url: str) -> str | None:
    """Return extracted article text, not an event-match verdict."""
    if not html.strip():
        return None

    text = extract(html, url=url, include_comments=False)
    if text is None:
        return None

    cleaned = text.strip()
    return cleaned or None
