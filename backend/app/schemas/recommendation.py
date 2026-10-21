"""The single Recommendation contract (STEERING section 5).

Every engine (quant, deep, advisor) returns THIS shape. Do not add
engine-specific shapes. If the quant signal and the deep decision
disagree, store both and flag the conflict — never average them.

This module is the single source of truth for the contract. `quant/`
and `deep/` import `Recommendation` from here; they must not redefine it.
"""

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Action(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class Recommendation(BaseModel):
    ticker: str
    as_of: date
    action: Action
    confidence: float = Field(ge=0, le=1)
    horizon_days: int
    rationale: str
    evidence: list[str]  # references into the data snapshot
    source: Literal["quant", "deep", "advisor"]
    model_id: str | None = None  # None for rule-based quant
    prompt_version: str | None = None
    snapshot_id: str
