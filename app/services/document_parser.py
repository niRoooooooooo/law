
from io import BytesIO
from zipfile import ZipFile, is_zipfile

from pypdf import PdfReader
from docx import Document


MAX_PAGES = 20
MAX_CHARACTERS = 20000


def extract_pdf_text(file_bytes):
    # Check if this is really a PDF
    if not file_bytes.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF file.")

    try:
        # Read the PDF from memory
        pdf = PdfReader(BytesIO(file_bytes))

        if pdf.is_encrypted:
            raise ValueError(
                "Password-protected PDFs are not supported."
            )

        if len(pdf.pages) > MAX_PAGES:
            raise ValueError(
                "PDF cannot exceed 20 pages."
            )

        text_parts = []

        # Extract text from each page
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)

        return "\n\n".join(text_parts)

    except ValueError:
        raise

    except Exception:
        raise ValueError(
            "Could not read this PDF document."
        )


def extract_docx_text(file_bytes):
    # DOCX is a ZIP-based file format
    if not is_zipfile(BytesIO(file_bytes)):
        raise ValueError("Invalid DOCX file.")

    # Check archive size before opening
    with ZipFile(BytesIO(file_bytes)) as archive:
        entries = archive.infolist()

        total_size = sum(
            entry.file_size for entry in entries
        )

        if len(entries) > 300 or total_size > 30_000_000:
            raise ValueError(
                "DOCX file is too complex or too large."
            )

        if "word/document.xml" not in archive.namelist():
            raise ValueError("Invalid Word document.")

    try:
        document = Document(BytesIO(file_bytes))

        text_parts = []

        # Extract normal paragraphs
        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                text_parts.append(paragraph.text)

        # Extract text from tables
        for table in document.tables:
            for row in table.rows:
                cells = [
                    cell.text for cell in row.cells
                ]
                text_parts.append(" | ".join(cells))

        return "\n".join(text_parts)

    except Exception:
        raise ValueError(
            "Could not read this Word document."
        )


def extract_document_text(file_bytes, extension):
    # Select parser based on file extension
    if extension == ".pdf":
        text = extract_pdf_text(file_bytes)

    elif extension == ".docx":
        text = extract_docx_text(file_bytes)

    else:
        raise ValueError(
            "Only PDF and DOCX files are supported."
        )

    text = text.strip()

    if not text:
        raise ValueError(
            "No selectable text found. "
            "Scanned documents require OCR."
        )

    if len(text) > MAX_CHARACTERS:
        raise ValueError(
            "Extracted text exceeds 20,000 characters."
        )

    return text
