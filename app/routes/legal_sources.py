
import hmac
import os
import re

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import ValidationError

from app.schemas.legal_source import LegalSourceCreate
from app.services.legal_repository import LegalRepository


load_dotenv()

MAX_FILE_SIZE = 15 * 1024 * 1024

repository = LegalRepository()


# Admin authentication
def require_admin(
    x_admin_key: str | None = Header(default=None)
):
    expected_key = os.getenv("TOR_LAW_ADMIN_KEY")

    if not expected_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API key is not configured."
        )

    if not x_admin_key or not hmac.compare_digest(
        x_admin_key,
        expected_key
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key."
        )


router = APIRouter(
    prefix="/admin/legal-sources",
    tags=["Legal Sources"],
    dependencies=[Depends(require_admin)]
)


# Save uploaded file temporarily
def save_temporary_upload(
    file: UploadFile,
    directory: Path
) -> Path:

    original_name = (
        file.filename or "document"
    ).replace("\\", "/").split("/")[-1]

    filename = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        original_name
    )[:150]

    extension = Path(filename).suffix.lower()

    if extension not in {".pdf", ".docx"}:
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX files are supported."
        )

    destination = directory / filename

    total_size = 0

    with destination.open("wb") as output:
        while True:

            chunk = file.file.read(1024 * 1024)

            if not chunk:
                break

            total_size += len(chunk)

            if total_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail="Maximum file size is 15 MB."
                )

            output.write(chunk)

    if total_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty."
        )

    with destination.open("rb") as saved:
        signature = saved.read(5)

    if extension == ".pdf":
        valid = signature.startswith(b"%PDF-")
    else:
        valid = signature.startswith(b"PK")

    if not valid:
        raise HTTPException(
            status_code=400,
            detail="File content does not match its extension."
        )

    return destination


# Upload a new legal document
@router.post("/")
def upload_legal_source(
    title: str = Form(...),
    jurisdiction: str = Form(...),
    agreement_type: Literal[
        "house_rent",
        "employment",
        "service"
    ] = Form(...),
    is_demo: bool = Form(True),
    file: UploadFile = File(...)
):

    try:
        metadata = LegalSourceCreate(
            title=title,
            jurisdiction=jurisdiction,
            agreement_type=agreement_type,
            is_demo=is_demo
        )

    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(
                include_context=False
            )
        ) from error

    with TemporaryDirectory() as temp_dir:

        source_path = save_temporary_upload(
            file,
            Path(temp_dir)
        )

        try:
            record = repository.add_document(
                source_path,
                metadata
            )

        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error)
            ) from error

    return {
        "message": "Legal source uploaded successfully.",
        "document": record.model_dump(mode="json")
    }


# List all current legal documents
@router.get("/")
def list_legal_sources():

    documents = repository.list_documents()

    return {
        "total": len(documents),
        "documents": [
            doc.model_dump(mode="json")
            for doc in documents
        ]
    }


# Get legal document and extracted sections
@router.get("/{document_id}")
def get_legal_source(
    document_id: UUID,
    version: int | None = Query(
        default=None,
        ge=1
    )
):

    try:
        return repository.get_document(
            str(document_id),
            version=version
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Legal source not found."
        )


# Replace existing legal document
@router.post("/{document_id}/replace")
def replace_legal_source(
    document_id: UUID,
    file: UploadFile = File(...)
):

    with TemporaryDirectory() as temp_dir:

        source_path = save_temporary_upload(
            file,
            Path(temp_dir)
        )

        try:
            record = repository.replace_document(
                str(document_id),
                source_path
            )

        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="Legal source not found."
            )

        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error)
            ) from error

    return {
        "message": "New document version activated.",
        "document": record.model_dump(mode="json")
    }
