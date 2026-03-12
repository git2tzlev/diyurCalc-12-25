#!/usr/bin/env python3
"""Check chains structure for standby details."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

        for person_id, name in [(211, "Kramer"), (221, "Shaliach")]:
            print(f"\n{'='*60}")
            print(f"  {name} (id={person_id})")
            print(f"{'='*60}")
            
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

            daily_segments, _ = get_daily_segments_data(
                conn, person_id, year, month,
                shabbat_cache, minimum_wage,
                preloaded_reports=reports
            )

            for day in daily_segments:
                standby_pay = day.get("standby_payment", 0)
                if not standby_pay:
                    continue
                    
                print(f"\n  Day: {day.get('day', '?')}")
                print(f"  standby_payment: {standby_pay}")
                
                # Check chains for standby events
                chains = day.get("chains", [])
                for ci, chain in enumerate(chains):
                    print(f"  Chain {ci}: type={chain.get('type', '?')}")
                    events = chain.get("events", [])
                    for e in events:
                        etype = e.get("type", "?")
                        print(f"    {etype}: {e.get('start', '?')}-{e.get('end', '?')} "
                              f"rate_type={e.get('rate_type', 'N/A')} "
                              f"rate={e.get('rate', 'N/A')} "
                              f"shift_id={e.get('shift_id', 'N/A')}")

                # Check cancelled standbys
                cancelled = day.get("cancelled_standbys", [])
                for cs in cancelled:
                    print(f"  Cancelled standby: {cs}")

                # Look for standby_details key
                for k in day.keys():
                    if "standby" in k.lower() and k not in ("standby_payment", "cancelled_standbys"):
                        print(f"  {k}: {day[k]}")


if __name__ == "__main__":
    main()
