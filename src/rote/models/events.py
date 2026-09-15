from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    ts: str
    run_id: str
    seq: int
    actor: str
    type: str
    step: int | str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
