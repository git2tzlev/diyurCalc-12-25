#!/usr/bin/env python3
"""
Debug standby discrepancy for 11/2025.

Guides affected:
- Kramer Riva (emp=9027) - Payslip standby=575 (2x287.50), System=505
- Shaliach Tzibur Lashem (emp=3893) - Payslip standby=565 (7x80.71), System=490
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def calc_guide(conn, person_id, name, year, month, shabbat_cache, minimum_wage, payslip_standby):
    """Calculate and print standby results for a guide."""
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

    standby_payment = monthly_totals.get("standby_payment", 0)
    standby_count = monthly_totals.get("standby", 0)
    diff = payslip_standby - standby_payment

    print()
    print("=" * 60)
    print(f"  {name} (person_id={person_id})")
    print("=" * 60)
    print(f"  Reports: {len(reports)}")
    for r in reports:
        print(f"    {r['date']} {r['start_time']}-{r['end_time']} "
              f"shift={r['shift_type_id']} ({r['shift_name']}) "
              f"apt_type={r['apartment_type_id']} married={r['is_married']}")
    print(f"  Standby count:   {standby_count}")
    print(f"  Standby payment: {standby_payment:.2f}")
    print(f"  Payslip standby: {payslip_standby:.2f}")
    print(f"  Difference:      {diff:.2f}")
    print()

    for day in daily_segments:
        day_date = day.get("day", "?")
        day_standby = day.get("standby_payment", 0)
        if day_standby:
            cancelled = day.get("cancelled_standbys", [])
            print(f"  {day_date}: standby_pay={day_standby:.2f}")
            for cs in cancelled:
                print(f"    cancelled: {cs}")


def main():
    year, month = 2025, 11

    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)
        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        people = conn.execute(
            "SELECT id, name, meirav_code FROM people WHERE meirav_code IN ('9027', '3893')"
        ).fetchall()
        print(f"Minimum wage: {minimum_wage}")
        print(f"\nPeople found:")
        person_map = {}
        for p in people:
            print(f"  id={p['id']}, name={p['name']}, meirav_code={p['meirav_code']}")
            person_map[p['meirav_code']] = p['id']

        if '9027' in person_map:
            calc_guide(conn, person_map['9027'], 'Kramer Riva (9027)',
                      year, month, shabbat_cache, minimum_wage, 575.0)

        if '3893' in person_map:
            calc_guide(conn, person_map['3893'], 'Shaliach Tzibur Lashem (3893)',
                      year, month, shabbat_cache, minimum_wage, 565.0)


if __name__ == "__main__":
    main()
