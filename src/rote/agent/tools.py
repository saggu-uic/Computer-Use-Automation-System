"""The fixed tool menu the LLM may use. Provider-neutral JSON schemas."""

from __future__ import annotations

from typing import Any

WHY = {"type": "string", "description": "One short, specific sentence: why this action moves toward the goal."}
REF = {"type": "string", "description": "Element reference from the CURRENT screen, for example e12."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "click",
        "description": "Click a button, link, or other control on the current screen.",
        "parameters": {"type": "object", "properties": {"ref": REF, "why": WHY}, "required": ["ref", "why"], "additionalProperties": False},
    },
    {
        "name": "type_text",
        "description": "Type into a text field (replaces its content). Use {{placeholders}} for goal inputs; never invent values.",
        "parameters": {
            "type": "object",
            "properties": {"ref": REF, "text": {"type": "string", "description": "Text or {{placeholder}} to type."}, "why": WHY},
            "required": ["ref", "text", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option in a drop-down by its visible label.",
        "parameters": {
            "type": "object",
            "properties": {"ref": REF, "option": {"type": "string"}, "why": WHY},
            "required": ["ref", "option", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_checkbox",
        "description": "Check or uncheck a checkbox.",
        "parameters": {
            "type": "object",
            "properties": {"ref": REF, "checked": {"type": "boolean"}, "why": WHY},
            "required": ["ref", "checked", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "press_key",
        "description": "Press Enter, Tab, or Escape (optionally in a specific field).",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": ["Enter", "Tab", "Escape"]}, "ref": REF, "why": WHY},
            "required": ["key", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "accept_dialog",
        "description": "Accept (OK) the open browser dialog.",
        "parameters": {"type": "object", "properties": {"why": WHY}, "required": ["why"], "additionalProperties": False},
    },
    {
        "name": "dismiss_dialog",
        "description": "Dismiss (Cancel) the open browser dialog.",
        "parameters": {"type": "object", "properties": {"why": WHY}, "required": ["why"], "additionalProperties": False},
    },
    {
        "name": "extract",
        "description": (
            "Read data for the caller. For a single value target a table cell or the value next to a label "
            "(the ref shown on that value); for a list target a whole table (ref of the TABLE). "
            "Rote reads the real value; you only choose where it is."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": REF,
                "output_name": {"type": "string", "description": "snake_case name, e.g. savings_balance"},
                "value_type": {"type": "string", "enum": ["string", "money", "date", "integer", "table"]},
                "why": WHY,
            },
            "required": ["ref", "output_name", "value_type", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait",
        "description": "Wait for a visibly loading screen (at most 5 seconds).",
        "parameters": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "minimum": 0.5, "maximum": 5}, "why": WHY},
            "required": ["seconds", "why"],
            "additionalProperties": False,
        },
    },
    {
        "name": "done",
        "description": "The goal is met and requested data has been extracted.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "capability_name": {"type": "string", "description": "snake_case name for this reusable task, e.g. get_savings_balance"},
            },
            "required": ["summary", "capability_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cannot_complete",
        "description": "The application offers no way to achieve this goal, or it would need a forbidden action.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"], "additionalProperties": False},
    },
    {
        "name": "ask_human",
        "description": "You are unsure or blocked; a human operator will look at the live session.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "question": {"type": "string"}},
            "required": ["reason", "question"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
REF_TOOLS = {"click", "type_text", "select_option", "set_checkbox", "extract"}
_BY_NAME = {t["name"]: t for t in TOOLS}


def validate_args(name: str, args: dict[str, Any]) -> str | None:
    tool = _BY_NAME.get(name)
    if tool is None:
        return f"unknown tool {name!r}"
    schema = tool["parameters"]
    for key in schema["required"]:
        if key not in args or args[key] in (None, ""):
            return f"{name} needs {key!r}"
    for key, value in args.items():
        prop = schema["properties"].get(key)
        if prop is None:
            return f"{name} does not accept {key!r}"
        if "enum" in prop and value not in prop["enum"]:
            return f"{key} must be one of {prop['enum']}"
        if prop.get("type") == "boolean" and not isinstance(value, bool):
            return f"{key} must be true or false"
    return None
