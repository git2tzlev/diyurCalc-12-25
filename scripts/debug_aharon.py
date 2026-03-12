#!/usr/bin/env python3
"""Debug calculation for אטיאס אהרון 11/2025."""
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main() -> None:
    person_id = 82  # אטיאס אהרון
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

    out_path = Path(__file__).parent / "debug_aharon_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== חישוב אטיאס אהרון 11/2025 ===\n\n")
        f.write(f"שכר מינימום: {minimum_wage}\n")
        f.write(f"דיווחים: {len(reports)}\n\n")

        # Show report details
        f.write("=== דיווחים ===\n")
        for r in reports:
            f.write(f"  {r['date']} {r['start_time']}-{r['end_time']} "
                    f"shift={r['shift_type_id']} ({r['shift_name']})\n")

        # Monthly totals
        f.write(f"\n=== סיכום חודשי ===\n")
        calc100 = monthly_totals.get('calc100', 0) or 0
        calc125 = monthly_totals.get('calc125', 0) or 0
        calc150 = monthly_totals.get('calc150', 0) or 0
        calc175 = monthly_totals.get('calc175', 0) or 0
        calc200 = monthly_totals.get('calc200', 0) or 0
        calc_var = monthly_totals.get('calc_variable', 0) or 0
        vacation = monthly_totals.get('vacation_minutes', 0) or 0
        sick = monthly_totals.get('sick_minutes', 0) or 0

        f.write(f"  calc100: {calc100}min = {calc100/60:.2f}h  (תלוש: 138h)\n")
        f.write(f"  calc125: {calc125}min = {calc125/60:.2f}h  (תלוש: 9.25h)\n")
        f.write(f"  calc150: {calc150}min = {calc150/60:.2f}h  (תלוש: 16.5h)\n")
        f.write(f"  calc175: {calc175}min = {calc175/60:.2f}h\n")
        f.write(f"  calc200: {calc200}min = {calc200/60:.2f}h\n")
        f.write(f"  calc_variable: {calc_var}min = {calc_var/60:.2f}h\n")
        f.write(f"  vacation: {vacation}min = {vacation/60:.2f}h  (תלוש: 30h)\n")
        f.write(f"  sick: {sick}min = {sick/60:.2f}h\n")
        f.write(f"  standby: {monthly_totals.get('standby', 0)}\n")
        f.write(f"  travel: {monthly_totals.get('travel', 0)}\n")

        total_work_hours = (calc100 + calc125 + calc150 + calc175 + calc200 + calc_var) / 60
        f.write(f"\n  total work hours: {total_work_hours:.2f}h\n")
        f.write(f"  total work+vacation: {total_work_hours + vacation/60:.2f}h\n")
        f.write(f"  payslip total work: {138 + 9.25 + 16.5:.2f}h\n")

        # Per-chain breakdown
        f.write(f"\n=== Per-chain breakdown ===\n")
        chain_num = 0
        for day in daily_segments:
            chains = day.get("chains", [])
            if not chains:
                continue
            day_date = day.get("date")
            f.write(f"\n--- {day_date} ---\n")
            for chain in chains:
                chain_type = chain.get("type", "work")
                chain_num += 1
                c100 = chain.get("calc100", 0) or 0
                c125 = chain.get("calc125", 0) or 0
                c150 = chain.get("calc150", 0) or 0
                c175 = chain.get("calc175", 0) or 0
                c200 = chain.get("calc200", 0) or 0
                start_t = chain.get("start_time", "?")
                end_t = chain.get("end_time", "?")
                shift = chain.get("shift_name", "?")
                total_min = chain.get("total_minutes", 0) or 0
                eff_rate = chain.get("effective_rate", 0)
                is_special = chain.get("is_special_hourly", False)
                supplement = chain.get("hourly_wage_supplement", 0)

                f.write(f"  [{chain_type}] {start_t}-{end_t} ({total_min}min) "
                        f"shift={shift} rate={eff_rate}\n")
                if chain_type == "work":
                    f.write(f"    100%={c100}m 125%={c125}m 150%={c150}m "
                            f"175%={c175}m 200%={c200}m\n")
                    if is_special or supplement:
                        f.write(f"    special={is_special} supplement={supplement}\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
