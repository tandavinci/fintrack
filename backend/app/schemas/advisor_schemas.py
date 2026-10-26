"""Request/response schemas for the advisor chat endpoint (Step 5 / G5).

The advisor is a grounded chat over the user's REAL stored data (holdings +
latest stored quant/deep recommendations). These are the wire shapes only; the
grounding itself lives in ``backend.app.services.advisor_service``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AdvisorTurn(BaseModel):
    """One prior conversation turn, replayed to the model for continuity."""

    role: Literal["user", "assistant"]
    content: str


class AdvisorQuestion(BaseModel):
    """A user's question plus optional prior turns.

    ``question`` is bounded (non-empty, length-capped) so a malformed request
    cannot blow the gateway's 8k num_ctx budget; validation is enforced here and
    re-checked in the router before the service runs.
    """

    question: str = Field(min_length=1, max_length=2000)
    history: list[AdvisorTurn] | None = None


class GroundedOn(BaseModel):
    """Small provenance so the UI can show what the answer was based on."""

    holdings: int
    recommendations: int


class AdvisorAnswer(BaseModel):
    """The advisor's grounded reply plus its provenance counts."""

    answer: str
    grounded_on: GroundedOn
