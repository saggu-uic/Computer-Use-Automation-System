"""Replay must never depend on the agent/planner code."""

import ast
import subprocess
import sys
from pathlib import Path

REPLAY = Path(__file__).resolve().parents[2] / "src" / "rote" / "replay"


def test_replay_sources_do_not_import_agent():
    for path in REPLAY.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.startswith("rote.agent") for n in names), f"{path.name} imports {names}"


def test_importing_replay_loads_no_agent_or_llm_sdk():
    code = "import sys, rote.replay.engine; print([m for m in sys.modules if m.startswith(('rote.agent', 'anthropic', 'google.genai'))])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
