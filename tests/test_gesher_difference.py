from services.gesher_difference import compare_line_sets, parse_gesher_file_lines


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
