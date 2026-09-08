from services.missing_gesher_candidates import (
    build_candidate_snapshots,
    is_item_eligible_for_rollforward,
    is_missing_final_candidate_item,
    merge_prior_completion_rows,
    remove_current_payment_completion_rows,
    source_item_belongs_to_candidate,
    was_code_missing_at_final,
)
from datetime import date, datetime


def _entry(**overrides):
    row = {
        "reason_category": "new_in_current",
        "category": "extra",
        "work_year": 2026,
        "work_month": 7,
        "company_code": "400",
        "filename": "gesher_400_2026_07.mrv",
        "file_id": 42,
        "person_id": 394,
        "person_name": "בודיק שירה",
        "employee_code": "009487",
        "symbol": "317",
        "actual_quantity": 0.0,
        "actual_amount": 460.2,
    }
    row.update(overrides)
    return row


def test_build_candidate_snapshots_groups_one_candidate_per_employee():
    result = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 8,
        "groups": [{
            "file_id": 42,
            "entries": [
                _entry(),
                _entry(symbol="253", actual_amount=32.0),
                _entry(symbol="410", actual_amount=0.0, actual_quantity=0.62),
            ],
        }],
    }, housing_array_id=1)

    assert len(result) == 1
    assert result[0]["person_id"] == 394
    assert result[0]["total_amount"] == 492.2
    assert result[0]["values"] == {
        "253": {"amount": 32.0, "quantity": 0.0},
        "317": {"amount": 460.2, "quantity": 0.0},
        "410": {"amount": 0.0, "quantity": 0.62},
    }
    assert result[0]["status"] == "pending"


def test_build_candidate_snapshots_ignores_other_difference_types():
    result = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 8,
        "groups": [{
            "file_id": 42,
            "entries": [
                _entry(reason_category="value_mismatch"),
                _entry(reason_category="removed_from_current"),
                _entry(category="matched", reason_category="matched"),
            ],
        }],
    }, housing_array_id=1)

    assert result == []


def test_build_candidate_snapshots_blocks_negative_net_value():
    result = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 8,
        "groups": [{"file_id": 42, "entries": [_entry(actual_amount=-10.0)]}],
    }, housing_array_id=1)

    assert result[0]["is_blocked"] is True
    assert result[0]["block_reason"] == "סכום ההשלמה שלילי ודורש בירור"


def test_approved_candidate_is_rebuilt_from_matched_rows_on_rescan():
    key = (2026, 7, "400", 394)
    result = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 8,
        "groups": [{
            "file_id": 42,
            "work_year": 2026,
            "work_month": 7,
            "company_code": "400",
            "entries": [_entry(category="matched", reason_category="matched")],
        }],
    }, housing_array_id=1, include_person_keys={key})

    assert len(result) == 1
    assert result[0]["person_id"] == 394


def test_unmarked_item_is_eligible_for_selected_payment_month():
    assert is_item_eligible_for_rollforward(None, None, 2026, 8) is True


def test_item_from_earlier_failed_payment_month_is_eligible_for_rollforward():
    assert is_item_eligible_for_rollforward(2026, 7, 2026, 8) is True


def test_item_marked_for_later_payment_month_is_not_moved_backwards():
    assert is_item_eligible_for_rollforward(2026, 10, 2026, 8) is False


def test_candidate_payment_item_is_kept_out_of_regular_completion_section():
    assert is_missing_final_candidate_item({
        "payment_note": "לא נכלל בגשר הסופי עקב קוד מירב חסר"
    }) is True
    assert is_missing_final_candidate_item({"payment_note": "השלמה רגילה"}) is False


def test_missing_code_candidate_requires_blank_code_at_final_file_time():
    final_at = datetime(2026, 7, 9, 13, 26)
    assert was_code_missing_at_final(
        person_created_at=datetime(2026, 6, 1),
        final_created_at=final_at,
        old_code_before_first_change="",
    ) is True
    assert was_code_missing_at_final(
        person_created_at=datetime(2026, 6, 1),
        final_created_at=final_at,
        old_code_before_first_change="6912",
    ) is False
    assert was_code_missing_at_final(
        person_created_at=datetime(2026, 7, 21),
        final_created_at=final_at,
        old_code_before_first_change="",
    ) is False


def test_prior_failed_payment_completions_are_added_to_candidate_amount():
    candidate = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 8,
        "groups": [{"file_id": 42, "entries": [_entry(person_id=392, actual_amount=2753.5)]}],
    }, housing_array_id=1)[0]

    merge_prior_completion_rows(candidate, [
        {"symbol": "253", "amount": 48.0, "quantity": 0.0},
        {"symbol": "317", "amount": 665.2, "quantity": 0.0},
    ])

    assert candidate["total_amount"] == 3466.7
    assert candidate["values"]["253"] == {"amount": 48.0, "quantity": 0.0}
    assert candidate["values"]["317"] == {"amount": 3418.7, "quantity": 0.0}


def test_completion_assigned_to_selected_payment_month_is_not_counted_in_missing_bridge_card():
    candidate = build_candidate_snapshots({
        "payment_year": 2026,
        "payment_month": 7,
        "groups": [{"file_id": 30, "entries": [_entry(
            person_id=378,
            work_month=6,
            employee_code="009401",
            symbol="253",
            actual_amount=2694.36,
        ), _entry(
            person_id=378,
            work_month=6,
            employee_code="009401",
            symbol="317",
            actual_amount=2708.10,
        )]}],
    }, housing_array_id=1)[0]

    remove_current_payment_completion_rows(candidate, [
        {"symbol": "253", "amount": 1000.0, "quantity": 0.0},
    ])

    assert candidate["total_amount"] == 4402.46
    assert candidate["values"]["253"] == {"amount": 1694.36, "quantity": 0.0}


def test_candidate_owns_prior_item_marked_for_the_missed_bridge_month():
    assert source_item_belongs_to_candidate(
        item_date=date(2026, 6, 30),
        payment_year=2026,
        payment_month=7,
        work_year=2026,
        work_month=7,
    ) is True
    assert source_item_belongs_to_candidate(
        item_date=date(2026, 6, 30),
        payment_year=2026,
        payment_month=6,
        work_year=2026,
        work_month=7,
    ) is False
