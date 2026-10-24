"""Download publisher HTML and extract article text; do not verify Events."""

from ipaddress import ip_address
from socket import SOCK_STREAM, getaddrinfo
from urllib.parse import urljoin, urlsplit

import requests
from requests.exceptions import RequestException
from trafilatura import extract

_MAX_HTML_BYTES = 2_000_000
_MAX_REDIRECTS = 3
_REDIRECT_CODES = {301, 302, 303, 307, 308}


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
    current_url = url.strip()

    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_public_web_url(current_url):
            return None

        try:
            response = requests.get(
                current_url,
                headers={"User-Agent": "MontyToolNewsResearch/1.0"},
                timeout=(3, 10),
                stream=True,
                allow_redirects=False,
            )
            try:
                if response.status_code in _REDIRECT_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue

                if response.status_code != 200:
                    return None

                content_type = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    return None

                content_length = response.headers.get("Content-Length")
                if (
                    content_length is not None
                    and int(content_length) > _MAX_HTML_BYTES
                ):
                    return None

                data = bytearray()
                for chunk in response.iter_content(chunk_size=65_536):
                    data.extend(chunk)
                    if len(data) > _MAX_HTML_BYTES:
                        return None

                if not data:
                    return None

                try:
                    html = data.decode(response.encoding or "utf-8", errors="replace")
                except LookupError:
                    html = data.decode("utf-8", errors="replace")
                return html, current_url
            finally:
                response.close()
        except (RequestException, OSError, ValueError):
            return None

    return None


def extract_publisher_body(html: str, *, url: str) -> str | None:
    """Return extracted article text, not an event-match verdict."""
    if not html.strip():
        return None

    text = extract(html, url=url, include_comments=False)
    if text is None:
        return None

    cleaned = text.strip()
    return cleaned or None
