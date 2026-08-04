from services.gesher_difference import (
    _build_unverified_completion_diffs,
    build_approved_completion_gesher_rows,
    build_legacy_completion_gesher_rows_from_final_file,
    build_completion_gesher_file,
    build_completion_gesher_rows,
    build_current_gesher_lines,
    compare_line_sets,
    get_legacy_completion_items,
    parse_gesher_file_lines,
)


def test_parse_gesher_file_lines_skips_headers_and_invalid_rows():
    content = "\n".join([
        "HEADER TEXT",
        "000123 101 0008.50 00040.00          201",
        "bad row",
        "45 202 0000.00 00150.00          201",
    ])

    rows = parse_gesher_file_lines(content)

    assert rows == [
        {
            "employee_code": "000123",
            "symbol": "101",
            "quantity": 8.5,
            "rate": 40.0,
            "amount": 340.0,
            "line_number": 2,
            "raw_line": "000123 101 0008.50 00040.00          201",
        },
        {
            "employee_code": "000045",
            "symbol": "202",
            "quantity": 0.0,
            "rate": 150.0,
            "amount": 150.0,
            "line_number": 4,
            "raw_line": "45 202 0000.00 00150.00          201",
        },
    ]


def test_compare_line_sets_aggregates_by_employee_symbol_and_rate():
    base_lines = [
        {"employee_code": "000123", "symbol": "101", "rate": 40.0, "quantity": 5.0, "amount": 200.0},
        {"employee_code": "000123", "symbol": "101", "rate": 40.0, "quantity": 3.0, "amount": 120.0},
    ]
    current_lines = [
        {"employee_code": "000123", "symbol": "101", "rate": 40.0, "quantity": 10.0, "amount": 400.0},
        {"employee_code": "000123", "symbol": "202", "rate": 50.0, "quantity": 1.0, "amount": 50.0},
    ]

    diffs = compare_line_sets(base_lines, current_lines)

    assert [
        (diff["employee_code"], diff["symbol"], diff["quantity_diff"], diff["amount_diff"], diff["diff_type"])
        for diff in diffs
    ] == [
        ("000123", "101", 2.0, 80.0, "כמות השתנתה"),
        ("000123", "202", 1.0, 50.0, "שורה נוספה"),
    ]


def test_compare_line_sets_hides_total_hours_as_display_rate():
    base_lines = [{
        "employee_code": "000123",
        "symbol": "767",
        "display_name": "ימי עבודה",
        "value_type": "days_with_total_hours",
        "rate": 151.45,
        "quantity": 17.0,
        "amount": 2574.65,
    }]
    current_lines = [{
        "employee_code": "000123",
        "symbol": "767",
        "display_name": "ימי עבודה",
        "value_type": "days_with_total_hours",
        "rate": 146.45,
        "quantity": 17.0,
        "amount": 2489.65,
    }]

    diffs = compare_line_sets(base_lines, current_lines)

    assert len(diffs) == 2
    assert all(diff["display_rate"] is None for diff in diffs)


def test_build_completion_gesher_rows_maps_source_symbols_to_target_symbols():
    diffs = [
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "360", "amount_diff": 100.0},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "362", "amount_diff": 25.5, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "366", "amount_diff": 10.0, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "370", "amount_diff": 32.0, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "243", "amount_diff": 500.0, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "299", "quantity_diff": 0.55, "amount_diff": 0.0, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "698", "quantity_diff": 0.83, "amount_diff": 0.0, "employer_code": "400"},
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "767", "amount_diff": 900.0, "employer_code": "400"},
    ]

    rows = build_completion_gesher_rows(diffs)

    assert [(row["employer_code"], row["symbol"], row["amount"], row["quantity"]) for row in rows] == [
        ("400", "253", 42.0, 0.0),
        ("001", "317", 100.0, 0.0),
        ("400", "317", 25.5, 0.0),
    ]


def test_unverified_completion_diffs_net_by_employee_and_symbol():
    before_lines = [{
        "employee_code": "000123",
        "person_name": "מדריך בדיקה",
        "symbol": "370",
        "display_name": "נסיעות",
        "rate": 96.0,
        "quantity": 0.0,
        "amount": 96.0,
    }]
    after_lines = [{
        "employee_code": "000123",
        "person_name": "מדריך בדיקה",
        "symbol": "370",
        "display_name": "נסיעות",
        "rate": 112.0,
        "quantity": 0.0,
        "amount": 112.0,
    }]

    rows = _build_unverified_completion_diffs(before_lines, after_lines)

    assert len(rows) == 1
    assert rows[0]["symbol"] == "370"
    assert rows[0]["amount_diff"] == 16.0
    assert rows[0]["diff_type"] == "השלמה ללא קובץ גשר סופי"


