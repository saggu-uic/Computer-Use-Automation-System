"""Scripted planner: replays pre-written decisions for tests, the offline demo, and the misled-agent demo.

Scripts select elements by role/name/label/table (never refs), so they survive ref renumbering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rote.agent.planners.base import PlannerDecision, TurnContext


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).rstrip(": ").upper()


def find_ref(snap: Any, find: dict[str, Any]) -> str | None:
    if "field" in find:
        return next((f.ref for f in snap.fields.values() if _norm(f.label) == _norm(find["field"])), None)
    if "cell" in find:
        c = find["cell"]
        for cell in snap.cells.values():
            if _norm(cell.table) == _norm(c["table"]) and _norm(cell.column) == _norm(c["column"]) and all(
                _norm(cell.row.get(_norm(k))) == _norm(v) for k, v in c.get("row", {}).items()
            ):
                return cell.ref
        return None
    if "table" in find:
        return next((t.ref for t in snap.tables.values() if _norm(t.name) == _norm(find["table"])), None)
    for element in snap.elements.values():
        if "role" in find and element.role != find["role"]:
            continue
        if "name" in find and _norm(element.name) != _norm(find["name"]):
            continue
        if "label" in find and _norm(element.inferred_label or element.name) != _norm(find["label"]):
            continue
        if "frame" in find and element.frame != find["frame"]:
            continue
        if "dialog" in find and _norm(find["dialog"]) not in _norm(element.dialog):
            continue
        if "row" in find:
            row = (element.table or {}).get("row", {})
            if not all(_norm(row.get(_norm(k))) == _norm(v) for k, v in find["row"].items()):
                continue
        return element.ref
    return None


class ScriptedPlanner:
    provider = "scripted"

    def __init__(self, script: Path) -> None:
        data = json.loads(Path(script).read_text(encoding="utf-8"))
        self.model = data.get("name", Path(script).stem)
        self.steps: list[dict[str, Any]] = data["steps"]
        self.index = 0
        self.misses = 0

    async def next_action(self, ctx: TurnContext) -> PlannerDecision:
        if self.index >= len(self.steps):
            return PlannerDecision("cannot_complete", {"reason": "script exhausted"}, why="script exhausted")
        spec = self.steps[self.index]
        tool = spec["tool"]
        args = {k: v for k, v in spec.items() if k not in ("tool", "find", "why")}
        why = spec.get("why", "")
        if tool not in ("done", "cannot_complete", "ask_human"):
            args["why"] = why
        if "find" in spec:
            ref = find_ref(ctx.snapshot, spec["find"])
            if ref is None:
                self.misses += 1
                if self.misses < 4:
                    return PlannerDecision("wait", {"seconds": 1, "why": "scripted element not on screen yet"}, why="waiting for the scripted element")
                return PlannerDecision(tool, args, why=why, error=f"scripted element not found: {spec['find']}")
            args["ref"] = ref
        self.misses = 0
        self.index += 1
        return PlannerDecision(tool, args, why=why)

    async def aclose(self) -> None:
        return None
