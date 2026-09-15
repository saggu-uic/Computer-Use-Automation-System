"""Masking at the model and persistence boundaries.

Real values stay in this object's memory only. Everything that leaves (LLM prompts,
events, snapshots, screenshots, result files) goes through `mask_text` / `redact`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from rote.models.common import DataClass
from rote.models.profile import ProductProfile
from rote.surface.snapshot import ElementNode, Snapshot, TableNode

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
MONEY_RE = re.compile(r"\(?-?\$\s?\d[\d,]*\.\d{2}\)?")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)*\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b")
LONG_DIGITS_RE = re.compile(r"(?<![\w{])\d{6,}(?![\w}])")
SENSITIVE = {DataClass.pii, DataClass.financial, DataClass.secret}


def _norm(text: str | None) -> str:
    return " ".join((text or "").replace(" ", " ").split()).rstrip(": ").upper()


def _sub_ci(text: str, needle: str, replacement: str, word: bool = False) -> str:
    if not needle:
        return text
    pattern = re.escape(needle)
    if word:
        pattern = rf"(?<![\w]){pattern}(?![\w])"
    return re.sub(pattern, lambda _m: replacement, text, flags=re.IGNORECASE)


class Masker:
    def __init__(
        self,
        profile: ProductProfile | None = None,
        params: dict[str, str] | None = None,
        secrets: list[str] | None = None,
    ) -> None:
        self.profile = profile
        self.params: dict[str, str] = {}
        self.secrets: list[str] = [s for s in (secrets or []) if s]
        self._tokens: dict[str, str] = {}
        self._counts: dict[str, int] = defaultdict(int)
        for name, value in (params or {}).items():
            self.add_param(name, value)

    # ---------------- registry ----------------
    def add_param(self, name: str, value: str | None) -> None:
        if value not in (None, ""):
            self.params[name] = str(value)

    def rename_param(self, old: str, new: str) -> None:
        if old in self.params:
            self.params[new] = self.params.pop(old)

    def add_secret(self, value: str | None) -> None:
        if value and value not in self.secrets:
            self.secrets.append(value)

    def token(self, value: str, kind: str) -> str:
        key = " ".join(value.split()).upper()
        if not key:
            return value
        if key not in self._tokens:
            self._counts[kind] += 1
            self._tokens[key] = f"«{kind}_{self._counts[kind]}»"
        return self._tokens[key]

    def sensitive_values(self) -> list[str]:
        values = {*self._tokens.keys(), *(v.upper() for v in self.params.values()), *(s.upper() for s in self.secrets)}
        return sorted((v for v in values if len(v) >= 2), key=len, reverse=True)

    # ---------------- text ----------------
    def mask_text(self, text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        out = text
        for secret in sorted(self.secrets, key=len, reverse=True):
            out = _sub_ci(out, secret, "«secret»")
        for name, value in sorted(self.params.items(), key=lambda kv: -len(kv[1])):
            out = _sub_ci(out, value, "{{" + name + "}}", word=True)
        for real, tok in sorted(self._tokens.items(), key=lambda kv: -len(kv[0])):
            out = _sub_ci(out, real, tok)
        out = SSN_RE.sub(lambda m: self.token(m.group(0), "SSN"), out)
        out = MONEY_RE.sub(lambda m: self.token(m.group(0), "MONEY"), out)
        out = EMAIL_RE.sub(lambda m: self.token(m.group(0), "EMAIL"), out)
        out = PHONE_RE.sub(lambda m: self.token(m.group(0), "PHONE"), out)
        out = LONG_DIGITS_RE.sub(lambda m: self.token(m.group(0), "NUM"), out)
        return self._heading_rules(out)

    def _heading_rules(self, text: str) -> str:
        if not self.profile:
            return text
        for rule in self.profile.field_classification:
            prefix = rule.match.get("heading_contains")
            if not prefix or rule.part != "after_member_number":
                continue
            pattern = re.compile(
                rf"^(\s*{re.escape(prefix)}\s+(?:\{{\{{\w+\}}\}}|«NUM_\d+»|\d{{6,10}})\s+)([A-Z][A-Z .'\-]*[A-Z])\s*$",
                re.IGNORECASE,
            )
            match = pattern.match(text)
            if match and rule.classification in SENSITIVE:
                text = match.group(1) + self.token(match.group(2), "NAME")
        return text

    def redact(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.mask_text(obj)
        if isinstance(obj, dict):
            return {k: self.redact(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact(v) for v in obj]
        return obj

    # ---------------- classification ----------------
    def classify_cell(self, table: str, column: str) -> DataClass | None:
        if not self.profile:
            return None
        for rule in self.profile.field_classification:
            if "table" in rule.match and _norm(rule.match["table"]) == _norm(table) and _norm(rule.match.get("column")) == _norm(column):
                return rule.classification
        return None

    def classify_section(self, label: str | None) -> DataClass | None:
        if not self.profile or not label:
            return None
        for rule in self.profile.field_classification:
            if "section" in rule.match and _norm(rule.match["section"]) == _norm(label):
                return rule.classification
        return None

    def mask_by_class(self, text: str, cls: DataClass | None) -> str:
        if cls not in SENSITIVE or not text:
            return self.mask_text(text)
        if MONEY_RE.search(text):
            return self.token(text, "MONEY")
        return self.token(text, "ACCT" if cls == DataClass.financial else "TEXT")

    def mask_cell(self, table: str, column: str, text: str) -> str:
        return self.mask_by_class(text, self.classify_cell(table, column))

    def mask_row_values(self, table: str, values: dict[str, str]) -> dict[str, str]:
        return {col: self.mask_cell(table, col, val) for col, val in values.items()}

    # ---------------- LLM view ----------------
    def render(self, snap: Snapshot) -> str:
        lines: list[str] = []
        for frame in snap.frames:
            if not frame.layout:
                continue
            parsed = urlparse(frame.url)
            route = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            lines.append(f"FRAME {frame.name}  route={self.mask_text(route)}")
            self._render_items(frame.layout, snap, lines, "  ")
        return "\n".join(lines)

    def observe(self, snap: Snapshot) -> None:
        """Register every sensitive value on screen (used before masked screenshots)."""
        self.render(snap)

    def _render_items(self, items: list[dict[str, Any]], snap: Snapshot, lines: list[str], indent: str) -> None:
        for item in items:
            kind = item.get("type")
            if kind == "heading":
                lines.append(f'{indent}heading "{self.mask_text(item["text"])}"')
            elif kind == "text":
                lines.append(f'{indent}text "{self.mask_text(item["text"])}"')
            elif kind == "control":
                node = snap.elements.get(item["ref"])
                if node:
                    lines.append(indent + self.render_element(node))
            elif kind == "table":
                table = snap.tables.get(item["ref"])
                if table:
                    self._render_table(table, snap, lines, indent)
            elif kind == "row":
                lines.append(indent + "row: " + self._render_row(item, snap))
            elif kind == "dialog":
                lines.append(f'{indent}DIALOG "{self.mask_text(item.get("title") or "")}" {{')
                self._render_items(item.get("items", []), snap, lines, indent + "  ")
                lines.append(indent + "}")

    def _render_row(self, row: dict[str, Any], snap: Snapshot) -> str:
        section = self.classify_section(row.get("label"))
        parts = []
        for position, cell in enumerate(row.get("cells", [])):
            bits = []
            for sub in cell:
                kind = sub.get("type")
                if kind == "text":
                    if position > 0 and section in SENSITIVE:
                        bits.append(f"(untrusted page text) {self.mask_by_class(sub['text'], section)}")
                    else:
                        bits.append(f'"{self.mask_text(sub["text"])}"')
                elif kind == "control":
                    node = snap.elements.get(sub["ref"])
                    if node:
                        bits.append(self.render_element(node))
                elif kind == "heading":
                    bits.append(f'heading "{self.mask_text(sub["text"])}"')
                elif kind == "table":
                    table = snap.tables.get(sub["ref"])
                    if table:
                        bits.append(f'[{table.ref}] TABLE "{table.name}"')
            if bits:
                parts.append(" ".join(bits))
        return " | ".join(parts)

    def render_element(self, node: ElementNode) -> str:
        parts = [f"[{node.ref}] {node.role}"]
        if node.name:
            parts.append(f'"{self.mask_text(node.name)}"')
        if node.inferred_label and node.role in ("textbox", "combobox", "checkbox", "radio"):
            parts.append(f'label="{self.mask_text(node.inferred_label)}"')
        if "secret" in node.states:
            parts.append("(password)")
        elif node.value:
            parts.append(f'value="{self.mask_text(node.value)}"')
        if node.options:
            parts.append("options=[" + ", ".join(f'"{self.mask_text(o)}"' for o in node.options) + "]")
        for state in node.states:
            if state in ("disabled", "checked", "readonly"):
                parts.append(f"({state})")
        return " ".join(parts)

    def _render_table(self, table: TableNode, snap: Snapshot, lines: list[str], indent: str) -> None:
        columns = [c for c in table.columns if c]
        lines.append(f'{indent}[{table.ref}] TABLE "{table.name}"  columns: ' + " | ".join(columns))
        for number, row in enumerate(table.rows, start=1):
            bits = []
            for cell in row.cells:
                if cell is None:
                    continue
                bits.append(f"[{cell.ref}] {self._quoted_cell(table.name, cell.column, cell.text)}")
            for ref in row.controls:
                node = snap.elements.get(ref)
                if node:
                    bits.append(self.render_element(node))
            lines.append(f"{indent}   row {number}: " + " | ".join(bits))

    def _quoted_cell(self, table: str, column: str, text: str) -> str:
        cls = self.classify_cell(table, column)
        if cls in SENSITIVE:
            return self.mask_by_class(text, cls)
        return f'"{self.mask_text(text)}"'
