"""Readable story of a run, generated from its (already redacted) events."""

from __future__ import annotations

import json
from pathlib import Path


def build_timeline(run_dir: Path) -> str:
    events_path = run_dir / "events.jsonl"
    events = []
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    run = {}
    if (run_dir / "run.json").exists():
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

    usage = run.get("llm_usage") or {}
    subject = run.get("capability") or run.get("goal") or ""
    header = [
        f"# {run.get('run_id', run_dir.name)}",
        "",
        f"- **kind:** {run.get('kind', '?')}",
        f"- **subject:** {subject}",
        f"- **status:** {run.get('status', '?')}" + (f" · `{run['code']}`" if run.get("code") else ""),
        f"- **duration:** {run.get('duration_ms', 0) / 1000:.1f} s",
        f"- **LLM:** {usage.get('calls', 0)} calls · {usage.get('input_tokens', 0) + usage.get('output_tokens', 0)} tokens · ${usage.get('cost_usd', 0):.4f}",
        "",
        "```",
    ]
    lines = []
    for event in events:
        time = event["ts"][11:19]
        data = event.get("data") or {}
        summary = data.get("summary")
        if not summary:
            if event["type"] in ("snapshot_taken", "effect_verified"):
                continue
            summary = event["type"].replace("_", " ")
        actor = event.get("actor", "system")
        lines.append(f"{time}  {actor:<14} {summary}")
    return "\n".join(header + lines + ["```", ""])


if __name__ == "__main__":
    import sys

    print(build_timeline(Path(sys.argv[1])))
