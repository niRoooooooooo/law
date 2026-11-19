
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.routes import pages, reviews

# Create the FastAPI application
app = FastAPI(
    title="Tor Lawyer",
    description="AI Agreement Review Platform for Bangladesh",
    version="1.0.0"
)

# Serve CSS and other static files
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

# Register routes
app.include_router(pages.router)
app.include_router(reviews.router)
