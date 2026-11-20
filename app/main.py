
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import BASE_DIR
from app.routes import pages, reviews, documents, intake, legal_sources


# Initialize FastAPI
app = FastAPI(
    title="Tor Lawyer",
    description="AI Agreement Review Platform for Bangladesh",
    version="1.0.0"
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


# Phase 4: Legal source management API
app.include_router(legal_sources.router)


# Phase 4: Admin interface
@app.get("/admin/laws", include_in_schema=False)
def admin_laws_page():
    return FileResponse(
        path=BASE_DIR / "templates" / "admin_laws.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"}
    )
