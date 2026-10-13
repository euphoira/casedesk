from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["refund", "coupon", "request_info", "escalate"]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=12, max_length=3000)


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classification: Literal["billing", "shipping", "account", "other"]
    draft: str = Field(min_length=10, max_length=5000)
    confidence: Literal["low", "medium", "high"]
    citations: list[Citation] = Field(max_length=6)
    action: Action
    limitations: list[str] = Field(default_factory=list, max_length=6)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class Rejection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket_revision: int = Field(ge=1)


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    status: Literal["open", "escalated", "closed"]


class CreateTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    subject: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=10, max_length=4000)
    category: Literal["billing", "shipping", "account", "other"]
    context_id: str = Field(min_length=1, max_length=80)
