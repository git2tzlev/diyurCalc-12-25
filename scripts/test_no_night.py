#!/usr/bin/env python3
"""Test: what happens if we disable night shift detection? Does it match the payslip?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Monkey-patch: force all chains to NOT be night chains
import app_utils
_orig_calc = app_utils._calculate_chain_wages

def _patched_calc(chain_segments, shabbat_cache, minutes_offset=0, is_night_shift=False, is_jerusalem=False):
    """Always pass is_night_shift=False."""
    return _orig_calc(chain_segments, shabbat_cache, minutes_offset, False, is_jerusalem)

app_utils._calculate_chain_wages = _patched_calc

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main() -> None:
    person_id = 83
    year, month = 2025, 11

    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)

        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        reports = conn.execute("""
            SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   tr.apartment_id, st.name AS shift_name,
                   ap.housing_array_id, at.hourly_wage_supplement,
                   at.name AS apartment_type_name,
                   ap.apartment_type_id,
                   p.is_married, p.name as person_name,
                   ap.city AS apartment_city
            FROM time_reports tr
            LEFT JOIN shift_types st ON st.id = tr.shift_type_id
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
            LEFT JOIN people p ON p.id = tr.person_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
            ORDER BY tr.date, tr.start_time
        """, (person_id, f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01')).fetchall()

        daily_segments, person_name = get_daily_segments_data(
            conn, person_id, year, month,
            shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )

        monthly_totals = aggregate_daily_segments_to_monthly(
            conn, daily_segments, person_id, year, month, minimum_wage
        )

    calc100_h = round((monthly_totals.get('calc100', 0) or 0) / 60, 2)
    calc125_h = round((monthly_totals.get('calc125', 0) or 0) / 60, 2)
    calc150_h = round((monthly_totals.get('calc150', 0) or 0) / 60, 2)
    calc175_h = round((monthly_totals.get('calc175', 0) or 0) / 60, 2)
    calc200_h = round((monthly_totals.get('calc200', 0) or 0) / 60, 2)

    print(f"=== No Night Detection - Results ===")
    print(f"calc100: {calc100_h}h  (payslip: 130.98h, current: 132.25h)")
    print(f"calc125: {calc125_h}h  (payslip: 16.20h, current: 14.93h)")
    print(f"calc150: {calc150_h}h  (payslip: 39.30h)")
    print(f"calc175: {calc175_h}h  (payslip: 3.07h)")
    print(f"calc200: {calc200_h}h  (payslip: 11.20h)")
    print(f"\nDelta from payslip: 100%={calc100_h - 130.98:.2f}h, 125%={calc125_h - 16.20:.2f}h")


if __name__ == "__main__":
    main()
