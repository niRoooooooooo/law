"""P0B.1a: numbered-clause extraction with LF and CRLF line endings."""

import pytest
from fastapi.testclient import TestClient

import app.agents.intake as intake_agent
from app.agents.intake import extract_numbered_clauses
from app.config import SecuritySettings
from app.main import create_app
from app.schemas.intake import IntakeResult


AGREEMENT_LF = (
    "FICTIONAL HOUSE RENT AGREEMENT\n"
    "1. Parties\n"
    "This agreement is between Landlord A and Tenant B.\n"
    "2. Rent\n"
    "The tenant shall pay rent of 10,000 taka monthly.\n"
    "3. Deposit\n"
    "The security deposit amount is not specified.\n"
)

EXPECTED = [
    ("Parties", "This agreement is between Landlord A and Tenant B."),
    ("Rent", "The tenant shall pay rent of 10,000 taka monthly."),
    ("Deposit", "The security deposit amount is not specified."),
]


def as_pairs(clauses):
    return [(c.title, c.exact_text) for c in clauses]


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_numbered_clauses_extracted(newline):
    text = AGREEMENT_LF.replace("\n", newline)
    assert as_pairs(extract_numbered_clauses(text)) == EXPECTED


def test_crlf_and_lf_results_identical_for_varied_input():
    text = (
        "  1.  Term   \n  The term is   one year.\n\n"
        "2.\tNotice\nThirty days notice.\nTEST NOTE: footer here\n"
        "10. Misc\n" + "word " * 120 + "\n"
        "11. বাড়ি ভাড়া\nভাড়া মাসিক ১০,০০০ টাকা।\n"
    )
    lf = as_pairs(extract_numbered_clauses(text))
    crlf = as_pairs(extract_numbered_clauses(text.replace("\n", "\r\n")))

    assert lf == crlf
    assert [t for t, _ in lf] == ["Term", "Notice", "Misc", "বাড়ি ভাড়া"]
    # No carriage returns leak into titles or quotations
    assert not any("\r" in t or "\r" in b for t, b in crlf)


def test_quotations_still_exact_text_from_document():
    text = AGREEMENT_LF.replace("\n", "\r\n")
    source = " ".join(text.split())
    for _, body in as_pairs(extract_numbered_clauses(text)):
        assert body in source


def test_browser_textarea_crlf_end_to_end(monkeypatch):
    """Browsers submit textarea text with CRLF; Agent 1 must still
    use numbered-section extraction (Ollama mocked)."""

    async def fake_analyze(text, hint="unknown"):
        assert "\r\n" in text  # original text reaches the model unchanged
        return IntakeResult(agreement_type="house_rent", language="en")

    monkeypatch.setattr(intake_agent, "analyze_with_qwen", fake_analyze)
    client = TestClient(
        create_app(SecuritySettings()),
        base_url="https://testserver",
        client=("127.0.0.1", 50000),
    )

    response = client.post("/intake/analyze", data={
        "agreement_type": "house_rent",
        "agreement_text": AGREEMENT_LF.replace("\n", "\r\n"),
    })

    assert response.status_code == 200
    assert "No matching clauses found" not in response.text
    assert "The tenant shall pay rent of 10,000 taka monthly." in response.text
    assert "missing details in the &#39;Deposit&#39; clause" in response.text
