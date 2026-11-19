
from typing import Literal
from pydantic import BaseModel, Field


class ExtractedClause(BaseModel):
    title: str
    exact_text: str


class IntakeResult(BaseModel):
    agreement_type: Literal[
        "house_rent",
        "employment",
        "service",
        "unknown"
    ]

    language: Literal["bn", "en", "mixed", "unknown"]

    parties: list[str] = Field(default_factory=list)

    user_role: str = "unknown"

    clauses: list[ExtractedClause] = Field(
        default_factory=list
    )

    clarification_questions: list[str] = Field(
        default_factory=list
    )
