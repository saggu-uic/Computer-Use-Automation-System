"""Run records: events.jsonl, run.json, snapshots, screenshots, timeline. Every write is redacted."""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rote.config import RUNS_DIR

PREFIX = {"discovery": "disc", "replay": "rep", "validation": "val", "probe": "probe"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunRecorder:
    def __init__(self, kind: str, *, runs_dir: Path | None = None, redact: Callable[[Any], Any] | None = None) -> None:
        self.kind = kind
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = f"{PREFIX.get(kind, kind)}_{stamp}_{secrets.token_hex(2)}"
        self.dir = (runs_dir or RUNS_DIR) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._redact = redact or (lambda value: value)
        self._t0 = time.monotonic()
        self.seq = 0
        self.events: list[dict[str, Any]] = []
        self.llm_usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self._events_path = self.dir / "events.jsonl"

    def set_redactor(self, redact: Callable[[Any], Any]) -> None:
        self._redact = redact

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def emit(self, type_: str, *, actor: str = "system", step: int | str | None = None, summary: str | None = None, **data: Any) -> dict[str, Any]:
        self.seq += 1
        payload = {"summary": summary, **data} if summary is not None else dict(data)
        event = {
            "ts": now_iso(),
            "run_id": self.run_id,
            "seq": self.seq,
            "actor": actor,
            "type": type_,
            "step": step,
            "data": self._redact(payload),
        }
        self.events.append(event)
        with self._events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event

    def record_llm_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.llm_usage["calls"] += 1
        self.llm_usage["input_tokens"] += input_tokens
        self.llm_usage["output_tokens"] += output_tokens
        self.llm_usage["cost_usd"] = round(self.llm_usage["cost_usd"] + cost_usd, 6)

    def path(self, relative: str) -> Path:
        target = self.dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, relative: str, obj: Any, *, redact: bool = True) -> Path:
        target = self.path(relative)
        data = self._redact(obj) if redact else obj
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return target

    def write_text(self, relative: str, text: str, *, redact: bool = True) -> Path:
        target = self.path(relative)
        target.write_text((self._redact(text) if redact else text) + "\n", encoding="utf-8")
        return target

    def relative(self, path: Path | str | None) -> str | None:
        if path is None:
            return None
        try:
            return str(Path(path).relative_to(self.dir.parent.parent)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def finish(self, summary: dict[str, Any]) -> Path:
        from rote.evidence.timeline import build_timeline

        record = {
            "run_id": self.run_id,
            "kind": self.kind,
            "duration_ms": self.elapsed_ms(),
            "llm_usage": self.llm_usage,
            **summary,
        }
        self.write_json("run.json", record)
        (self.dir / "timeline.md").write_text(build_timeline(self.dir), encoding="utf-8")
        return self.dir
