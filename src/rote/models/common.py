from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DataClass(str, Enum):
    public = "public"
    internal = "internal"
    pii = "pii"
    financial = "financial"
    secret = "secret"


Risk = Literal["safe", "irreversible"]


class Strict(BaseModel):
    """Base for artifact models: unknown fields are errors, so typos never pass silently."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
