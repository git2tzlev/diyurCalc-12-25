from services.gesher_difference import (
    _build_unverified_completion_diffs,
    build_completion_gesher_file,
    build_completion_gesher_rows,
    compare_line_sets,
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
        {"employee_code": "000123", "person_name": "מדריך", "symbol": "767", "amount_diff": 900.0, "employer_code": "400"},
    ]

    rows = build_completion_gesher_rows(diffs)

    assert [(row["employer_code"], row["symbol"], row["amount"]) for row in rows] == [
        ("400", "243", 500.0),
        ("400", "253", 42.0),
        ("001", "317", 100.0),
        ("400", "317", 25.5),
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