def test_legacy_items_exclude_any_matching_salary_event_status():
    class Result:
        def fetchall(self):
            return [{"source_table": "time_reports", "source_id": 10}]

    class Conn:
        def execute(self, query, params):
            assert "salary_impact_events" in query
            assert params[0:2] == (2026, 7)
            return Result()

    items = [
        {
            "id": 10,
            "item_type": "time_report",
            "person_id": 1,
            "employer_code": "400",
        },
        {
            "id": 11,
            "item_type": "payment_component",
            "person_id": 2,
            "employer_code": "400",
        },
    ]

    result = get_legacy_completion_items(
        Conn(),
        items,
        payment_year=2026,
        payment_month=7,
        company_code="400",
    )

    assert [item["id"] for item in result] == [11]


def test_approved_completion_rows_merge_events_and_legacy_without_duplication(monkeypatch):
    import services.gesher_difference as gesher_difference
    import services.salary_impact as salary_impact

    event_row = {
        "employer_code": "400",
        "employee_code": "000123",
        "person_id": 1,
        "person_name": "מדריך",
        "symbol": "317",
        "amount": 100.0,
        "quantity": 0.0,
        "source_symbols": "360",
    }
    legacy_row = {
        **event_row,
        "amount": -25.0,
        "source_symbols": "362",
    }
    captured = {}

    monkeypatch.setattr(
        salary_impact,
        "build_salary_impact_completion_rows",
        lambda *args, **kwargs: {
            "rows": [event_row],
            "events": [],
            "invalid_events": [],
        },
    )
    monkeypatch.setattr(
        gesher_difference,
        "get_payment_period_completions",
        lambda *args, **kwargs: {"items": [{"id": 11}]},
    )
    monkeypatch.setattr(
        gesher_difference,
        "get_legacy_completion_items",
        lambda *args, **kwargs: [{"id": 11}],
    )

    def fake_legacy(*args, **kwargs):
        captured.update(kwargs)
        return {
            "rows": [legacy_row],
            "blocks": [],
            "approved_files": [],
            "diffs": [],
        }

    monkeypatch.setattr(
        gesher_difference,
        "build_legacy_completion_gesher_rows_from_final_file",
        fake_legacy,
    )

    result = build_approved_completion_gesher_rows(
        object(),
        2026,
        7,
        company_code="400",
        person_ids={1},
    )

    assert [(row["symbol"], row["amount"]) for row in result["rows"]] == [("317", 75.0)]
    assert result["rows"][0]["source_symbols"] == "360, 362"
    assert captured["person_ids"] == {1}
    assert captured["completion_items"] == [{"id": 11}]


def test_current_gesher_lines_skip_zero_quantity_hour_rates_even_for_completion_deltas(monkeypatch):
    import services.gesher_difference as gesher_difference

    monkeypatch.setattr(
        gesher_difference.gesher_exporter,
        "load_export_config_from_db",
        lambda conn: {
            "360": ("calc100", "hours_100", "שעות רגילות"),
            "368": ("calc150_overtime", "hours_150", "שעות נוספות 150%"),
            "370": ("travel", "money", "נסיעות"),
        },
    )
    monkeypatch.setattr(
        gesher_difference.gesher_exporter,
        "get_export_options",
        lambda: {"export_zero_values": False, "min_amount": 0.01},
    )
    monkeypatch.setattr(
        gesher_difference.gesher_exporter,
        "get_minimum_wage",
        lambda conn, year, month: 34.4,
    )
    monkeypatch.setattr(
        gesher_difference,
        "_person_lookup",
        lambda conn: {"004495": {"person_id": 381, "person_name": "כהן הדסה", "employer_code": "400"}},
    )
    monkeypatch.setattr(
        gesher_difference,
        "calculate_monthly_summary",
        lambda *args, **kwargs: ([
            {
                "person_id": 381,
                "name": "כהן הדסה",
                "merav_code": "4495",
                "totals": {
                    "calc100": 120,
                    "calc150_overtime": 0,
                    "travel": 8.0,
                },
            }
        ], {}),
    )

    rows = build_current_gesher_lines(
        object(),
        2025,
        10,
        company_code="400",
        person_ids={381},
        include_negative_values=True,
    )

    assert [(row["symbol"], row["quantity"], row["rate"], row["amount"]) for row in rows] == [
        ("360", 2.0, 34.4, 68.8),
        ("370", 0.0, 8.0, 8.0),
    ]


