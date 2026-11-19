
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.routes import pages, reviews, documents, intake


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
