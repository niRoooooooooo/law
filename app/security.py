
"""
Access security (Phase P0A.2).

- The public website is open.
- Legal Sources admin (page + API) needs either a short-lived
  admin browser session or the X-Admin-Key header.
- AI endpoints are restricted (direct local requests, an admin
  session, or - only with TOR_LAW_ALLOW_TUNNEL_AI=1 - requests
  arriving through the local Cloudflare Tunnel). Allowed requests are then rate limited per client and
  rejected early while the inference slot is busy (P0B.1).

AccessControlMiddleware is pure ASGI: it runs before routing and
before any form/multipart body is read or parsed. It never logs
credentials, cookies or session tokens.

This is temporary access control, not production authentication.
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.requests import Request, cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import SecuritySettings, templates
from app.services.ai_limits import (
    ConcurrencyLimiter,
    RateLimiter,
    client_key,
    tunnel_client_ip,
)


logger = logging.getLogger(__name__)

ADMIN_API_PREFIX = "/admin/legal-sources"

# Routes that start Ollama work
AI_PATHS = {"/intake/analyze"}

# Header the admin UI sends on state-changing requests (CSRF guard).
# Cross-site pages cannot add custom headers without CORS approval.
ADMIN_REQUEST_HEADER = "x-admin-request"

MAX_ADMIN_SESSIONS = 20

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Present on any request that arrived through a proxy or tunnel
PROXY_HEADERS = (
    "cf-connecting-ip",
    "cf-ray",
    "x-forwarded-for",
    "forwarded",
)

# Extra headers for admin HTML responses
ADMIN_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


# ------------------------------------------
# Admin key
# ------------------------------------------

def admin_key_matches(given: str | None, settings: SecuritySettings) -> bool:

    if not given or not settings.admin_key:
        return False

    # Compare bytes: str comparison raises on non-ASCII input
    return hmac.compare_digest(
        given.encode("utf-8"),
        settings.admin_key.encode("utf-8")
    )


# ------------------------------------------
# Admin browser sessions (in memory)
# ------------------------------------------

@dataclass
class _Session:
    expires_at: float
    created_at: float


class AdminSessionStore:
    """
    Random opaque tokens, stored only as SHA-256 hashes.
    In memory: sessions end on restart (single-process server).
    """

    def __init__(self, lifetime_seconds: int, clock=time.monotonic):
        self.lifetime_seconds = lifetime_seconds
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self) -> str:

        token = secrets.token_urlsafe(32)
        now = self._clock()

        with self._lock:
            self._purge(now)

            # Bound memory use; drop the oldest session
            while len(self._sessions) >= MAX_ADMIN_SESSIONS:
                oldest = min(
                    self._sessions,
                    key=lambda k: self._sessions[k].created_at
                )
                del self._sessions[oldest]

            self._sessions[self._hash(token)] = _Session(
                expires_at=now + self.lifetime_seconds,
                created_at=now
            )

        return token

    def is_valid(self, token: str | None) -> bool:

        if not token or len(token) > 128:
            return False

        key = self._hash(token)
        now = self._clock()

        with self._lock:
            session = self._sessions.get(key)

            if session is None:
                return False

            if session.expires_at <= now:
                del self._sessions[key]
                return False

            return True

    def revoke(self, token: str | None) -> None:

        if not token:
            return

        with self._lock:
            self._sessions.pop(self._hash(token), None)

    def _purge(self, now: float) -> None:
        expired = [
            k for k, s in self._sessions.items()
            if s.expires_at <= now
        ]
        for k in expired:
            del self._sessions[k]


def session_cookie_name(settings: SecuritySettings) -> str:
    # __Host- prefix: Secure, Path=/, no Domain, so other
    # subdomains (e.g. other tunnels) cannot plant or override it
    if settings.admin_cookie_secure:
        return "__Host-torlaw_admin"
    return "torlaw_admin"


# ------------------------------------------
# Request checks (shared by middleware and routes)
# ------------------------------------------

def _header(headers: dict[str, str], name: str) -> str | None:
    return headers.get(name)


def scope_headers(scope: Scope) -> dict[str, str]:
    # Header names are lower-case in ASGI; keep the first value
    result: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        name = key.decode("latin-1").lower()
        result.setdefault(name, value.decode("latin-1"))
    return result


def session_token_from_headers(
    headers: dict[str, str],
    settings: SecuritySettings
) -> str | None:

    cookie_header = _header(headers, "cookie")

    if not cookie_header:
        return None

    return cookie_parser(cookie_header).get(
        session_cookie_name(settings)
    )


def has_admin_session(
    headers: dict[str, str],
    settings: SecuritySettings,
    store: AdminSessionStore
) -> bool:
    return store.is_valid(
        session_token_from_headers(headers, settings)
    )


def check_admin_request(
    method: str,
    headers: dict[str, str],
    settings: SecuritySettings,
    store: AdminSessionStore
) -> str | None:
    """Return None if allowed, otherwise an error message."""

    # 1. API clients: X-Admin-Key header (CSRF-safe by nature)
    if admin_key_matches(_header(headers, "x-admin-key"), settings):
        return None

    # 2. Browser admin UI: session cookie
    if has_admin_session(headers, settings, store):

        if (
            method.upper() in UNSAFE_METHODS
            and _header(headers, ADMIN_REQUEST_HEADER) != "1"
        ):
            return "Missing admin request header."

        return None

    return "Admin authentication required."


def is_direct_local_request(
    client_host: str | None,
    headers: dict[str, str]
) -> bool:

    if client_host not in LOOPBACK_HOSTS:
        return False

    # cloudflared connects from localhost, so requests
    # carrying proxy/tunnel headers are not "local"
    return not any(name in headers for name in PROXY_HEADERS)


def ai_access_allowed(
    client_host: str | None,
    headers: dict[str, str],
    settings: SecuritySettings,
    store: AdminSessionStore
) -> bool:

    if is_direct_local_request(client_host, headers):
        return True

    # Temporary team testing through Cloudflare Tunnel (opt-in).
    # Rate limit and concurrency limit still apply afterwards.
    if (
        settings.allow_tunnel_ai
        and tunnel_client_ip(client_host, headers) is not None
    ):
        return True

    return (
        settings.admin_enabled
        and has_admin_session(headers, settings, store)
    )


# ------------------------------------------
# FastAPI dependency for admin routes
# ------------------------------------------

def get_security(request: Request) -> tuple[SecuritySettings, AdminSessionStore]:
    return (
        request.app.state.security_settings,
        request.app.state.admin_sessions,
    )


def require_admin(request: Request) -> None:
    """
    Route-level admin check (defence in depth; the middleware
    already ran the same check before the body was read).
    """
    settings, store = get_security(request)

    if not settings.admin_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API key is not configured."
        )

    error = check_admin_request(
        request.method,
        scope_headers(request.scope),
        settings,
        store
    )

    if error is not None:
        raise HTTPException(status_code=403, detail=error)


# ------------------------------------------
# Pre-body access gate
# ------------------------------------------

def review_error_response(
    scope: Scope,
    receive: Receive,
    message: str,
    status_code: int,
    retry_after: int | None = None
):
    headers = {"Cache-Control": "no-store"}

    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    return templates.TemplateResponse(
        request=Request(scope, receive),
        name="review.html",
        context={"error": message, "agreement_text": ""},
        status_code=status_code,
        headers=headers
    )


AI_BUSY_MESSAGE = (
    "The AI analyser is busy with another agreement. "
    "Please try again in a minute."
)

AI_BUSY_RETRY_SECONDS = 30

class AccessControlMiddleware:

    def __init__(
        self,
        app: ASGIApp,
        settings: SecuritySettings,
        store: AdminSessionStore,
        rate_limiter: RateLimiter | None = None,
        inference_limiter: ConcurrencyLimiter | None = None
    ):
        self.app = app
        self.settings = settings
        self.store = store
        self.rate_limiter = rate_limiter
        self.inference_limiter = inference_limiter
        self._proxy_warning_logged = False

    def _warn_if_proxy_rewritten(
        self,
        client_host: str | None,
        headers: dict[str, str]
    ) -> None:
        """
        Diagnostic only (never grants access). With uvicorn's
        default --proxy-headers, tunnel requests reach the app with
        the visitor's IP as the peer instead of 127.0.0.1, so
        TOR_LAW_ALLOW_TUNNEL_AI cannot recognise them.
        """
        if (
            self._proxy_warning_logged
            or not self.settings.allow_tunnel_ai
            or not headers.get("cf-ray")
            or client_host in LOOPBACK_HOSTS
        ):
            return

        self._proxy_warning_logged = True
        logger.warning(
            "Tunnel request seen from a non-loopback peer while "
            "TOR_LAW_ALLOW_TUNNEL_AI=1. Uvicorn proxy headers are "
            "probably enabled; start with scripts/run_server.py or "
            "add --no-proxy-headers."
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send
    ) -> None:

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        headers = scope_headers(scope)

        # Legal Sources API: authenticate before the upload is read
        if self.settings.admin_enabled and (
            path == ADMIN_API_PREFIX
            or path.startswith(ADMIN_API_PREFIX + "/")
        ):
            error = check_admin_request(
                scope.get("method", "GET"),
                headers,
                self.settings,
                self.store
            )

            if error is not None:
                response = JSONResponse(
                    {"detail": error},
                    status_code=403,
                    headers={"Cache-Control": "no-store"}
                )
                await response(scope, receive, send)
                return

        # AI endpoints: access check, then resource protection
        if path in AI_PATHS:

            client = scope.get("client")
            client_host = client[0] if client else None

            # 1. Access restriction always runs first
            if not ai_access_allowed(
                client_host, headers, self.settings, self.store
            ):
                self._warn_if_proxy_rewritten(client_host, headers)

                response = review_error_response(
                    scope, receive,
                    "AI analysis is temporarily unavailable "
                    "on the public site while resource "
                    "protections are being added. Text "
                    "extraction and preview still work.",
                    status_code=503
                )
                await response(scope, receive, send)
                return

            # 2. Fast reject while busy (before the upload is read
            #    and without using the client's rate budget). The
            #    route re-checks authoritatively when it acquires.
            if (
                self.inference_limiter is not None
                and self.inference_limiter.busy
            ):
                response = review_error_response(
                    scope, receive, AI_BUSY_MESSAGE,
                    status_code=503,
                    retry_after=AI_BUSY_RETRY_SECONDS
                )
                await response(scope, receive, send)
                return

            # 3. Per-client rate limit
            if self.rate_limiter is not None:
                allowed, retry_after = self.rate_limiter.hit(
                    client_key(client_host, headers)
                )

                if not allowed:
                    minutes = max(1, round(retry_after / 60))
                    response = review_error_response(
                        scope, receive,
                        "Too many AI analysis requests. Please "
                        f"wait about {minutes} minute(s) and "
                        "try again.",
                        status_code=429,
                        retry_after=retry_after
                    )
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)
