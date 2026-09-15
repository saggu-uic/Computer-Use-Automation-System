"""Gemini planner (google-genai). Same tool menu and prompts; function calling constrained to one call."""

from __future__ import annotations

import copy
import os
from typing import Any

from rote.agent.planners.base import PlannerDecision, TurnContext, estimate_cost
from rote.agent.prompts import SYSTEM_PROMPT, render_turn
from rote.agent.tools import TOOLS
from rote.config import ENV_GEMINI_KEY

DEFAULT_MODEL = "gemini-2.5-flash"


def _strip(schema: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(schema)
    schema.pop("additionalProperties", None)
    return schema


class GeminiPlanner:
    provider = "gemini"

    def __init__(self, model: str | None = None) -> None:
        from google import genai
        from google.genai import types

        key = os.environ.get(ENV_GEMINI_KEY) or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(f"live discovery with Gemini needs {ENV_GEMINI_KEY} (or use --planner scripted)")
        self._types = types
        self.client = genai.Client(api_key=key)
        self.model = model or DEFAULT_MODEL
        declarations = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=_strip(t["parameters"]))
            for t in TOOLS
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
            temperature=0,
        )

    async def next_action(self, ctx: TurnContext) -> PlannerDecision:
        try:
            response = await self.client.aio.models.generate_content(model=self.model, contents=render_turn(ctx), config=self.config)
        except Exception as exc:  # noqa: BLE001 - surface provider errors as an invalid decision
            message = str(exc)
            if "API key" in message or "PERMISSION_DENIED" in message or "401" in message:
                raise RuntimeError(f"Gemini API refused the request: {message[:200]}") from exc
            return PlannerDecision("", error=f"Gemini API error: {message[:200]}")
        usage = response.usage_metadata
        input_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        output_tokens = ((getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)) if usage else 0
        cost = estimate_cost(self.model, input_tokens, output_tokens)
        calls = response.function_calls or []
        if not calls:
            return PlannerDecision("", input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost, error="no function call returned")
        call = calls[0]
        args = dict(call.args or {})
        why = args.get("why") or args.get("reason") or args.get("summary") or ""
        return PlannerDecision(call.name, args, why=why, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)

    async def aclose(self) -> None:
        return None
