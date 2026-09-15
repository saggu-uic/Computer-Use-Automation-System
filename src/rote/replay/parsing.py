"""Typed parsing of values read from the screen, and validation of caller inputs."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from rote.models.capability import InputSpec

MONEY_SHAPE = re.compile(r"^\(?-?\$?\s?\d[\d,]*(\.\d{1,2})?\)?$")
DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")


class ParseError(ValueError):
    pass


def snake(text: str) -> str:
    text = text.replace("#", " number ").replace("&", " and ")
    text = re.sub(r"\bNO\.?(?=\s|$)", " number", text, flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return re.sub(r"_+", "_", text) or "value"


def parse_money(text: str) -> dict[str, str]:
    raw = " ".join(text.split())
    if not raw or not MONEY_SHAPE.match(raw):
        raise ParseError(f"not a money value: {len(raw)} characters")
    negative = raw.startswith("(") or raw.startswith("-") or "-$" in raw
    digits = re.sub(r"[^\d.]", "", raw)
    try:
        amount = Decimal(digits).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ParseError("not a money value") from exc
    if negative:
        amount = -amount
    return {"amount": f"{amount:.2f}", "currency": "USD"}


def parse_date(text: str) -> str:
    raw = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ParseError("not a date")


def parse_value(kind: str, text: str) -> Any:
    raw = " ".join((text or "").split())
    if kind == "string":
        if not raw:
            raise ParseError("empty value")
        return raw
    if kind == "money":
        return parse_money(raw)
    if kind == "date":
        return parse_date(raw)
    if kind == "integer":
        try:
            return int(raw.replace(",", ""))
        except ValueError as exc:
            raise ParseError("not an integer") from exc
    if kind == "decimal":
        try:
            return str(Decimal(raw.replace(",", "")))
        except InvalidOperation as exc:
            raise ParseError("not a decimal") from exc
    if kind == "boolean":
        if raw.upper() in ("Y", "YES", "TRUE", "ON"):
            return True
        if raw.upper() in ("N", "NO", "FALSE", "OFF"):
            return False
        raise ParseError("not a boolean")
    if kind in ("enum",):
        return raw
    raise ParseError(f"unknown type {kind!r}")


def infer_type(values: list[str]) -> str:
    present = [v for v in values if v and v.strip()]
    if not present:
        return "string"
    for kind in ("money", "date", "integer"):
        try:
            for value in present:
                if kind == "integer" and not re.fullmatch(r"-?[\d,]+", value.strip()):
                    raise ParseError("not integer")
                if kind == "money" and "$" not in value:
                    raise ParseError("money needs a currency sign")
                parse_value(kind, value)
            return kind
        except ParseError:
            continue
    return "string"


def infer_columns(raw: dict[str, Any]) -> dict[str, str]:
    columns = {}
    for index, header in enumerate(raw.get("columns", [])):
        if not header:
            continue
        values = [row[index] for row in raw.get("rows", []) if index < len(row)]
        columns[snake(header)] = infer_type(values)
    return columns


def parse_table(raw: dict[str, Any] | None, columns: dict[str, str] | None, max_rows: int | None = None) -> list[dict[str, Any]]:
    if not raw:
        raise ParseError("table not readable")
    spec = columns or infer_columns(raw)
    headers = [snake(h) if h else None for h in raw.get("columns", [])]
    rows = []
    for row in raw.get("rows", [])[: max_rows or None]:
        item = {}
        for index, name in enumerate(headers):
            if not name or name not in spec or index >= len(row):
                continue
            text = row[index]
            item[name] = parse_value(spec[name], text) if text.strip() else None
        rows.append(item)
    return rows


def validate_input(name: str, spec: InputSpec, value: Any) -> str | None:
    if value is None or str(value) == "":
        return "required"
    text = str(value)
    if spec.pattern and not re.fullmatch(spec.pattern, text):
        return f"must match {spec.pattern}"
    if spec.min_length is not None and len(text) < spec.min_length:
        return f"must be at least {spec.min_length} characters"
    if spec.max_length is not None and len(text) > spec.max_length:
        return f"must be at most {spec.max_length} characters"
    if spec.values and text not in spec.values:
        return f"must be one of {spec.values}"
    if spec.type in ("money", "date", "integer", "decimal", "boolean"):
        try:
            if spec.type == "money":
                parse_money(text if "$" in text else f"${text}")
            else:
                parse_value(spec.type, text)
        except ParseError as exc:
            return str(exc)
    return None
