"""Paths, ports, and environment variable names. No credential values live here."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("ROTE_HOME", Path(__file__).resolve().parents[2]))
CATALOG_DIR = ROOT / "catalog"
POLICIES_DIR = ROOT / "policies"
RUNS_DIR = ROOT / "runs"
EVIDENCE_DIR = ROOT / "evidence"
SCHEMAS_DIR = ROOT / "schemas"
SCRIPTS_DIR = ROOT / "scripts"

FAKEBANK_HOST = "127.0.0.1"
FAKEBANK_PORT = 8700
FAKEBANK_CONTROL_PORT = 8701
OPERATOR_PORT = 8765

# Credentials for a FakeBank instance started outside Rote. When Rote starts
# FakeBank itself it generates per-run values and keeps them in memory only.
ENV_OPERATOR_ID = "ROTE_FAKEBANK_OPERATOR_ID"
ENV_OPERATOR_PASSWORD = "ROTE_FAKEBANK_OPERATOR_PASSWORD"

# Model API keys (only needed for live discovery).
ENV_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
ENV_GEMINI_KEY = "GEMINI_API_KEY"
DOTENV_KEYS = (ENV_ANTHROPIC_KEY, ENV_GEMINI_KEY)

DEFAULT_PRODUCT = "fakebank-core"


def load_dotenv_keys(path: Path | None = None) -> list[str]:
    """Copy model API keys from a local, gitignored .env into the environment.

    Only DOTENV_KEYS are read; a variable already set in the environment wins.
    Returns the names that were loaded from the file.
    """
    path = path or ROOT / ".env"
    if not path.is_file():
        return []
    raw = path.read_bytes()
    # Windows PowerShell 5.1 writes UTF-16 by default; editors write UTF-8, sometimes with a BOM.
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    loaded = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in DOTENV_KEYS and value and not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded
