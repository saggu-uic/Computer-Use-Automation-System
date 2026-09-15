from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# USD per million tokens (input, output). Anthropic first-party rates from the Claude API pricing
# table (cached 2026-06-24); Gemini free tier is $0. Used only for the per-run cost cap and cost table.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return round(input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out, 6)


@dataclass
class TurnContext:
    goal: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    extracted: dict[str, str]
    step: int
    max_steps: int
    last_result: str
    history: list[str]
    screen: str
    snapshot: Any = None  # structured, unmasked; only the scripted planner may use it


@dataclass
class PlannerDecision:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class Planner(Protocol):
    provider: str
    model: str

    async def next_action(self, ctx: TurnContext) -> PlannerDecision: ...

    async def aclose(self) -> None: ...
