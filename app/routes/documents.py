
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    UploadFile,
    File,
    Form
)

from fastapi.responses import HTMLResponse

from app.config import templates
from app.services.document_parser import (
    extract_document_text
)


router = APIRouter()

MAX_FILE_SIZE = 3 * 1024 * 1024  # 3 MB

AGREEMENT_TYPES = {
    "house_rent": "House Rent",
    "employment": "Employment",
    "service": "Service / Freelance"
}


def show_upload_error(request, message):
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
    "/documents/preview",
    response_class=HTMLResponse
)
async def upload_document(
    request: Request,
    agreement_type: str = Form(...),
    document: UploadFile = File(...)
):

    # Step 1: Validate agreement type
    if agreement_type not in AGREEMENT_TYPES:
        return show_upload_error(
            request,
            "Invalid agreement type."
        )

    # Step 2: Get file extension
    filename = document.filename or ""
    extension = Path(filename).suffix.lower()

    if extension not in [".pdf", ".docx"]:
        return show_upload_error(
            request,
            "Only PDF and DOCX files are allowed."
        )

    # Step 3: Read the uploaded file
    try:
        file_bytes = await document.read(
            MAX_FILE_SIZE + 1
        )
    finally:
        await document.close()

    # Step 4: Check file size
    if not file_bytes:
        return show_upload_error(
            request,
            "The uploaded file is empty."
        )

    if len(file_bytes) > MAX_FILE_SIZE:
        return show_upload_error(
            request,
            "File size must not exceed 3 MB."
        )

    # Step 5: Extract document text
    try:
        extracted_text = extract_document_text(
            file_bytes,
            extension
        )

    except ValueError as error:
        return show_upload_error(
            request,
            str(error)
        )

    # Step 6: Count characters and words
    character_count = len(extracted_text)
    word_count = len(extracted_text.split())

    # Step 7: Create a preview
    preview_text = extracted_text[:1200]

    # Step 8: Send results to HTML
    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "agreement_type": AGREEMENT_TYPES[
                agreement_type
            ],
            "character_count": character_count,
            "word_count": word_count,
            "preview_text": preview_text
        }
    )
