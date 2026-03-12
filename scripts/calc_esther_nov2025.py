#!/usr/bin/env python3
"""Calculate Esther's monthly totals for 11/2025 and show detailed breakdown."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main() -> None:
    person_id = 83  # אטיאס אסתר
    year, month = 2025, 11

    with get_conn() as conn:
        raw_conn = conn  # keep the wrapper, it has .execute
        shabbat_cache = get_shabbat_times_cache(conn)

        # Get minimum wage
        row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(row["hourly_rate"]) / 100 if row else 34.40

        # Get reports
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
        """, (person_id, f'{year}-{month:02d}-01', f'{year}-{month+1:02d}-01' if month < 12 else f'{year+1}-01-01')).fetchall()

        print(f"Reports count: {len(reports)}")
        print(f"Minimum wage: {minimum_wage}")

        # Run the calculation
        daily_segments, person_name = get_daily_segments_data(
            raw_conn, person_id, year, month,
            shabbat_cache, minimum_wage,
            preloaded_reports=reports
        )

        monthly_totals = aggregate_daily_segments_to_monthly(
            raw_conn, daily_segments, person_id, year, month, minimum_wage
        )

    out_path = Path(__file__).parent / "calc_esther_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== חישוב אטיאס אסתר 11/2025 ===\n\n")
        f.write(f"שכר מינימום: {minimum_wage}\n")
        f.write(f"דיווחים: {len(reports)}\n\n")

        # Key totals
        keys = [
            ('calc100', 'שעות 100%'),
            ('calc125', 'שעות 125%'),
            ('calc150', 'שעות 150%'),
            ('calc150_shabbat', 'שעות שבת 150%'),
            ('calc150_shabbat_100', 'שבת בסיס 100%'),
            ('calc150_shabbat_50', 'שבת תוספת 50%'),
            ('calc175', 'שעות שבת 175%'),
            ('calc200', 'שעות שבת 200%'),
            ('calc_variable', 'תעריף משתנה'),
            ('payment_calc_variable', 'תשלום תעריף משתנה'),
            ('standby', 'כוננויות'),
            ('standby_payment', 'תשלום כוננויות'),
            ('travel', 'נסיעות'),
            ('extras', 'תוספות'),
        ]

        f.write("=== סיכום חודשי ===\n")
        for key, label in keys:
            val = monthly_totals.get(key, 0) or 0
            if key in ('travel', 'extras', 'standby_payment', 'payment_calc_variable'):
                f.write(f"  {label}: {val:.2f} ש\"ח\n")
            elif key == 'standby':
                f.write(f"  {label}: {val} ימים\n")
            else:
                hours = val / 60 if val else 0
                f.write(f"  {label}: {val} דקות = {hours:.2f} שעות\n")

        f.write(f"\n=== השוואה למספרים בתלוש ===\n")
        calc100_h = round((monthly_totals.get('calc100', 0) or 0) / 60, 2)
        calc125_h = round((monthly_totals.get('calc125', 0) or 0) / 60, 2)
        calc150_h = round((monthly_totals.get('calc150', 0) or 0) / 60, 2)
        calc175_h = round((monthly_totals.get('calc175', 0) or 0) / 60, 2)
        calc200_h = round((monthly_totals.get('calc200', 0) or 0) / 60, 2)
        shabbat100_h = round((monthly_totals.get('calc150_shabbat_100', 0) or 0) / 60, 2)

        f.write(f"  קוד 360 (100%): {calc100_h}h  (תלוש: 130.98h)\n")
        f.write(f"  קוד 362 (שבת בסיס): {shabbat100_h}h  (תלוש: 34.82h)\n")
        f.write(f"  קוד 366 (125%): {calc125_h}h  (תלוש: 16.20h)\n")
        f.write(f"  קוד 368 (150%): {round((monthly_totals.get('calc150_overtime', 0) or 0) / 60, 2)}h  (תלוש: 39.30h)\n")
        f.write(f"  קוד 382 (175%): {calc175_h}h  (תלוש: 3.07h)\n")
        f.write(f"  קוד 434 (200%): {calc200_h}h  (תלוש: 11.20h)\n")

        # Variable rates detail
        var_rates = monthly_totals.get('variable_rates', {})
        if var_rates:
            f.write(f"\n=== תעריפים משתנים ===\n")
            for rate, data in var_rates.items():
                f.write(f"  תעריף {rate}: {data}\n")

        # average_base_rate
        f.write(f"\n  average_base_rate: {monthly_totals.get('average_base_rate', 'N/A')}\n")

        # Gesher total
        f.write(f"\n  gesher_total: {monthly_totals.get('gesher_total', 0)}\n")
        f.write(f"  display_total: {monthly_totals.get('display_total', 0)}\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
