
"""
Regression tests for existing features after P0A.2.

Uses real parsers, the real Agent 1 post-processing and a real
LegalRepository in a temporary folder. Only the Ollama call is
mocked; the real data/ folder is never touched.
"""

from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient

import app.agents.intake as intake_agent
from app.config import SecuritySettings
from app.main import create_app
from app.routes import legal_sources as legal_sources_route
from app.schemas.intake import ExtractedClause, IntakeResult
from app.services.law_search import search_legal_sections
from app.services.legal_repository import LegalRepository


ADMIN_KEY = "test-admin-key-not-real-0123456789abcdef"
CSRF = {"X-Admin-Request": "1"}

AGREEMENT = (
    "FICTIONAL HOUSE RENT AGREEMENT\n"
    "1. Parties\n"
    "This agreement is between Landlord A and Tenant B.\n"
    "2. Rent\n"
    "The tenant shall pay rent of 10,000 taka monthly.\n"
    "3. Deposit\n"
    "The security deposit amount is not specified.\n"
)

LAW_TEXT = [
    "Fictional Demo Rent Law",
    "Section 1: Scope",
    "This demo law applies to residential tenancies.",
    "Section 2: Security deposit",
    "A landlord must return the security deposit within 30 days.",
]


def make_docx(paragraphs) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pdf(lines) -> bytes:
    """Build a minimal one-page text PDF without extra libraries."""
    text_ops = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(")
        .replace(")", "\\)") + ") Tj T*"
        for line in lines
    ) + " ET"
    stream = text_ops.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(output)


def make_client(client_host="127.0.0.1", admin_enabled=True) -> TestClient:
    settings = SecuritySettings(
        admin_enabled=admin_enabled,
        admin_key=ADMIN_KEY,
        admin_cookie_secure=True,
    )
    return TestClient(
        create_app(settings),
        base_url="https://testserver",
        client=(client_host, 50000),
        follow_redirects=False,
    )


@pytest.fixture
def fake_qwen(monkeypatch):
    """Replace only the Ollama call; Agent 1 logic stays real."""
    calls = []

    async def fake_analyze(text, hint="unknown"):
        calls.append(hint)
        return IntakeResult(
            agreement_type="house_rent",
            language="bn",  # Agent 1 must override this
            parties=["Landlord A", "Tenant B"],
            user_role="unknown",
            clauses=[
                ExtractedClause(title="Invented", exact_text="not in text"),
            ],
        )

    monkeypatch.setattr(intake_agent, "analyze_with_qwen", fake_analyze)
    return calls


@pytest.fixture
def temp_repository(tmp_path, monkeypatch):
    repo = LegalRepository(data_dir=tmp_path)
    monkeypatch.setattr(legal_sources_route, "repository", repo)
    return repo


# ------------------------------------------
# Document processing
# ------------------------------------------

def test_docx_upload_preview():
    response = make_client(client_host="203.0.113.9").post(
        "/documents/preview",
        data={"agreement_type": "house_rent"},
        files={"document": ("a.docx", make_docx(AGREEMENT.splitlines()),
                            "application/octet-stream")},
    )

    assert response.status_code == 200
    assert "Agreement received successfully" in response.text
    assert "10,000 taka" in response.text


def test_pdf_upload_preview():
    response = make_client(client_host="203.0.113.9").post(
        "/documents/preview",
        data={"agreement_type": "house_rent"},
        files={"document": ("a.pdf", make_pdf(AGREEMENT.splitlines()),
                            "application/pdf")},
    )

    assert response.status_code == 200
    assert "Tenant B" in response.text


