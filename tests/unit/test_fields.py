"""Label–value fields (MEMBER SINCE | 09/02/2016): addressable, masked when sensitive, compiled to a field locator."""

import pytest

from rote.agent.planners.scripted import find_ref
from rote.compiler.compile import target_name
from rote.models.capability import Locator
from rote.perception.masking import Masker
from rote.surface.snapshot import FieldNode, FrameView, Snapshot


def _snapshot() -> Snapshot:
    fields = {
        "e1": FieldNode(ref="e1", frame="main", index=0, label="TAX ID", text="900-00-4321"),
        "e2": FieldNode(ref="e2", frame="main", index=1, label="MEMBER SINCE", text="09/02/2016"),
    }
    layout = [
        {"type": "row", "label": "TAX ID", "cells": [[{"type": "text", "text": "TAX ID"}], [{"type": "text", "text": "900-00-4321", "ref": "e1"}]]},
        {"type": "row", "label": "MEMBER SINCE", "cells": [[{"type": "text", "text": "MEMBER SINCE"}], [{"type": "text", "text": "09/02/2016", "ref": "e2"}]]},
    ]
    return Snapshot(frames=[FrameView(name="main", url="http://x/cgi/MBRDTL?m=100245", layout=layout)], fields=fields)


def test_field_locator_is_durable_and_needs_a_label():
    loc = Locator(kind="field", label="MEMBER SINCE", frame="main")
    assert loc.durable
    assert loc.describe() == 'value next to "MEMBER SINCE" [main]'
    with pytest.raises(ValueError):
        Locator(kind="field")


def test_fields_get_refs_and_sensitive_values_stay_masked(profile):
    view = Masker(profile).render(_snapshot())
    assert '[e2] "09/02/2016"' in view
    assert "[e1] (untrusted page text) «TEXT_1»" in view
    assert "900-00-4321" not in view


def test_snapshot_node_lookup_includes_fields():
    snap = _snapshot()
    assert snap.has_ref("e2")
    assert snap.node("e2").describe() == 'value next to "MEMBER SINCE" [main]'


def test_scripted_planner_and_compiler_address_fields():
    assert find_ref(_snapshot(), {"field": "member since:"}) == "e2"
    assert target_name(Locator(kind="field", label="MEMBER SINCE")) == "member_since_value"
