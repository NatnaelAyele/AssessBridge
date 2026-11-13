"""Verify SEB's URL-bound Config Key, including the modern WebView JS bridge."""

import hashlib
import hmac
import re

from fastapi import HTTPException, Request

from .config import SEB_BASE_URL, SEB_CONFIG_KEY, SEB_REQUIRED


def config_hash(url: str, key: str | None = None) -> str:
    return hashlib.sha256((url.split("#", 1)[0] + (key or SEB_CONFIG_KEY)).encode("utf-8")).hexdigest()


def require_seb(request: Request, page: str | tuple[str, ...], key: str | None = None) -> None:
    if not SEB_REQUIRED:
        return
    actual_key = (key or SEB_CONFIG_KEY).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", actual_key):
        raise HTTPException(status_code=503, detail="SEB configuration is not installed on this server")

    # Modern macOS WKWebView supplies a hash for the document URL through its
    # JavaScript API. Windows and classic WebView may additionally send the
    # official header, which is hashed for the actual request URL.
    js_proof = request.headers.get("X-AssessBridge-SEB-ConfigKeyHash", "").lower()
    native_proof = request.headers.get("X-SafeExamBrowser-ConfigKeyHash", "").lower()
    pages = (page,) if isinstance(page, str) else page
    request_url = SEB_BASE_URL + request.url.path
    if request.url.query:
        request_url += "?" + request.url.query
    if (any(hmac.compare_digest(js_proof, config_hash(SEB_BASE_URL + allowed_page, actual_key)) for allowed_page in pages)
            or hmac.compare_digest(native_proof, config_hash(request_url, actual_key))):
        return
    raise HTTPException(status_code=403, detail="Open this assessment with the approved Safe Exam Browser configuration")
