"""Safe document errors; API and SEB client responses retain their contracts."""
from pathlib import Path
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException
from .config import FRONTEND_DIR

COPY = {
    401: ("Sign in to continue", "Your session has ended or you are not signed in.", "/", "Sign in"),
    403: ("Access unavailable", "This account or exam session cannot open this page.", "/", "Return to sign in"),
    404: ("Page unavailable", "This link is unavailable. Check the address or ask the assessment organizer for a new link.", "/", "Return to sign in"),
    503: ("Service temporarily unavailable", "AssessBridge could not complete your request. Try again shortly.", "", "Retry"),
}

async def page_http_error(request, exc: HTTPException):
    document = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if not document or request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    title, message, href, action = COPY.get(exc.status_code, (
        "Could not open this page", "Try again or contact the assessment organizer if the problem continues.", "/", "Return to sign in"))
    markup = (Path(FRONTEND_DIR) / "error.html").read_text(encoding="utf-8")
    # Only fixed, server-owned copy is inserted. Exception details and request URLs are never rendered.
    for key, value in {"TITLE": title, "MESSAGE": message, "HREF": href, "ACTION": action}.items():
        markup = markup.replace("{{" + key + "}}", value)
    return HTMLResponse(markup, status_code=exc.status_code, headers={**(exc.headers or {}), "Cache-Control": "no-store"})
