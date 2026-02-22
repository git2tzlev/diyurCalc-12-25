"""
סקריפט השוואת גרסאות - מריץ חישוב לעובד 7381 ומדפיס תוצאות.
משמש להשוואה בין גרסאות קוד שונות.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load .env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

from core.database import get_conn
from core.logic import calculate_monthly_summary, calculate_person_monthly_totals, get_shabbat_times_cache
from core.history import get_minimum_wage_for_month

FIELDS = [
    'calc100', 'calc125', 'calc150_overtime', 'calc150_shabbat_100',
    'calc150_shabbat_50', 'calc175', 'calc200',
    'standby_hours', 'travel_hours', 'special_hours',
    'standby_payment', 'travel', 'vacation_payment', 'sick_payment',
    'extras', 'professional_support',
    'payment_calc100', 'payment_calc125', 'payment_calc150',
    'payment_calc175', 'payment_calc200', 'payment_calc_variable',
    'rounded_total', 'total_payment', 'gesher_total', 'display_total',
]

HOUR_FIELDS = ['calc100', 'calc125', 'calc150_overtime', 'calc150_shabbat_100',
               'calc150_shabbat_50', 'calc175', 'calc200',
               'standby_hours', 'travel_hours', 'special_hours']

def print_totals(totals, label):
    print(f"\n=== {label} ===")
    for f in FIELDS:
        v = totals.get(f, 0) or 0
        if f in HOUR_FIELDS:
            print(f"{f}: {v} min = {round(v/60, 2)} hrs")
        else:
            print(f"{f}: {v}")
    total_hrs = sum(round((totals.get(f, 0) or 0) / 60, 2) for f in HOUR_FIELDS[:7])
    print(f"TOTAL_WORK_HOURS: {total_hrs}")

def main():
    with get_conn() as conn:
        raw_conn = conn.conn if hasattr(conn, 'conn') else conn

        # Path 1: calculate_monthly_summary (bulk)
        summary_data, _ = calculate_monthly_summary(raw_conn, 2026, 1)
        for r in summary_data:
            mc = str(r.get('merav_code', r.get('meirav_code', '')))
            pid = r.get('person_id', 0)
            if mc == '7381' or pid == 78:
                print_totals(r.get('totals', {}), "BULK PATH (calculate_monthly_summary)")
                break
        else:
            print("Employee 7381 NOT FOUND in bulk path")

        # Path 2: calculate_person_monthly_totals (individual - used by gesher export)
        shabbat_cache = get_shabbat_times_cache(raw_conn)
        minimum_wage = get_minimum_wage_for_month(raw_conn, 2026, 1)
        totals2 = calculate_person_monthly_totals(
            raw_conn, 78, 2026, 1, shabbat_cache, minimum_wage
        )
        print_totals(totals2, "INDIVIDUAL PATH (calculate_person_monthly_totals)")

if __name__ == '__main__':
    main()
