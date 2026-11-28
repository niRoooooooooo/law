
"""
Admin login flow for the Legal Sources page (Phase P0A.2, F1).

Every visit to the admin page needs the administrator key:
login issues a session with a single-use page entry; the first
/admin/laws load consumes it. A refresh, a back/forward visit or
re-opening the page later finds it used up, revokes the session
on the server and sends the browser to /admin/login. AJAX calls
from the open page keep working with the same session.
"""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
)

from app.config import BASE_DIR, templates
from app.security import (
    ADMIN_PAGE_HEADERS,
    ADMIN_REQUEST_HEADER,
    admin_key_matches,
    get_security,
    session_cookie_name,
    session_token_from_headers,
    scope_headers,
)


router = APIRouter(prefix="/admin", include_in_schema=False)
logger = logging.getLogger(__name__)


def _clear_session_cookie(response, settings) -> None:
    response.delete_cookie(
        key=session_cookie_name(settings),
        path="/",
        secure=settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _end_browser_session(request: Request, response) -> None:
    """Revoke the request's admin session (if any) and clear its cookie."""
    settings, store = get_security(request)
    token = session_token_from_headers(scope_headers(request.scope), settings)

    if token:
        store.revoke(token)
        _clear_session_cookie(response, settings)


def _login_page(request: Request, error: str | None, status_code: int):
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": error},
        status_code=status_code,
        headers=ADMIN_PAGE_HEADERS,
    )


@router.get("/login")
def admin_login_page(request: Request):

    # Showing the login form ends any earlier session: the key
    # must be entered again to reach the admin page.
    response = _login_page(request, None, 200)
    _end_browser_session(request, response)
    return response


@router.post("/login")
def admin_login(request: Request, admin_key: str = Form("")):

    settings, store = get_security(request)

    if not admin_key_matches(admin_key, settings):
        # Never log the submitted value
        logger.warning("Admin login failed.")
        return _login_page(
            request, "Invalid administrator key.", 401
        )

    token = store.create()

    response = RedirectResponse("/admin/laws", status_code=303)
    response.set_cookie(
        key=session_cookie_name(settings),
        value=token,
        max_age=settings.admin_session_minutes * 60,
        path="/",
        secure=settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"

    logger.info("Admin login succeeded.")
    return response


@router.post("/logout")
def admin_logout(request: Request):

    settings, store = get_security(request)

    # CSRF guard: only the admin page's fetch() sends this header
    if request.headers.get(ADMIN_REQUEST_HEADER) != "1":
        return JSONResponse(
            {"detail": "Missing admin request header."},
            status_code=403,
        )

    store.revoke(
        session_token_from_headers(
            scope_headers(request.scope), settings
        )
    )

    response = JSONResponse({"message": "Logged out."})
    _clear_session_cookie(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/laws")
def admin_laws_page(request: Request):

    settings, store = get_security(request)
    token = session_token_from_headers(scope_headers(request.scope), settings)

    # Server-side check: only the first page load after login is
    # allowed. Refresh / revisit revokes the session (done inside
    # consume_page_entry) and asks for the key again.
    if not store.consume_page_entry(token):
        response = RedirectResponse("/admin/login", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        if token:
            _clear_session_cookie(response, settings)
        return response

    return FileResponse(
        path=BASE_DIR / "templates" / "admin_laws.html",
        media_type="text/html",
        headers=ADMIN_PAGE_HEADERS,
    )
