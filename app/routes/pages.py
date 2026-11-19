
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import templates

router = APIRouter()


# Homepage
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "project_name": "Tor Lawyer",
            "version": "1.0"
        }
    )


# Agreement review form
@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "error": None,
            "agreement_text": ""
        }
    )