@pytest.mark.parametrize(
    "filename, content, message",
    [
        ("a.txt", b"hello", "Only PDF and DOCX files are allowed."),
        ("a.pdf", b"", "The uploaded file is empty."),
        ("a.pdf", b"not a pdf", "Invalid PDF file."),
        ("a.docx", b"not a zip", "Invalid DOCX file."),
    ],
)
def test_upload_validation_unchanged(filename, content, message):
    response = make_client(client_host="203.0.113.9").post(
        "/documents/preview",
        data={"agreement_type": "house_rent"},
        files={"document": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 400
    assert message in response.text


def test_pasted_text_preview():
    response = make_client(client_host="203.0.113.9").post(
        "/reviews/preview",
        data={"agreement_type": "employment", "agreement_text": AGREEMENT},
    )

    assert response.status_code == 200
    assert "Employment" in response.text


# ------------------------------------------
# Agent 1 (Ollama mocked, logic real)
# ------------------------------------------

def test_agent1_text_analysis(fake_qwen):
    response = make_client().post(
        "/intake/analyze",
        data={"agreement_type": "house_rent", "agreement_text": AGREEMENT},
    )

    assert response.status_code == 200
    assert fake_qwen == ["house_rent"]

    page = response.text
    # Numbered-clause extraction replaced the invented clause
    assert "Invented" not in page
    assert "The tenant shall pay rent of 10,000 taka monthly." in page
    # Language detection overrides the model
    assert "<strong>Language:</strong>\n                en" in page
    # Role and missing-information questions added
    assert "Which party do you represent" in page
    assert "missing details in the &#39;Deposit&#39; clause" in page


def test_agent1_document_analysis(fake_qwen):
    response = make_client().post(
        "/intake/analyze",
        data={"agreement_type": "house_rent"},
        files={"document": ("a.docx", make_docx(AGREEMENT.splitlines()),
                            "application/octet-stream")},
    )

    assert response.status_code == 200
    assert "Landlord A" in response.text


def test_agent1_length_limit_unchanged(fake_qwen):
    response = make_client().post(
        "/intake/analyze",
        data={"agreement_type": "house_rent", "agreement_text": "x" * 6001},
    )

    assert response.status_code == 400
    assert fake_qwen == []


def test_agent1_failure_shows_friendly_error(monkeypatch):
    async def broken(*args, **kwargs):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(intake_agent, "analyze_with_qwen", broken)

    response = make_client().post(
        "/intake/analyze",
        data={"agreement_type": "house_rent", "agreement_text": AGREEMENT},
    )

    assert response.status_code == 503
    assert "AI analysis failed" in response.text
    assert "ollama down" not in response.text


# ------------------------------------------
# Legal source management (via admin session)
# ------------------------------------------

def test_legal_source_full_lifecycle(temp_repository):
    client = make_client(client_host="203.0.113.9")
    assert client.post(
        "/admin/login", data={"admin_key": ADMIN_KEY}
    ).status_code == 303

    # Upload
    upload = client.post(
        "/admin/legal-sources/",
        headers=CSRF,
        data={"title": "Fictional Demo Rent Law", "jurisdiction": "BD",
              "agreement_type": "house_rent", "is_demo": "true"},
        files={"file": ("demo_law.docx", make_docx(LAW_TEXT),
                        "application/octet-stream")},
    )
    assert upload.status_code == 200, upload.text
    document = upload.json()["document"]
    document_id = document["document_id"]
    assert document["version"] == 1
    assert document["verification_status"] == "demo"

    # List
    listing = client.get("/admin/legal-sources/").json()
    assert listing["total"] == 1

    # Retrieve sections
    detail = client.get(f"/admin/legal-sources/{document_id}").json()
    headings = [s["heading"] for s in detail["sections"]]
    assert "Section 2: Security deposit" in headings

    # Replace (PDF) -> version 2 becomes current
    replace = client.post(
        f"/admin/legal-sources/{document_id}/replace",
        headers=CSRF,
        files={"file": ("demo_law_v2.pdf", make_pdf(LAW_TEXT),
                        "application/pdf")},
    )
    assert replace.status_code == 200, replace.text
    assert replace.json()["document"]["version"] == 2

    old = client.get(f"/admin/legal-sources/{document_id}?version=1").json()
    assert old["source"]["is_active"] is False

    # Retrieval over the stored index
    results = search_legal_sections(
        "security deposit", "BD", "house_rent",
        mode="demo", repository=temp_repository,
    )
    assert results and results[0]["section_heading"].startswith("Section 2")

    # Verified mode never returns demo sources
    assert search_legal_sections(
        "deposit", "BD", "house_rent",
        mode="verified", repository=temp_repository,
    ) == []


def test_legal_source_validation_unchanged(temp_repository):
    client = make_client()

    bad_signature = client.post(
        "/admin/legal-sources/",
        headers={"X-Admin-Key": ADMIN_KEY},
        data={"title": "Demo", "jurisdiction": "BD",
              "agreement_type": "house_rent"},
        files={"file": ("fake.pdf", b"PK not a pdf", "application/pdf")},
    )
    bad_jurisdiction = client.post(
        "/admin/legal-sources/",
        headers={"X-Admin-Key": ADMIN_KEY},
        data={"title": "Demo", "jurisdiction": "bd",
              "agreement_type": "house_rent"},
        files={"file": ("a.docx", make_docx(LAW_TEXT),
                        "application/octet-stream")},
    )
    missing = client.get(
        "/admin/legal-sources/00000000-0000-4000-8000-000000000000",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert bad_signature.status_code == 400
    assert bad_jurisdiction.status_code == 422
    assert missing.status_code == 404
    assert temp_repository.list_documents() == []