def test_approved_completion_rows_include_unrelated_diffs_when_final_file_exists(monkeypatch):
    import services.gesher_difference as gesher_difference

    paid_lines = [{
        "employee_code": "000123",
        "person_id": 1,
        "person_name": "מדריך בדיקה",
        "employer_code": "400",
        "symbol": "370",
        "display_name": "נסיעות",
        "value_type": "money",
        "rate": 380.0,
        "quantity": 0.0,
        "amount": 380.0,
    }]
    current_without = [{
        **paid_lines[0],
        "rate": 236.0,
        "amount": 236.0,
    }]
    current_with = [
        current_without[0],
        {
            "employee_code": "000123",
            "person_id": 1,
            "person_name": "מדריך בדיקה",
            "employer_code": "400",
            "symbol": "360",
            "display_name": "שעות רגילות",
            "value_type": "money",
            "rate": 100.0,
            "quantity": 0.0,
            "amount": 100.0,
        },
    ]

    monkeypatch.setattr(gesher_difference, "get_payment_period_completions", lambda *args, **kwargs: {
        "items": [{
            "id": 10,
            "item_type": "time_report",
            "work_year": 2026,
            "work_month": 5,
            "employer_code": "400",
        }],
    })
    monkeypatch.setattr(gesher_difference, "list_gesher_export_files", lambda *args, **kwargs: [{
        "id": 99,
        "is_final": True,
        "is_cancelled": False,
    }])
    monkeypatch.setattr(gesher_difference, "get_gesher_export_file", lambda *args, **kwargs: {
        "id": 99,
        "filename": "gesher_400_2026_05.mrv",
        "is_final": True,
        "is_cancelled": False,
        "content": "dummy",
        "person_ids": [1],
    })
    monkeypatch.setattr(gesher_difference, "parse_gesher_file_lines", lambda content: paid_lines)
    monkeypatch.setattr(gesher_difference, "enrich_paid_lines", lambda conn, lines: lines)

    def fake_current_lines(*args, **kwargs):
        if kwargs.get("excluded_time_report_ids"):
            return current_without
        return current_with

    monkeypatch.setattr(gesher_difference, "build_current_gesher_lines", fake_current_lines)

    result = build_legacy_completion_gesher_rows_from_final_file(
        None,
        2026,
        6,
        company_code="400",
        housing_array_id=1,
    )

    assert result["blocks"] == []
    assert result["approved_files"][0]["has_unrelated_diffs"] is True
    assert [(row["symbol"], row["amount"]) for row in result["rows"]] == [
        ("253", -144.0),
        ("317", 100.0),
    ]


def test_completion_gesher_rows_treats_zero_quantity_rate_as_amount_diff():
    diffs = [{
            "employee_code": "000123",
            "person_name": "מדריך",
            "symbol": "370",
            "display_name": "נסיעות",
            "rate": 32.0,
            "quantity": 0.0,
            "amount": 32.0,
            "amount_diff": 32.0,
        }]

    gesher_rows = build_completion_gesher_rows(diffs)

    assert gesher_rows == [{
        "employer_code": "001",
        "employee_code": "000123",
        "person_id": None,
        "person_name": "מדריך",
        "symbol": "253",
        "amount": 32.0,
        "quantity": 0.0,
        "source_symbols": "370",
    }]


def test_build_completion_gesher_file_uses_gesher_money_format():
    rows = [
        {"employee_code": "000123", "symbol": "317", "amount": -20.64},
        {"employee_code": "000123", "symbol": "253", "amount": 32.0},
    ]

    content = build_completion_gesher_file(rows, 2026, 3, company_code="400")

    assert content.splitlines() == [
        "400 26 03      0",
        "000123 317 0000.00 -0020.64          201",
        "000123 253 0000.00 00032.00          201",
    ]
