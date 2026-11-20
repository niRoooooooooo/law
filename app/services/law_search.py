
import re
from typing import Literal

from app.services.legal_repository import LegalRepository


SearchMode = Literal["demo", "verified"]


def search_legal_sections(
    query: str,
    jurisdiction: str,
    agreement_type: str,
    mode: SearchMode = "verified",
    limit: int = 5,
    repository: LegalRepository | None = None,
) -> list[dict]:

    if mode not in ("demo", "verified"):
        raise ValueError("Invalid search mode.")

    if not query.strip():
        raise ValueError("Search query cannot be empty.")

    if limit < 1:
        raise ValueError("Limit must be at least 1.")

    repo = repository or LegalRepository()

    search_terms = set(
        re.findall(r"\w+", query.casefold())
    )

    results = []

    for document in repo.list_documents():

        # Match country and agreement category
        if document.jurisdiction != jurisdiction:
            continue

        if document.agreement_type != agreement_type:
            continue

        # Keep fictional and verified laws separate
        if mode == "demo":
            if not document.is_demo:
                continue
        else:
            if document.is_demo:
                continue

            if document.verification_status != "lawyer_verified":
                continue

        data = repo.get_document(
            document.document_id
        )

        for section in data["sections"]:

            heading = section["heading"]
            content = section["content"]

            searchable_text = (
                heading + " " + content
            ).casefold()

            matched_terms = [
                term for term in search_terms
                if term in searchable_text
            ]

            if not matched_terms:
                continue

            # Rank more relevant sections higher
            score = len(matched_terms)

            if query.casefold() in heading.casefold():
                score += 5

            elif query.casefold() in content.casefold():
                score += 3

            results.append({
                "document_id": document.document_id,
                "version": document.version,
                "document_title": document.title,
                "filename": document.original_filename,
                "jurisdiction": document.jurisdiction,
                "agreement_type": document.agreement_type,
                "verification_status": document.verification_status,
                "is_demo": document.is_demo,
                "section_heading": heading,
                "section_text": content,
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "score": score,
            })

    results.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return results[:limit]
