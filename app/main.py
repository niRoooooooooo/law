
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.routes import pages, reviews, documents


app = FastAPI(
    title="Tor Lawyer",
    description="AI Agreement Review Platform for Bangladesh",
    version="1.0.0"
)


# Connect CSS files
app.mount(
    "/static",
    StaticFiles(
        directory=str(BASE_DIR / "static")
    ),
    name="static"
)


# Connect HTML pages
app.include_router(pages.router)

# Connect text agreement submissions
app.include_router(reviews.router)

# Connect document upload submissions
app.include_router(documents.router)
