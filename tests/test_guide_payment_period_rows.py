from routes.guide import _completion_rows_for_guide_report


def test_completion_rows_for_guide_report_show_the_exact_gesher_lines():
    rows = _completion_rows_for_guide_report([
        {
            "symbol": "253",
            "display_name": "הפרשי השלמות לא לפנסיה",
            "amount": 554.0,
            "quantity": 0.0,
            "rate": 554.0,
            "source_symbols": "370, 373",
        },
        {
            "symbol": "317",
            "display_name": "הפרשי השלמות לפנסיה",
            "amount": 1537.07,
            "quantity": 0.0,
            "rate": 1537.07,
            "source_symbols": "360",
        },
    ], payment_year=2026, payment_month=8)

    assert rows == [
        {
            "description": "סמל 253 - הפרשי השלמות לא לפנסיה",
            "detail": "סמלי מקור: 370, 373",
            "amount": 554.0,
            "note": "לתשלום ב-08/2026",
            "is_payment_period_completion": True,
        },
        {
            "description": "סמל 317 - הפרשי השלמות לפנסיה",
            "detail": "סמלי מקור: 360",
            "amount": 1537.07,
            "note": "לתשלום ב-08/2026",
            "is_payment_period_completion": True,
        },
    ]
