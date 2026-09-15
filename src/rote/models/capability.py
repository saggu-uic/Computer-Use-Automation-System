"""The capability artifact: a typed, versioned, reviewable description of one flow."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from rote.models.common import DataClass, Risk, Strict

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

LocatorKind = Literal["role", "label", "near_text", "table_cell", "table", "text", "css"]
DURABLE_KINDS = {"role", "label", "near_text", "table_cell", "table", "text"}


class Locator(Strict):
    kind: LocatorKind
    frame: str | None = None
    role: str | None = None
    name: str | None = None
    exact: bool = True
    label: str | None = None
    text: str | None = None
    direction: Literal["left", "right"] | None = None
    table: str | None = None
    column: str | None = None
    row_where: dict[str, str] | None = None
    value: str | None = None
    within_dialog: str | None = None
    fragile: bool = False

    @property
    def durable(self) -> bool:
        return self.kind in DURABLE_KINDS and not self.fragile

    @model_validator(mode="after")
    def _required_fields(self) -> "Locator":
        need = {
            "role": ["role", "name"],
            "label": ["role", "label"],
            "near_text": ["role", "text"],
            "table_cell": ["table", "column", "row_where"],
            "table": ["table"],
            "text": ["text"],
            "css": ["value"],
        }[self.kind]
        missing = [f for f in need if getattr(self, f) in (None, "", {})]
        if missing:
            raise ValueError(f"locator kind {self.kind!r} needs {missing}")
        if self.kind == "css":
            self.fragile = True
        return self

    def describe(self) -> str:
        scope = f" [{self.frame}]" if self.frame else ""
        if self.kind == "role":
            text = f'{self.role} "{self.name}"'
        elif self.kind == "label":
            text = f'{self.role} labelled "{self.label}"'
        elif self.kind == "near_text":
            text = f'{self.role} {self.direction or "right"} of "{self.text}"'
        elif self.kind == "table_cell":
            where = ", ".join(f"{k}={v}" for k, v in (self.row_where or {}).items())
            text = f'{self.column} where {where} in "{self.table}"'
        elif self.kind == "table":
            text = f'table "{self.table}"'
        elif self.kind == "text":
            text = f'text "{self.text}"'
        else:
            text = f"css {self.value}"
        if self.row_where and self.kind != "table_cell":
            text += " in row " + ", ".join(f"{k}={v}" for k, v in self.row_where.items())
        if self.within_dialog:
            text += f' in dialog "{self.within_dialog}"'
        return text + scope


class Target(Strict):
    description: str = ""
    locators: list[Locator] = Field(min_length=1)

    @property
    def durable_count(self) -> int:
        return sum(1 for loc in self.locators if loc.durable)


class RoleRef(Strict):
    role: str
    name: str


class ScreenPredicate(Strict):
    heading: str | None = None
    heading_contains: str | None = None
    text_contains: str | None = None
    dialog_text_contains: str | None = None
    route_matches: str | None = None
    role_exists: RoleRef | None = None
    not_: "ScreenPredicate | None" = Field(default=None, alias="not")
    frame: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ScreenPredicate":
        keys = ["heading", "heading_contains", "text_contains", "dialog_text_contains", "route_matches", "role_exists", "not_"]
        present = [k for k in keys if getattr(self, k) is not None]
        if len(present) != 1:
            raise ValueError(f"a screen predicate needs exactly one condition, got {present or 'none'}")
        return self


ScreenKind = Literal["page", "outcome", "interstitial", "needs_human", "session_lost", "app_error", "transient"]


class Screen(Strict):
    kind: ScreenKind = "page"
    description: str | None = None
    match: list[ScreenPredicate] = Field(min_length=1)
    outcome: str | None = None
    may_follow: list[str] = Field(default_factory=list)
    handler: str | dict[str, str] | None = None
    max_per_run: int | None = None
    escalate: str | None = None
    fail: str | None = None
    proposed: bool = False


class ThenAction(Strict):
    outcome: str | None = None
    fail: str | None = None
    escalate: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "ThenAction":
        if sum(v is not None for v in (self.outcome, self.fail, self.escalate)) != 1:
            raise ValueError("then needs exactly one of outcome, fail, escalate")
        return self


class ExpectCase(Strict):
    screen: str
    then: Literal["continue"] | ThenAction = "continue"


class IfMissing(Strict):
    then: ThenAction


StepAction = Literal[
    "click", "type", "select", "check", "press_key", "accept_dialog", "dismiss_dialog", "extract", "wait"
]


class Step(Strict):
    id: str
    action: StepAction
    target: str | None = None
    value: str | None = None
    checked: bool | None = None
    key: str | None = None
    risk: Risk = "safe"
    verify: Literal["value_matches"] | None = None
    expect: list[ExpectCase] = Field(default_factory=list)
    timeout_ms: int | None = None
    into: str | None = None
    parse: str | None = None
    columns: dict[str, str] | None = None
    if_missing: IfMissing | None = None
    origin: Literal["agent", "author", "human"] = "agent"
    description: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if self.action in {"click", "type", "select", "check", "extract"} and not self.target:
            raise ValueError(f"step {self.id!r}: action {self.action!r} needs a target")
        if self.action in {"type", "select"} and self.value is None:
            raise ValueError(f"step {self.id!r}: action {self.action!r} needs a value")
        if self.action == "extract" and not (self.into and self.parse):
            raise ValueError(f"step {self.id!r}: extract needs into and parse")
        return self


ValueType = Literal["string", "integer", "decimal", "money", "date", "enum", "boolean"]


class InputSpec(Strict):
    type: ValueType = "string"
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    values: list[str] | None = None
    classification: DataClass = DataClass.internal
    description: str = ""
    named_from: str | None = None

    @field_validator("classification")
    @classmethod
    def _no_secret(cls, value: DataClass) -> DataClass:
        if value == DataClass.secret:
            raise ValueError("capability inputs may not be secret; secrets live in the profile as {{secret:...}}")
        return value


class OutputSpec(Strict):
    type: Literal["string", "integer", "decimal", "money", "date", "enum", "boolean", "table"]
    columns: dict[str, str] | None = None
    max_rows: int | None = None
    classification: DataClass = DataClass.internal
    description: str = ""

    @field_validator("classification")
    @classmethod
    def _no_secret(cls, value: DataClass) -> DataClass:
        if value == DataClass.secret:
            raise ValueError("capability outputs may not be secret")
        return value


class OutcomeSpec(Strict):
    code: str
    description: str = ""


class Contract(Strict):
    summary: str
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputSpec] = Field(default_factory=dict)
    outcomes: list[OutcomeSpec] = Field(default_factory=list)
    side_effects: Literal["none", "irreversible"] = "none"
    idempotent: bool = True
    irreversible_steps: list[str] = Field(default_factory=list)


class ProfileRef(Strict):
    version: str
    hash: str


class AppRef(Strict):
    product: str
    product_versions: str = ">=3.0,<4.0"
    surface: Literal["web"] = "web"
    profile: ProfileRef


class StartRef(Strict):
    screen: str


class SuccessCondition(Strict):
    screen: str | None = None
    output_valid: str | None = None


class Success(Strict):
    all: list[SuccessCondition] = Field(default_factory=list)


class Timeouts(Strict):
    step_default_ms: int = 6000
    run_max_ms: int = 90000


class Provenance(Strict):
    authored_by: str | None = None
    discovered_from_run: str | None = None
    goal: str | None = None
    planner: dict[str, str] | None = None
    compiled_at: str | None = None
    validated_by_runs: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    content_hash: str | None = None


class Capability(Strict):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["capability"] = "capability"
    id: str
    version: str
    status: Literal["draft", "validated"] = "draft"
    app: AppRef
    contract: Contract
    start: StartRef
    steps: list[Step] = Field(min_length=1)
    targets: dict[str, Target]
    screens: dict[str, Screen]
    constants: dict[str, str] = Field(default_factory=dict)
    success: Success = Field(default_factory=Success)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("version must be MAJOR.MINOR.PATCH")
        return value

    @model_validator(mode="after")
    def _references(self) -> "Capability":
        errors: list[str] = []
        if self.start.screen not in self.screens:
            errors.append(f"start screen {self.start.screen!r} is not defined in screens")
        codes = {o.code for o in self.contract.outcomes}
        ids = set()
        for step in self.steps:
            if step.id in ids:
                errors.append(f"duplicate step id {step.id!r}")
            ids.add(step.id)
            if step.target and step.target not in self.targets:
                errors.append(f"step {step.id!r} targets unknown target {step.target!r}")
            for case in step.expect:
                if case.screen not in self.screens:
                    errors.append(f"step {step.id!r} expects unknown screen {case.screen!r}")
                if isinstance(case.then, ThenAction) and case.then.outcome and case.then.outcome not in codes:
                    errors.append(f"step {step.id!r} returns undeclared outcome {case.then.outcome!r}")
            if step.if_missing and step.if_missing.then.outcome and step.if_missing.then.outcome not in codes:
                errors.append(f"step {step.id!r} returns undeclared outcome {step.if_missing.then.outcome!r}")
            if step.into and step.into not in self.contract.outputs:
                errors.append(f"step {step.id!r} extracts undeclared output {step.into!r}")
            for name in placeholders(step.value or ""):
                if name not in self.contract.inputs and name not in self.constants:
                    errors.append(f"step {step.id!r} uses unknown placeholder {{{{{name}}}}}")
            if step.risk == "irreversible" and step.id not in self.contract.irreversible_steps:
                errors.append(f"irreversible step {step.id!r} is not declared in contract.irreversible_steps")
        for cond in self.success.all:
            if cond.screen and cond.screen not in self.screens:
                errors.append(f"success condition uses unknown screen {cond.screen!r}")
            if cond.output_valid and cond.output_valid not in self.contract.outputs:
                errors.append(f"success condition uses unknown output {cond.output_valid!r}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def placeholders(text: str) -> list[str]:
    return [m.group(1) for m in PLACEHOLDER.finditer(text) if not m.group(1).startswith("secret")]


ScreenPredicate.model_rebuild()
