
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Form,
    File,
    UploadFile
)

from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool

from app.config import templates
from app.agents.intake import run_intake_agent
from app.services.document_parser import extract_document_text
from app.services.ai_limits import LimiterBusy
from app.security import AI_BUSY_MESSAGE, AI_BUSY_RETRY_SECONDS


router = APIRouter()
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 3 * 1024 * 1024
MAX_TEXT_LENGTH = 6000

VALID_TYPES = {
    "house_rent",
    "employment",
    "service"
}


def show_error(request, message):
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "error": message,
            "agreement_text": ""
        },
        status_code=400
    )


@router.post(
    "/intake/analyze",
    response_class=HTMLResponse
)
async def analyze_agreement(
    request: Request,
    agreement_type: str = Form("house_rent"),
    agreement_text: str = Form(""),
    document: UploadFile | None = File(None)
):
    # Receive pasted agreement text
    text = agreement_text.strip()

    if agreement_type not in VALID_TYPES:
        return show_error(
            request,
            "Invalid agreement type."
        )

    # If a document is uploaded, extract its text
    if document is not None and document.filename:

        extension = Path(
            document.filename
        ).suffix.lower()

        if extension not in {".pdf", ".docx"}:
            await document.close()
            return show_error(
                request,
                "Only PDF and DOCX files are supported."
            )

        try:
            file_bytes = await document.read(
                MAX_FILE_SIZE + 1
            )
        finally:
            await document.close()

        if not file_bytes:
            return show_error(
                request,
                "The uploaded file is empty."
            )

        if len(file_bytes) > MAX_FILE_SIZE:
            return show_error(
                request,
                "Maximum file size is 3 MB."
            )

        try:
            text = await run_in_threadpool(
                extract_document_text,
                file_bytes,
                extension
            )

        except ValueError as error:
            return show_error(request, str(error))

    # Check agreement text
    if not text:
        return show_error(
            request,
            "Please paste or upload an agreement."
        )

    if len(text) > MAX_TEXT_LENGTH:
        return show_error(
            request,
            "AI analysis currently supports "
            "up to 6,000 characters."
        )

    # Execute our Intake Agent inside the shared inference slot
    # (released on success, error or cancellation)
    limiter = request.app.state.ai_inference_limiter

    try:
        async with limiter.slot():
            analysis = await run_intake_agent(
                text,
                agreement_type
            )

    except LimiterBusy:
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "error": AI_BUSY_MESSAGE,
                "agreement_text": ""
            },
            status_code=503,
            headers={
                "Retry-After": str(AI_BUSY_RETRY_SECONDS),
                "Cache-Control": "no-store"
            }
        )

    except Exception:
        logger.exception("Intake Agent failed")

        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={
                "error": (
                    "AI analysis failed. Check Ollama, "
                    "your model installation and "
                    "the FastAPI terminal."
                ),
                "agreement_text": ""
            },
            status_code=503
        )

    # Return results to HTML
    return templates.TemplateResponse(
        request=request,
        name="intake_result.html",
        context={
            "analysis": analysis,
            "character_count": len(text)
        }
    )
