"""Product profile: app-wide knowledge written once per vendor product."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rote.models.capability import Screen, Step, Target
from rote.models.common import DataClass, Strict


class Entry(Strict):
    origin: str
    home_path: str = "/"
    home_screen: str


class Session(Strict):
    login_steps: list[Step]


class FieldRule(Strict):
    """How to classify data on screen.

    match keys:
      table + column          a column in a data table
      heading_contains + part the part of a heading after the member number
      section                 the value next to a row label (e.g. MEMBER NOTES)
    """

    match: dict[str, str]
    part: Literal["after_member_number"] | None = None
    classification: DataClass


class InputHint(Strict):
    """Classification and description for inputs named after a field in this product."""

    classification: DataClass = DataClass.internal
    description: str = ""


class ProductProfile(Strict):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["product_profile"] = "product_profile"
    product: str
    display_name: str
    version: str
    surface: Literal["web"] = "web"
    capability_prefix: str
    entry: Entry
    session: Session
    global_screens: dict[str, Screen]
    screen_catalog: dict[str, Screen]
    test_inputs: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    input_hints: dict[str, InputHint] = Field(default_factory=dict)
    shared_targets: dict[str, Target] = Field(default_factory=dict)
    field_classification: list[FieldRule] = Field(default_factory=list)
