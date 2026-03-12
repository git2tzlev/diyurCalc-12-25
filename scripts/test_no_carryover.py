#!/usr/bin/env python3
"""Test: what happens if we disable chain carryover between days?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

        # Zero out all carryover data in daily segments before aggregation
        # This simulates the system before carryover was implemented
        for day in daily_segments:
            for chain in day.get("chains", []):
                # Don't modify - just check what the data looks like
                pass

        monthly_totals = aggregate_daily_segments_to_monthly(
            conn, daily_segments, person_id, year, month, minimum_wage
        )

    calc100 = monthly_totals.get('calc100', 0) or 0
    calc125 = monthly_totals.get('calc125', 0) or 0
    calc150 = monthly_totals.get('calc150', 0) or 0
    calc175 = monthly_totals.get('calc175', 0) or 0
    calc200 = monthly_totals.get('calc200', 0) or 0

    print(f"=== Current Results ===")
    print(f"calc100: {calc100}min = {calc100/60:.2f}h  (payslip: 130.98h)")
    print(f"calc125: {calc125}min = {calc125/60:.2f}h  (payslip: 16.20h)")
    print(f"calc150: {calc150}min = {calc150/60:.2f}h  (payslip: 39.30h)")

    # Now examine chain carryover patterns
    print(f"\n=== Analyzing carryover between days ===")
    prev_day = None
    for day in daily_segments:
        chains = day.get("chains", [])
        if not chains:
            prev_day = day
            continue

        day_date = day.get("date")
        # Check carryover from previous day
        carryover = day.get("prev_day_carryover", 0)
        night_carryover = day.get("prev_day_night_minutes", 0)

        # Sum calc values for this day
        day_c100 = sum(c.get("calc100", 0) or 0 for c in chains if c.get("type") == "work")
        day_c125 = sum(c.get("calc125", 0) or 0 for c in chains if c.get("type") == "work")
        day_c150 = sum(c.get("calc150", 0) or 0 for c in chains if c.get("type") == "work")
        total = day_c100 + day_c125 + day_c150

        if day_c125 > 0 or carryover:
            print(f"  {day_date}: carryover={carryover}, night_carry={night_carryover}")
            print(f"    100%={day_c100}min, 125%={day_c125}min, 150%={day_c150}min (total={total})")
            for i, chain in enumerate(chains):
                if chain.get("type") != "work":
                    continue
                c100 = chain.get("calc100", 0) or 0
                c125 = chain.get("calc125", 0) or 0
                c150 = chain.get("calc150", 0) or 0
                offset = chain.get("minutes_offset", 0) or 0
                is_night = chain.get("is_night", "?")
                print(f"      chain[{i}]: {chain.get('start_time', '?')}-{chain.get('end_time', '?')} "
                      f"100%={c100} 125%={c125} 150%={c150} offset={offset} night={is_night}")

        prev_day = day


if __name__ == "__main__":
    main()
