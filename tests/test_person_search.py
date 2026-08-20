"""בדיקות חיפוש מדריך לפי שם, מספר עובד ומספר זהות."""

from utils.utils import person_matches_search


def test_empty_query_matches_everyone() -> None:
    assert person_matches_search("", "אבנסון חני", "4495", "207646050")
    assert person_matches_search(None, "אבנסון חני")


def test_matches_by_name() -> None:
    assert person_matches_search("אבנס", "אבנסון חני", "4495", "207646050")
    assert not person_matches_search("כהן", "אבנסון חני", "4495", "207646050")


def test_matches_by_employee_number() -> None:
    assert person_matches_search("4495", "אבנסון חני", "4495", "207646050")
    assert person_matches_search("495", "אבנסון חני", "4495", "207646050")


def test_matches_by_id_number() -> None:
    assert person_matches_search("207646050", "אבנסון חני", "4495", "207646050")
    assert person_matches_search("207-646-050", "אבנסון חני", "4495", "207646050")
    assert person_matches_search("646050", "אבנסון חני", "4495", "207646050")


def test_missing_codes_do_not_match_numeric_query() -> None:
    assert not person_matches_search("4495", "אבנסון חני", None, None)
