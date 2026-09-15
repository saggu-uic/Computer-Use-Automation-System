"""Structured, unmasked snapshot of the live screen. Lives in memory only; masked before it goes anywhere."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


class Box(BaseModel):
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


class ElementNode(BaseModel):
    ref: str
    frame: str
    index: int
    role: str | None = None
    name: str | None = None
    tag: str | None = None
    input_type: str | None = None
    inferred_label: str | None = None
    value: str | None = None
    options: list[str] | None = None
    states: list[str] = Field(default_factory=list)
    box: Box | None = None
    dialog: str | None = None
    table: dict[str, Any] | None = None
    form_submit: bool = False
    form_method: str | None = None

    def describe(self) -> str:
        label = self.name or self.inferred_label or ""
        return f'{self.role} "{label}" [{self.frame}]'

    def policy_view(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "form_submit": self.form_submit,
            "form_method": self.form_method,
        }


class CellNode(BaseModel):
    ref: str
    frame: str
    index: int
    column: str
    text: str
    table: str
    row: dict[str, str]
    box: Box | None = None

    role: str = "cell"

    def describe(self) -> str:
        where = ", ".join(f"{k}={v}" for k, v in list(self.row.items())[:1])
        return f'cell {self.column} where {where} in "{self.table}" [{self.frame}]'

    def policy_view(self) -> dict[str, Any]:
        return {"role": "cell", "name": self.column, "form_submit": False, "form_method": None}


class RowNode(BaseModel):
    values: dict[str, str]
    cells: list[CellNode | None]
    controls: list[str] = Field(default_factory=list)


class TableNode(BaseModel):
    ref: str
    frame: str
    index: int
    name: str
    columns: list[str]
    rows: list[RowNode]
    box: Box | None = None
    dialog: str | None = None

    role: str = "table"

    def describe(self) -> str:
        return f'table "{self.name}" [{self.frame}]'

    def policy_view(self) -> dict[str, Any]:
        return {"role": "table", "name": self.name, "form_submit": False, "form_method": None}


class FrameView(BaseModel):
    name: str
    url: str
    title: str = ""
    headings: list[str] = Field(default_factory=list)
    dialogs: list[dict[str, Any]] = Field(default_factory=list)
    layout: list[dict[str, Any]] = Field(default_factory=list)


Node = ElementNode | CellNode | TableNode


class Snapshot(BaseModel):
    frames: list[FrameView]
    elements: dict[str, ElementNode] = Field(default_factory=dict)
    tables: dict[str, TableNode] = Field(default_factory=dict)
    cells: dict[str, CellNode] = Field(default_factory=dict)
    fingerprint: str = ""

    def node(self, ref: str) -> Node | None:
        return self.elements.get(ref) or self.tables.get(ref) or self.cells.get(ref)

    def has_ref(self, ref: str) -> bool:
        return self.node(ref) is not None

    def frame(self, name: str) -> FrameView | None:
        return next((f for f in self.frames if f.name == name), None)

    def dialogs(self) -> list[dict[str, Any]]:
        return [d for f in self.frames for d in f.dialogs]

    def headings(self) -> list[str]:
        return [h for f in self.frames for h in f.headings]

    def find(self, role: str | None = None, name: str | None = None, frame: str | None = None) -> list[ElementNode]:
        def norm(s: str | None) -> str:
            return " ".join((s or "").split()).rstrip(": ").upper()

        return [
            e
            for e in self.elements.values()
            if (role is None or e.role == role)
            and (name is None or norm(e.name) == norm(name) or norm(e.inferred_label) == norm(name))
            and (frame is None or e.frame == frame)
        ]


def fingerprint(frames: list[FrameView], elements: dict[str, ElementNode], tables: dict[str, TableNode]) -> str:
    """Structure only: headings, dialogs, actionable roles/names, tables. Excludes values and volatile text (clocks)."""
    material = {
        "frames": [
            {
                "name": f.name,
                "path": f.url.split("#")[0],
                "headings": f.headings,
                "dialogs": [d.get("title") for d in f.dialogs],
            }
            for f in frames
        ],
        "elements": sorted(f"{e.frame}|{e.role}|{e.name}|{e.inferred_label}|{e.dialog}" for e in elements.values()),
        "tables": sorted(f"{t.frame}|{t.name}|{len(t.rows)}" for t in tables.values()),
    }
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]
