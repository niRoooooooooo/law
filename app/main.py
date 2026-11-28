
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, SecuritySettings
from app.routes import auth, pages, reviews, documents, intake, legal_sources
from app.security import AccessControlMiddleware, AdminSessionStore


def create_app(settings: SecuritySettings | None = None) -> FastAPI:

    if settings is None:
        settings = SecuritySettings.from_env()

    # Fail closed: refuse to start with unsafe settings
    settings.validate()

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

    app.state.security_settings = settings
    app.state.admin_sessions = sessions

    # Admin API + AI access checks, before any body is parsed
    app.add_middleware(
        AccessControlMiddleware,
        settings=settings,
        store=sessions,
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
