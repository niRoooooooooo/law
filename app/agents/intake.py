

import re

from app.schemas.intake import ExtractedClause
from app.services.llm_client import analyze_with_qwen


# ==========================================
# FUNCTION 1: DETECT DOCUMENT LANGUAGE
# ==========================================

def detect_language(text: str) -> str:

    bangla_count = sum(
        1 for char in text
        if "\u0980" <= char <= "\u09FF"
        and char.isalpha()
    )

    english_count = sum(
        1 for char in text
        if "a" <= char.lower() <= "z"
    )

    if bangla_count >= 10 and english_count >= 10:
        return "mixed"

    if bangla_count >= 10:
        return "bn"

    if english_count >= 10:
        return "en"

    return "unknown"


# ==========================================
# FUNCTION 2: EXTRACT NUMBERED CLAUSES
# ==========================================

def extract_numbered_clauses(text: str):

    # \r? : in (?m) mode "$" only matches before "\n", so CRLF
    # (Windows / browser textarea) lines need the "\r" allowed
    pattern = re.compile(
        r"(?m)^[ \t]*(\d{1,2})\.[ \t]+"
        r"([^\r\n]{3,100})[ \t]*\r?$"
    )

    matches = list(pattern.finditer(text))
    clauses = []

    for index, match in enumerate(matches):

        title = match.group(2).strip()

        # Find where the current clause ends
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            end = len(text)

        # Extract the clause body
        body = text[match.end():end].strip()

        # Exclude footer in our test document
        body = re.split(
            r"(?im)^\s*TEST NOTE:",
            body,
            maxsplit=1
        )[0].strip()

        # Normalize whitespace
        body = " ".join(body.split())

        if not body:
            continue

        # Limit the displayed quotation
        if len(body) > 400:
            body = body[:400].rsplit(" ", 1)[0]

        clauses.append(
            ExtractedClause(
                title=title,
                exact_text=body
            )
        )

    return clauses


# ==========================================
# FUNCTION 3: RUN INTAKE AGENT
# ==========================================

async def run_intake_agent(
    agreement_text: str,
    agreement_type_hint: str = "unknown"
):

    # STEP 1: Send agreement to Qwen
    result = await analyze_with_qwen(
        agreement_text,
        agreement_type_hint
    )

    print(
        f"Qwen returned {len(result.clauses)} clauses."
    )

    # STEP 2: Verify AI-extracted quotations
    source = " ".join(
        agreement_text.split()
    ).casefold()

    matching_clauses = []

    for clause in result.clauses:

        quote = " ".join(
            clause.exact_text.split()
        ).casefold()

        if quote and quote in source:
            matching_clauses.append(clause)

    # STEP 3: Extract numbered clauses directly
    numbered_clauses = extract_numbered_clauses(
        agreement_text
    )

    if len(numbered_clauses) >= 2:
        result.clauses = numbered_clauses
        print("Used numbered-section extraction.")

    else:
        result.clauses = matching_clauses
        print("Used verified Qwen quotations.")

    # STEP 4: Detect document language
    result.language = detect_language(
        agreement_text
    )

    # STEP 5: Generate clarification questions
    questions = list(result.clarification_questions)

    # Ask about the user's role
    if result.user_role == "unknown":

        role_question = (
            "Which party do you represent "
            "in this agreement?"
        )

        if not any(
            "represent" in q.lower()
            or "your role" in q.lower()
            for q in questions
        ):
            questions.insert(0, role_question)

    # Check each clause for missing information
    for clause in result.clauses:

        if re.search(
            r"\b("
            r"not specified|"
            r"not included|"
            r"not stated|"
            r"does not specify|"
            r"have not been included|"
            r"has not been included|"
            r"missing"
            r")\b",
            clause.exact_text,
            re.IGNORECASE
        ):

            question = (
                f"Could you clarify the missing "
                f"details in the '{clause.title}' clause?"
            )

            if question not in questions:
                questions.append(question)

    result.clarification_questions = questions

    # STEP 6: Print processing summary
    print(
        f"Intake Agent: {len(result.clauses)} "
        f"clauses extracted."
    )

    print(
        f"Detected language: {result.language}"
    )

    print(
        f"Clarification questions: "
        f"{len(result.clarification_questions)}"
    )

    # STEP 7: Return extracted information
    return result
