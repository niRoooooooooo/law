
from dataclasses import dataclass, field
from pathlib import Path
import re

from docx import Document
from pypdf import PdfReader


# Maximum file size for our initial MVP
MAX_FILE_SIZE = 15 * 1024 * 1024

# Safety limit for initial PDF processing
MAX_PDF_PAGES = 150

SUPPORTED_FORMATS = {".pdf", ".docx"}


# Detect common legal section headings
SECTION_PATTERN = re.compile(
    r"^(?:"
    r"(?:section|sec\.?)\s+[0-9]+[a-z]?"
    r"|ধারা\s*[০-৯0-9]+"
    r"|[০-৯0-9]+[.)।]\s+\S"
    r")",
    re.IGNORECASE
)


# One extracted legal section
@dataclass
class LegalSection:
    heading: str
    content: str
    page_start: int | None = None
    page_end: int | None = None


# Complete ingestion result
@dataclass
class LegalIngestionResult:
    filename: str
    total_characters: int
    sections: list[LegalSection] = field(
        default_factory=list
    )
    unreadable_pages: list[int] = field(
        default_factory=list
    )


# ----------------------------------------
# FUNCTION 1: READ PDF OR DOCX
# ----------------------------------------

def extract_document_pages(
    file_path: Path
) -> tuple[list[tuple[int | None, str]], list[int]]:

    extension = file_path.suffix.lower()
    pages = []
    unreadable_pages = []

    if extension == ".pdf":

        reader = PdfReader(str(file_path))

        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(
                "PDF exceeds the current page limit."
            )

        for page_number, page in enumerate(
            reader.pages,
            start=1
        ):

            text = page.extract_text() or ""

            if text.strip():
                pages.append(
                    (page_number, text)
                )
            else:
                unreadable_pages.append(
                    page_number
                )

    elif extension == ".docx":

        document = Document(str(file_path))

        paragraphs = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]

        text = "\n".join(paragraphs)

        # DOCX pages cannot be reliably determined
        pages.append((None, text))

    else:
        raise ValueError(
            "Only PDF and DOCX files are supported."
        )

    return pages, unreadable_pages


# ----------------------------------------
# FUNCTION 2: DIVIDE TEXT INTO SECTIONS
# ----------------------------------------

def split_legal_sections(
    pages: list[tuple[int | None, str]]
) -> list[LegalSection]:

    sections = []

    heading = "Document introduction"
    body_lines = []

    start_page = None
    end_page = None

    def save_current_section():

        if body_lines or heading != "Document introduction":

            sections.append(
                LegalSection(
                    heading=heading,
                    content="\n".join(body_lines).strip(),
                    page_start=start_page,
                    page_end=end_page
                )
            )

    for page_number, text in pages:

        for raw_line in text.splitlines():

            line = raw_line.strip()

            if not line:
                continue

            is_heading = (
                len(line) <= 140
                and SECTION_PATTERN.match(line)
            )

            if is_heading:

                save_current_section()

                heading = line
                body_lines = []

                start_page = page_number
                end_page = page_number

            else:

                body_lines.append(line)

                if start_page is None:
                    start_page = page_number

                if page_number is not None:
                    end_page = page_number

    save_current_section()

    return sections


# ----------------------------------------
# FUNCTION 3: MAIN INGESTION FUNCTION
# ----------------------------------------

def ingest_legal_document(
    file_path: str | Path
) -> LegalIngestionResult:

    file_path = Path(file_path)

    if not file_path.is_file():
        raise FileNotFoundError(
            "Legal document was not found."
        )

    if file_path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(
            "Unsupported document format."
        )

    if file_path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(
            "File is larger than 15 MB."
        )

    pages, unreadable_pages = extract_document_pages(
        file_path
    )

    total_characters = sum(
        len(text)
        for _, text in pages
    )

    if total_characters == 0:
        raise ValueError(
            "No readable text found. "
            "The document may require OCR."
        )

    sections = split_legal_sections(pages)

    return LegalIngestionResult(
        filename=file_path.name,
        total_characters=total_characters,
        sections=sections,
        unreadable_pages=unreadable_pages
    )
