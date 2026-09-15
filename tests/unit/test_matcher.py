from rote.models import Screen
from rote.replay import matcher


def probe(heading="MEMBER DETAIL 100245 ALPHA VORTENSKI", text="", dialogs=None):
    return {"main": {"url": "http://x/cgi/MBRDTL?m=100245", "headings": [heading], "text": text or heading, "dialogs": dialogs or [], "ready": "complete"}}


def test_right_record_check(profile):
    detail = profile.screen_catalog["member_detail"]
    assert matcher.screen_matches(detail, probe(), {"member_number": "100245"})
    assert not matcher.screen_matches(detail, probe(), {"member_number": "100377"})
    assert not matcher.screen_matches(detail, probe(), {})  # unfilled placeholder never matches


def test_not_predicate(profile):
    tx = profile.screen_catalog["member_transactions"]
    heading = "TRANSACTION HISTORY 100245"
    assert matcher.screen_matches(tx, probe(heading, text=heading + " DATE SHARE"), {"member_number": "100245"})
    assert not matcher.screen_matches(tx, probe(heading, text=heading + " NO TRANSACTIONS IN LAST 90 DAYS"), {"member_number": "100245"})


def test_dialog_predicate(profile):
    notice = profile.global_screens["system_notice"]
    assert matcher.screen_matches(notice, probe(dialogs=["SYSTEM NOTICE: MAINTENANCE 22:00 ET OK"]), {})
    assert not matcher.screen_matches(notice, probe(), {})


def test_match_catalog_binds_placeholders(profile):
    found = dict(matcher.match_catalog(profile.screen_catalog, probe(), {"value_1": "100245"}))
    assert found["member_detail"] == {"member_number": "value_1"}


def test_signature_changes_with_screen():
    assert matcher.signature(probe("A")) != matcher.signature(probe("B"))


def test_screen_model_roundtrip():
    screen = Screen.model_validate({"match": [{"not": {"text_contains": "X"}, "frame": "main"}]})
    assert screen.match[0].not_.text_contains == "X"
