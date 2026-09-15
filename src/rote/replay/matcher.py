"""Screen predicate evaluation over a cheap state probe. Pure functions, unit-testable without a browser."""

from __future__ import annotations

import fnmatch
import re
from typing import Any
from urllib.parse import urlparse

from rote.models.capability import Screen, ScreenPredicate

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
Probe = dict[str, dict[str, Any]]


def norm(text: str | None) -> str:
    return " ".join((text or "").replace(" ", " ").split()).rstrip(": ").upper()


def fill(text: str, params: dict[str, str]) -> str | None:
    """Substitute placeholders; None if any placeholder has no value (such a predicate can never match)."""
    missing = False

    def repl(match: re.Match) -> str:
        nonlocal missing
        name = match.group(1)
        if name not in params:
            missing = True
            return match.group(0)
        return str(params[name])

    out = PLACEHOLDER.sub(repl, text)
    return None if missing else out


def _frames(probe: Probe, frame: str | None) -> list[dict[str, Any]]:
    if frame:
        return [probe[frame]] if frame in probe else []
    return list(probe.values())


def eval_predicate(pred: ScreenPredicate, probe: Probe, params: dict[str, str]) -> bool:
    if pred.not_ is not None:
        inner = pred.not_
        if inner.frame is None and pred.frame is not None:
            inner = inner.model_copy(update={"frame": pred.frame})
        return not eval_predicate(inner, probe, params)
    frames = _frames(probe, pred.frame)
    if pred.heading is not None:
        want = fill(pred.heading, params)
        return want is not None and any(norm(h) == norm(want) for f in frames for h in f.get("headings", []))
    if pred.heading_contains is not None:
        want = fill(pred.heading_contains, params)
        return want is not None and any(norm(want) in norm(h) for f in frames for h in f.get("headings", []))
    if pred.text_contains is not None:
        want = fill(pred.text_contains, params)
        return want is not None and any(norm(want) in norm(f.get("text")) for f in frames)
    if pred.dialog_text_contains is not None:
        want = fill(pred.dialog_text_contains, params)
        return want is not None and any(norm(want) in norm(d) for f in frames for d in f.get("dialogs", []))
    if pred.route_matches is not None:
        return any(fnmatch.fnmatch(urlparse(f.get("url", "")).path, pred.route_matches) for f in frames)
    if pred.role_exists is not None:
        name = fill(pred.role_exists.name, params)
        return name is not None and any(norm(name) in norm(f.get("text")) for f in frames)
    return False


def screen_matches(screen: Screen, probe: Probe, params: dict[str, str]) -> bool:
    return all(eval_predicate(p, probe, params) for p in screen.match)


def dialog_predicates(screen: Screen) -> list[str]:
    return [p.dialog_text_contains for p in screen.match if p.dialog_text_contains]


def all_dialogs(probe: Probe) -> list[str]:
    return [d for f in probe.values() for d in f.get("dialogs", [])]


def loading(probe: Probe, frame: str = "main") -> bool:
    main = probe.get(frame)
    return main is None or main.get("ready") not in (None, "complete", "interactive") or not (main.get("text") or main.get("headings"))


def signature(probe: Probe) -> str:
    """Change detector for 'did anything happen after the action?'"""
    parts = []
    for name in sorted(probe):
        f = probe[name]
        parts.append(f"{name}|{f.get('url')}|{'/'.join(f.get('headings', []))}|{'/'.join(f.get('dialogs', []))}")
    return "\n".join(parts)


def match_catalog(catalog: dict[str, Screen], probe: Probe, params: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    """Screens that match, trying every assignment of run parameters to catalog placeholders.

    Discovery runs use placeholders like {{value_1}} while the catalog says {{member_number}};
    the returned binding ({"member_number": "value_1"}) tells the compiler how they correspond."""
    found = []
    values = list(params.items())
    for screen_id, screen in catalog.items():
        names = sorted({m.group(1) for p in screen.match for m in PLACEHOLDER.finditer(_pred_text(p))})
        if not names:
            if screen_matches(screen, probe, {}):
                found.append((screen_id, {}))
            continue
        for assignment in _assignments(names, values):
            bound = {name: value for name, (_param, value) in assignment.items()}
            if screen_matches(screen, probe, bound):
                found.append((screen_id, {name: param for name, (param, _value) in assignment.items()}))
                break
    return found


def _pred_text(pred: ScreenPredicate) -> str:
    parts = [pred.heading, pred.heading_contains, pred.text_contains, pred.dialog_text_contains]
    if pred.not_ is not None:
        parts.append(_pred_text(pred.not_))
    return " ".join(p for p in parts if p)


def _assignments(names: list[str], values: list[tuple[str, str]]):
    if not names:
        yield {}
        return
    first, rest = names[0], names[1:]
    for item in values:
        for tail in _assignments(rest, values):
            yield {first: item, **tail}
