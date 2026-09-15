import pytest

from rote.policy.engine import PolicyEngine

CLOSE = {"role": "button", "name": "CLOSE SHARE", "form_submit": False, "form_method": "POST"}
GO = {"role": "button", "name": "GO", "form_submit": True, "form_method": "GET"}
UNKNOWN_POST = {"role": "button", "name": "PROCEED", "form_submit": True, "form_method": "POST"}
LINK = {"role": "link", "name": "MEMBER INQ", "form_submit": False, "form_method": None}
SIGN_ON = {"role": "button", "name": "SIGN ON", "form_submit": True, "form_method": "POST"}


@pytest.mark.parametrize(
    "mode, action, element, screen, declared, dialog, expected",
    [
        ("discovery", "click", CLOSE, "member_detail", None, None, "require_approval"),
        ("replay", "click", CLOSE, "member_detail", "safe", None, "block"),
        ("replay", "click", CLOSE, "member_detail", "irreversible", None, "require_approval"),
        ("replay", "click", GO, "member_inquiry", "safe", None, "allow"),
        ("discovery", "click", UNKNOWN_POST, "somewhere", None, None, "require_approval"),
        ("replay", "click", UNKNOWN_POST, "somewhere", "safe", None, "block"),
        ("discovery", "click", LINK, None, None, None, "allow"),
        ("session", "click", SIGN_ON, "sign_on", "safe", None, "allow"),
        ("session", "click", SIGN_ON, None, "safe", None, "block"),
        ("discovery", "accept_dialog", None, None, None, "CLOSE SHARE S-01?", "require_approval"),
        ("discovery", "accept_dialog", None, None, None, "PASSWORD REQUIRED", "allow"),
    ],
)
def test_decisions(policy, mode, action, element, screen, declared, dialog, expected):
    engine = PolicyEngine(policy)
    decision = engine.decide(mode=mode, action=action, element=element, screen=screen, dialog_text=dialog, declared_risk=declared)
    assert decision.decision == expected


def test_runtime_classification_overrides_declared_safe(policy):
    decision = PolicyEngine(policy).decide(mode="replay", action="click", element=CLOSE, screen="member_detail", declared_risk="safe")
    assert decision.risk == "irreversible" and decision.blocked and "not declared" in decision.reason


def test_keys_and_actions(policy):
    engine = PolicyEngine(policy)
    assert engine.decide(mode="discovery", action="press_key", element=None, key="F5").blocked
    assert engine.decide(mode="discovery", action="execute_js", element=None).blocked


def test_url_allowlist(policy):
    engine = PolicyEngine(policy)
    assert engine.url_allowed("http://127.0.0.1:8700/cgi/MBRINQ")[0]
    assert not engine.url_allowed("http://127.0.0.1:8700/cgi/ADMIN")[0]
    assert not engine.url_allowed("http://127.0.0.1:8701/__faults")[0]
    assert not engine.url_allowed("https://example.com/")[0]
