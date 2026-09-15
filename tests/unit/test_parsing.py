import pytest

from rote.models import InputSpec
from rote.replay.parsing import ParseError, infer_columns, infer_type, parse_money, parse_table, parse_value, snake, validate_input


@pytest.mark.parametrize(
    "text, amount",
    [("$4,210.55", "4210.55"), ("($84.12)", "-84.12"), ("$3.51", "3.51"), ("-$10.00", "-10.00"), ("$2,150", "2150.00")],
)
def test_money(text, amount):
    assert parse_money(text) == {"amount": amount, "currency": "USD"}


@pytest.mark.parametrize("text", ["", "   ", "N/A", "$1.2.3", "ALPHA"])
def test_money_garbage(text):
    with pytest.raises(ParseError):
        parse_money(text)


def test_dates_and_integers():
    assert parse_value("date", "09/12/2026") == "2026-09-12"
    assert parse_value("integer", "1,234") == 1234
    with pytest.raises(ParseError):
        parse_value("date", "yesterday")


def test_infer_type():
    assert infer_type(["$1.00", "($2.50)"]) == "money"
    assert infer_type(["09/12/2026", "08/01/2026"]) == "date"
    assert infer_type(["S-10", "S-01"]) == "string"
    assert infer_type(["12", "40"]) == "integer"


def test_table_parsing():
    raw = {"columns": ["DATE", "SHARE", "DESCRIPTION", "AMOUNT"], "rows": [["09/12/2026", "S-10", "POS PURCHASE", "($84.12)"]]}
    columns = infer_columns(raw)
    assert columns == {"date": "date", "share": "string", "description": "string", "amount": "money"}
    assert parse_table(raw, columns) == [{"date": "2026-09-12", "share": "S-10", "description": "POS PURCHASE", "amount": {"amount": "-84.12", "currency": "USD"}}]


def test_snake_names_from_legacy_labels():
    assert snake("MEMBER #") == "member_number"
    assert snake("ACCT HOLDER NO.") == "acct_holder_number"
    assert snake("TRANSACTION HISTORY") == "transaction_history"


def test_validate_input():
    spec = InputSpec(type="string", pattern="^[0-9]{6,10}$")
    assert validate_input("member_number", spec, "100245") is None
    assert "must match" in validate_input("member_number", spec, "12AB")
    assert validate_input("member_number", spec, None) == "required"
