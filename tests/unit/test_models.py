import json

import pytest

from rote.models import Capability, InputSpec, Locator, ScreenPredicate
from rote.registry.store import content_hash, dump_model


def _data(cap: Capability) -> dict:
    return json.loads(json.dumps(dump_model(cap)))


def test_handwritten_capability_loads(handwritten):
    assert handwritten.id == "fakebank.member.get_savings_balance"
    assert handwritten.targets["regular_savings_balance_cell"].durable_count == 1


def test_unknown_screen_reference_rejected(handwritten):
    data = _data(handwritten)
    data["steps"][0]["expect"][0]["screen"] = "no_such_screen"
    with pytest.raises(ValueError, match="unknown screen"):
        Capability.model_validate(data)


def test_undeclared_outcome_rejected(handwritten):
    data = _data(handwritten)
    data["contract"]["outcomes"] = [o for o in data["contract"]["outcomes"] if o["code"] != "MEMBER_NOT_FOUND"]
    with pytest.raises(ValueError, match="undeclared outcome"):
        Capability.model_validate(data)


def test_irreversible_step_must_be_declared(handwritten):
    data = _data(handwritten)
    data["steps"][2]["risk"] = "irreversible"
    with pytest.raises(ValueError, match="irreversible_steps"):
        Capability.model_validate(data)


def test_secret_inputs_are_not_allowed():
    with pytest.raises(ValueError, match="secret"):
        InputSpec(type="string", classification="secret")


def test_unknown_fields_are_errors(handwritten):
    data = _data(handwritten)
    data["contract"]["surprise"] = True
    with pytest.raises(ValueError):
        Capability.model_validate(data)


def test_locator_requires_its_fields():
    with pytest.raises(ValueError, match="needs"):
        Locator(kind="table_cell", table="SHARE ACCOUNTS")
    assert not Locator(kind="css", value="a > b").durable


def test_screen_predicate_needs_exactly_one_condition():
    with pytest.raises(ValueError):
        ScreenPredicate(heading="A", text_contains="B")
    with pytest.raises(ValueError):
        ScreenPredicate(frame="main")


def test_content_hash_ignores_status_and_provenance(handwritten):
    other = handwritten.model_copy(deep=True)
    other.status = "validated"
    other.version = "9.9.9"
    other.provenance.validated_by_runs = ["rep_x"]
    assert content_hash(other) == content_hash(handwritten)
    other.steps[0].timeout_ms = 1234
    assert content_hash(other) != content_hash(handwritten)


def test_profile_hash_ignores_deployment_origin(profile):
    moved = profile.model_copy(deep=True)
    moved.entry.origin = "http://127.0.0.1:9999"
    assert content_hash(moved) == content_hash(profile)
