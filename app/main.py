
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, SecuritySettings
from app.routes import auth, pages, reviews, documents, intake, legal_sources
from app.security import AccessControlMiddleware, AdminSessionStore
from app.services.ai_limits import ConcurrencyLimiter, RateLimiter


def create_app(settings: SecuritySettings | None = None) -> FastAPI:

    if settings is None:
        settings = SecuritySettings.from_env()

    # Fail closed: refuse to start with unsafe settings
    settings.validate()

    if settings.allow_tunnel_ai:
        logging.getLogger(__name__).warning(
            "TOR_LAW_ALLOW_TUNNEL_AI=1: anyone with the tunnel URL "
            "can run AI analysis (rate/concurrency limits apply). "
            "Temporary team-testing mode only."
        )

    # API docs only in debug mode
    docs_enabled = settings.debug

    # Initialize FastAPI
    app = FastAPI(
        title="Tor Lawyer",
        description="AI Agreement Review Platform for Bangladesh",
        version="1.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    sessions = AdminSessionStore(
        lifetime_seconds=settings.admin_session_minutes * 60
    )

    # AI resource protection (P0B.1). One shared inference slot
    # pool for the local Ollama server; future agents should reuse
    # app.state.ai_inference_limiter rather than create their own.
    inference_limiter = ConcurrencyLimiter(
        settings.ai_max_concurrent, name="ollama"
    )
    ai_rate_limiter = RateLimiter(
        settings.ai_rate_limit,
        settings.ai_rate_window_seconds,
    )

    app.state.security_settings = settings
    app.state.admin_sessions = sessions
    app.state.ai_inference_limiter = inference_limiter
    app.state.ai_rate_limiter = ai_rate_limiter

    # Admin API + AI access checks and AI limits, before any
    # body is parsed
    app.add_middleware(
        AccessControlMiddleware,
        settings=settings,
        store=sessions,
        rate_limiter=ai_rate_limiter,
        inference_limiter=inference_limiter,
    )

    # Connect CSS and static files
    app.mount(
        "/static",
        StaticFiles(
            directory=str(BASE_DIR / "static")
        ),
        name="static"
    )

    # Register all routers (only once)
    app.include_router(pages.router)
    app.include_router(reviews.router)
    app.include_router(documents.router)
    app.include_router(intake.router)

    # Legal Sources admin: login page, admin page and API.
    # Not registered at all unless explicitly enabled.
    if settings.admin_enabled:
        app.include_router(auth.router)
        app.include_router(legal_sources.router)

    return app


app = create_app()
