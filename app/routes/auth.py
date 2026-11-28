
"""Admin login flow for the Legal Sources page (Phase P0A.2)."""

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
    has_admin_session,
    session_cookie_name,
    session_token_from_headers,
    scope_headers,
)


router = APIRouter(prefix="/admin", include_in_schema=False)
logger = logging.getLogger(__name__)


def _has_session(request: Request) -> bool:
    settings, store = get_security(request)
    return has_admin_session(
        scope_headers(request.scope), settings, store
    )


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

    if _has_session(request):
        return RedirectResponse("/admin/laws", status_code=303)

    return _login_page(request, None, 200)


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
    response.delete_cookie(
        key=session_cookie_name(settings),
        path="/",
        secure=settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/laws")
def admin_laws_page(request: Request):

    # Server-side check: no session, no admin page
    if not _has_session(request):
        return RedirectResponse("/admin/login", status_code=303)

    return FileResponse(
        path=BASE_DIR / "templates" / "admin_laws.html",
        media_type="text/html",
        headers=ADMIN_PAGE_HEADERS,
    )
