"""Inputs detected from a plain-English goal.

Code, not the model, finds value-like text in the goal and swaps it for placeholders before
anything reaches the LLM. The compiler later names each placeholder after the field it filled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

QUOTED = re.compile(r"[\"“']([^\"”']{2,})[\"”']")
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
DATE = re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b")
DIGITS = re.compile(r"(?<![\w$.,/-])\d{3,}(?![\w.,/-]*\d)")

KIND_TYPES = {"digits": "string", "money": "money", "date": "date", "quoted": "string"}


@dataclass
class InputBinding:
    placeholder: str
    value: str
    kind: str  # digits | money | date | quoted | explicit
    explicit_name: str | None = None
    explicit_type: str | None = None

    def describe(self) -> str:
        label = self.explicit_name or self.placeholder
        return f"{label} = {self.value} ({self.kind})"


def detect_inputs(goal: str) -> tuple[str, list[InputBinding]]:
    """Returns the templated goal and the detected bindings (values stay in memory only)."""
    bindings: list[InputBinding] = []
    text = goal

    def take(pattern: re.Pattern, kind: str, group: int = 0) -> None:
        nonlocal text

        def repl(match: re.Match) -> str:
            value = match.group(group)
            placeholder = f"value_{len(bindings) + 1}"
            bindings.append(InputBinding(placeholder=placeholder, value=value.strip(), kind=kind))
            return "{{" + placeholder + "}}"

        text = pattern.sub(repl, text)

    take(QUOTED, "quoted", 1)
    take(MONEY, "money")
    take(DATE, "date")
    take(DIGITS, "digits")
    return text, bindings


def explicit_inputs(goal: str, specs: list[str]) -> tuple[str, list[InputBinding]]:
    """`--input name=value[:type]`. Occurrences of the value in the goal become {{name}}."""
    bindings: list[InputBinding] = []
    text = goal
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--input must be name=value[:type], got {spec!r}")
        name, rest = spec.split("=", 1)
        value, _, type_ = rest.partition(":")
        name = name.strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise ValueError(f"input name {name!r} must be snake_case")
        bindings.append(InputBinding(placeholder=name, value=value, kind="explicit", explicit_name=name, explicit_type=type_ or "string"))
        text = re.sub(rf"(?<![\w]){re.escape(value)}(?![\w])", "{{" + name + "}}", text)
    return text, bindings


def build_bindings(goal: str, specs: list[str], detect: bool = True) -> tuple[str, list[InputBinding]]:
    templated, explicit = explicit_inputs(goal, specs)
    if not detect:
        return templated, explicit
    detected_text, detected = detect_inputs(templated)
    # detected placeholders must not collide with explicit names
    return detected_text, explicit + detected
