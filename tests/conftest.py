from __future__ import annotations

import pytest

from rote.config import CATALOG_DIR, POLICIES_DIR
from rote.models import Capability, Policy, ProductProfile


@pytest.fixture(scope="session")
def profile() -> ProductProfile:
    return ProductProfile.model_validate_json((CATALOG_DIR / "fakebank-core" / "profile.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def policy() -> Policy:
    return Policy.model_validate_json((POLICIES_DIR / "fakebank-core.policy.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def handwritten() -> Capability:
    path = CATALOG_DIR / "fakebank-core" / "capabilities" / "get_savings_balance" / "1.0.0.json"
    return Capability.model_validate_json(path.read_text(encoding="utf-8"))
