"""Tiny stdlib-only HTTP client.

Skills run inside sandboxes that may or may not have ``requests``; the Iranian
endpoints are also behind flaky infrastructure, so every call here has a hard
timeout, a bounded retry budget and returns ``(payload, error)`` instead of
raising.  Callers turn errors into ``FetchResult(ok=False, error=...)``.
"""

from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

DEFAULT_TIMEOUT = 8.0
DEFAULT_RETRIES = 2
USER_AGENT = "iran-market-analysis/1.0 (+deerflow skill)"

#: The Iranian exchanges do not all serve valid chains; skipping verification is
#: opt-in per call so the operator decides.
_SSL_CTX = ssl.create_default_context()


def build_url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(clean)}"
    return url


def http_get(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    headers: dict[str, str] | None = None,
    verify_tls: bool = True,
) -> tuple[bytes, str]:
    """GET a URL.  Returns ``(body, error)``; exactly one of them is empty."""
    ctx = _SSL_CTX if verify_tls else ssl._create_unverified_context()  # noqa: S323 - operator opt-in
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        **(headers or {}),
    }
    last_error = ""
    for attempt in range(max(1, retries + 1)):
        try:
            request = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:  # noqa: S310
                raw = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                if encoding == "gzip":
                    raw = gzip.decompress(raw)
                elif encoding == "deflate":
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return raw, ""
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} {exc.reason}"
            if exc.code in (400, 401, 403, 404):
                return b"", last_error
        except urllib.error.URLError as exc:
            last_error = f"URL error: {exc.reason}"
        except TimeoutError:
            last_error = "timeout"
        except (ssl.SSLError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))
    return b"", last_error or "unknown error"


def http_json(url: str, **kwargs: Any) -> tuple[Any, str]:
    body, error = http_get(url, **kwargs)
    if error:
        return None, error
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None, "empty response"
    # Some TSETMC endpoints wrap JSON in a UTF-8 BOM or return a leading "("
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc} ({text[:80]!r})"


def http_text(url: str, **kwargs: Any) -> tuple[str, str]:
    body, error = http_get(url, **kwargs)
    if error:
        return "", error
    return body.decode("utf-8", errors="replace"), ""


__all__ = ["DEFAULT_TIMEOUT", "build_url", "http_get", "http_json", "http_text"]
