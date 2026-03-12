#!/usr/bin/env python3
"""Deep debug of standby calculation per day."""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Enable DEBUG logging for standby
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("app_utils")
logger.setLevel(logging.DEBUG)

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


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
        person_map = {}
        for p in people:
            person_map[p['meirav_code']] = p['id']

        # Test Kramer only
        person_id = person_map.get('9027')
        if person_id:
            print(f"\n=== Kramer Riva (id={person_id}) ===")
            
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

            for r in reports:
                print(f"  {r['date']} {r['start_time']}-{r['end_time']} "
                      f"shift={r['shift_type_id']} ({r['shift_name']}) "
                      f"apt_type={r['apartment_type_id']}")

            daily_segments, _ = get_daily_segments_data(
                conn, person_id, year, month,
                shabbat_cache, minimum_wage,
                preloaded_reports=reports
            )

            for day in daily_segments:
                day_date = day.get("date", "?")
                events = day.get("events", [])
                has_standby = any(e.get("type") in ("standby", "cancelled_standby") for e in events)
                if has_standby or day.get("standby_payment", 0):
                    print(f"\n  Day: {day_date}")
                    print(f"    standby_payment: {day.get('standby_payment', 0)}")
                    print(f"    cancelled_standby_payment: {day.get('cancelled_standby_payment', 0)}")
                    for e in events:
                        print(f"    event: type={e.get('type')}, "
                              f"{e.get('start_time','?')}-{e.get('end_time','?')}, "
                              f"rate={e.get('rate', 'N/A')}, "
                              f"shift_id={e.get('shift_id', 'N/A')}, "
                              f"label={e.get('label', '')}, "
                              f"seg_id={e.get('segment_id', 'N/A')}, "
                              f"apt_type={e.get('apartment_type_id', 'N/A')}")

        # Test Shaliach
        person_id = person_map.get('3893')
        if person_id:
            print(f"\n\n=== Shaliach Tzibur (id={person_id}) ===")
            
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

            for r in reports:
                print(f"  {r['date']} {r['start_time']}-{r['end_time']} "
                      f"shift={r['shift_type_id']} ({r['shift_name']}) "
                      f"apt_type={r['apartment_type_id']}")

            daily_segments, _ = get_daily_segments_data(
                conn, person_id, year, month,
                shabbat_cache, minimum_wage,
                preloaded_reports=reports
            )

            for day in daily_segments:
                day_date = day.get("date", "?")
                events = day.get("events", [])
                has_standby = any(e.get("type") in ("standby", "cancelled_standby") for e in events)
                if has_standby or day.get("standby_payment", 0):
                    print(f"\n  Day: {day_date}")
                    print(f"    standby_payment: {day.get('standby_payment', 0)}")
                    print(f"    cancelled_standby_payment: {day.get('cancelled_standby_payment', 0)}")
                    for e in events:
                        print(f"    event: type={e.get('type')}, "
                              f"{e.get('start_time','?')}-{e.get('end_time','?')}, "
                              f"rate={e.get('rate', 'N/A')}, "
                              f"shift_id={e.get('shift_id', 'N/A')}, "
                              f"label={e.get('label', '')}, "
                              f"seg_id={e.get('segment_id', 'N/A')}, "
                              f"apt_type={e.get('apartment_type_id', 'N/A')}")


if __name__ == "__main__":
    main()
