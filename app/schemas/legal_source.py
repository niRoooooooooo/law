
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


AgreementType = Literal[
    "house_rent",
    "employment",
    "service"
]


class LegalSourceCreate(BaseModel):

    title: str = Field(min_length=3, max_length=200)

    jurisdiction: str = Field(
        pattern=r"^[A-Z]{2}$"
    )

    agreement_type: AgreementType

    is_demo: bool = True


class LegalSourceRecord(LegalSourceCreate):

    document_id: str = Field(
        default_factory=lambda: str(uuid4())
    )

    version: int = Field(default=1, ge=1)

    original_filename: str

    stored_path: str

    verification_status: Literal[
        "demo",
        "pending_legal_review",
        "lawyer_verified"
    ] = "demo"

    is_active: bool = True

    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )

    @model_validator(mode="after")
    def validate_status(self):

        if (
            self.is_demo
            and self.verification_status != "demo"
        ):
            raise ValueError(
                "Demo documents must have demo status."
            )

        if (
            not self.is_demo
            and self.verification_status == "demo"
        ):
            raise ValueError(
                "Real documents cannot have demo status."
            )

        return self
