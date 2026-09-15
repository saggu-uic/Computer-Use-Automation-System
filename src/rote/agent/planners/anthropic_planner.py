"""Claude planner. One stateless request per turn: stable cached system prompt + tool menu, fresh turn context."""

from __future__ import annotations

import os
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from rote.agent.planners.base import PlannerDecision, TurnContext, estimate_cost
from rote.agent.prompts import SYSTEM_PROMPT, render_turn
from rote.agent.tools import TOOLS
from rote.config import ENV_ANTHROPIC_KEY

DEFAULT_MODEL = "claude-opus-5"
EFFORT_MODELS = {"claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-opus-4-8"}
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}


class AnthropicPlanner:
    provider = "anthropic"

    def __init__(self, model: str | None = None, effort: str = "low", max_tokens: int = 16000) -> None:
        if not os.environ.get(ENV_ANTHROPIC_KEY):
            raise RuntimeError(f"live discovery with Claude needs {ENV_ANTHROPIC_KEY} (or use --planner scripted)")
        self.client = AsyncAnthropic()
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self.max_tokens = max_tokens
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]
        self.system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

    async def next_action(self, ctx: TurnContext) -> PlannerDecision:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "tools": self.tools,
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "messages": [{"role": "user", "content": render_turn(ctx)}],
        }
        if self.model in EFFORT_MODELS:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            if self.model in FALLBACK_MODELS:
                response = await self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                response = await self.client.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise RuntimeError(f"Claude API refused the request: {exc.status_code} {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            return PlannerDecision("", error=f"Claude API error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            return PlannerDecision("", error=f"Claude API connection error: {exc}")

        usage = response.usage
        input_tokens = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        output_tokens = usage.output_tokens or 0
        cost = estimate_cost(self.model, input_tokens, output_tokens)
        if response.stop_reason == "refusal":
            return PlannerDecision("", input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost, error="the model declined this request")
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            return PlannerDecision("", input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
                                   error=f"no tool call (stop_reason {response.stop_reason})")
        call = calls[0]
        args = dict(call.input or {})
        why = args.get("why") or args.get("reason") or args.get("summary") or ""
        return PlannerDecision(call.name, args, why=why, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    async def aclose(self) -> None:
        await self.client.close()
