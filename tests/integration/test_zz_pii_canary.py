"""Runs last: no PII canary value may appear in any persisted file."""

import pytest

from fakebank.seed import PII_CANARIES
from rote.config import CATALOG_DIR, EVIDENCE_DIR, RUNS_DIR

pytestmark = pytest.mark.integration

TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}


def test_no_canary_in_persisted_files():
    leaks = []
    for root in (RUNS_DIR, CATALOG_DIR, EVIDENCE_DIR):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="ignore")
                leaks += [f"{path}: {c}" for c in PII_CANARIES if c in text]
    assert not leaks, leaks[:10]
