from __future__ import annotations

from pathlib import Path

from rote.agent.planners.base import Planner, PlannerDecision, TurnContext


def create_planner(kind: str, *, provider: str | None = None, model: str | None = None, script: Path | None = None) -> Planner:
    if kind == "scripted":
        from rote.agent.planners.scripted import ScriptedPlanner

        if script is None:
            raise ValueError("--planner scripted needs --script")
        return ScriptedPlanner(script)
    provider = provider or "anthropic"
    if provider == "anthropic":
        from rote.agent.planners.anthropic_planner import AnthropicPlanner

        return AnthropicPlanner(model=model)
    if provider == "gemini":
        from rote.agent.planners.gemini_planner import GeminiPlanner

        return GeminiPlanner(model=model)
    raise ValueError(f"unknown provider {provider!r}")


__all__ = ["Planner", "PlannerDecision", "TurnContext", "create_planner"]
