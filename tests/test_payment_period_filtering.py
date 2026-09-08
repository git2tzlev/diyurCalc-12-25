from datetime import date

from core.payment_period import filter_items_for_work_month


def test_future_payment_month_items_are_excluded_from_work_month_payroll():
    items = [
        {"id": 1, "date": date(2026, 9, 10), "payment_year": None, "payment_month": None},
        {"id": 2, "date": date(2026, 9, 11), "payment_year": 2026, "payment_month": 9},
        {"id": 3, "date": date(2026, 9, 12), "payment_year": 2026, "payment_month": 10},
    ]

    payable = filter_items_for_work_month(items, 2026, 9)

    assert [item["id"] for item in payable] == [1, 2]


def test_future_payment_month_filter_handles_year_transition():
    items = [
        {"id": 1, "date": date(2026, 12, 20), "payment_year": 2027, "payment_month": 1},
    ]

    assert filter_items_for_work_month(items, 2026, 12) == []


def test_completion_calculation_can_include_deferred_items_explicitly():
    items = [
        {"id": 1, "date": date(2026, 9, 12), "payment_year": 2026, "payment_month": 10},
    ]

    payable = filter_items_for_work_month(items, 2026, 9, include_deferred=True)

    assert payable == items


def test_completion_calculation_includes_only_requested_deferred_payment_month():
    items = [
        {"id": 1, "date": date(2026, 7, 10), "payment_year": None, "payment_month": None},
        {"id": 2, "date": date(2026, 7, 11), "payment_year": 2026, "payment_month": 8},
        {"id": 3, "date": date(2026, 7, 12), "payment_year": 2026, "payment_month": 10},
    ]

    payable = filter_items_for_work_month(
        items,
        2026,
        7,
        include_deferred=True,
        deferred_payment_period=(2026, 8),
    )

    assert [item["id"] for item in payable] == [1, 2]
