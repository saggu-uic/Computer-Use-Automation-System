from rote.agent.inputs import build_bindings, detect_inputs


def test_member_number_detected():
    text, bindings = detect_inputs("Show recent transactions for member 100245")
    assert text == "Show recent transactions for member {{value_1}}"
    assert [(b.placeholder, b.value, b.kind) for b in bindings] == [("value_1", "100245", "digits")]


def test_money_date_quoted():
    text, bindings = detect_inputs("Wire $500 on 09/30/2026 from member 100245 to 'HOLIDAY FUND'")
    kinds = {b.kind: b.value for b in bindings}
    assert kinds == {"quoted": "HOLIDAY FUND", "money": "$500", "date": "09/30/2026", "digits": "100245"}
    assert "100245" not in text and "$500" not in text


def test_small_numbers_are_not_inputs():
    text, bindings = detect_inputs("Show the last 10 transactions")
    assert bindings == [] and text == "Show the last 10 transactions"


def test_explicit_input_wins():
    text, bindings = build_bindings("Balance for member 100245", ["member_number=100245:string"])
    assert text == "Balance for member {{member_number}}"
    assert len(bindings) == 1 and bindings[0].explicit_name == "member_number"


def test_no_detect():
    text, bindings = build_bindings("Balance for member 100245", [], detect=False)
    assert bindings == [] and "100245" in text
