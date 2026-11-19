
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from app.config import templates

router = APIRouter()

AGREEMENT_TYPES = {
    "house_rent": "House Rent",
    "employment": "Employment",
    "service": "Service / Freelance"
}

MAX_CHARS = 20000


@router.post(
    "/reviews/preview",
    response_class=HTMLResponse
)
def preview_agreement(
    request: Request,
    agreement_type: str = Form("house_rent"),
    agreement_text: str = Form("")
):
    # Remove leading and trailing whitespace
    text = agreement_text.strip()

    # Validate submitted text
    if not text:
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "error": "Please enter agreement text.",
                "agreement_text": agreement_text
            },
            status_code=400
        )

    if len(text) > MAX_CHARS:
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "error": "Agreement text is too long.",
                "agreement_text": agreement_text
            },
            status_code=400
        )

    if agreement_type not in AGREEMENT_TYPES:
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "error": "Invalid agreement type.",
                "agreement_text": agreement_text
            },
            status_code=400
        )

    # Prepare basic agreement information
    character_count = len(text)
    word_count = len(text.split())

    # Only show the beginning of the document
    preview_text = text[:1200]

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "agreement_type": AGREEMENT_TYPES[agreement_type],
            "character_count": character_count,
            "word_count": word_count,
            "preview_text": preview_text
        }
    )
