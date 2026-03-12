#!/usr/bin/env python3
"""Debug per-chain breakdown for Esther 11/2025 to find the 76min discrepancy."""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main() -> None:
    person_id = 83  # אטיאס אסתר
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

    out_path = Path(__file__).parent / "debug_chains_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== Per-Chain Breakdown: אטיאס אסתר 11/2025 ===\n\n")

        total_c100 = 0
        total_c125 = 0
        total_c150 = 0
        total_c175 = 0
        total_c200 = 0
        chain_num = 0

        for day in daily_segments:
            day_date = day.get("date")
            chains = day.get("chains", [])

            if not chains:
                continue

            f.write(f"\n--- {day_date} ---\n")

            for chain in chains:
                chain_type = chain.get("type", "work")
                if chain_type != "work":
                    continue

                chain_num += 1
                c100 = chain.get("calc100", 0) or 0
                c125 = chain.get("calc125", 0) or 0
                c150 = chain.get("calc150", 0) or 0
                c175 = chain.get("calc175", 0) or 0
                c200 = chain.get("calc200", 0) or 0

                total_c100 += c100
                total_c125 += c125
                total_c150 += c150
                total_c175 += c175
                total_c200 += c200

                offset = chain.get("minutes_offset", 0) or 0
                is_night = chain.get("is_night", "?")
                effective_rate = chain.get("effective_rate", 0)
                is_special = chain.get("is_special_hourly", False)
                supplement = chain.get("hourly_wage_supplement", 0)
                start_time = chain.get("start_time", "?")
                end_time = chain.get("end_time", "?")
                chain_duration = chain.get("total_minutes", 0) or 0
                apartment = chain.get("apartment", "?")
                shift_name = chain.get("shift_name", "?")

                f.write(f"  Chain #{chain_num}: {start_time}-{end_time} "
                        f"({chain_duration}min, offset={offset})\n")
                f.write(f"    apartment={apartment}, shift={shift_name}\n")
                f.write(f"    is_night={is_night}, rate={effective_rate}, "
                        f"supplement={supplement}, special={is_special}\n")
                f.write(f"    100%={c100}min ({c100/60:.2f}h), "
                        f"125%={c125}min ({c125/60:.2f}h), "
                        f"150%={c150}min ({c150/60:.2f}h)\n")
                if c175 or c200:
                    f.write(f"    175%={c175}min, 200%={c200}min\n")

                # Show segment details if available
                seg_detail = chain.get("segments_detail", [])
                if seg_detail:
                    f.write(f"    Segments:\n")
                    for seg_start, seg_end, seg_label, is_shabbat in seg_detail:
                        f.write(f"      {seg_start}-{seg_end} ({seg_end-seg_start}min) "
                                f"{seg_label} {'[SHABBAT]' if is_shabbat else ''}\n")

        f.write(f"\n\n=== TOTALS ===\n")
        f.write(f"  Total calc100: {total_c100}min = {total_c100/60:.2f}h\n")
        f.write(f"  Total calc125: {total_c125}min = {total_c125/60:.2f}h\n")
        f.write(f"  Total calc150: {total_c150}min = {total_c150/60:.2f}h\n")
        f.write(f"  Total calc175: {total_c175}min = {total_c175/60:.2f}h\n")
        f.write(f"  Total calc200: {total_c200}min = {total_c200/60:.2f}h\n")
        f.write(f"\n  Payslip expected:\n")
        f.write(f"    calc100: 130.98h = {130.98*60:.1f}min\n")
        f.write(f"    calc125: 16.20h = {16.20*60:.1f}min\n")
        f.write(f"  Difference: {total_c100 - 130.98*60:.1f}min in calc100, "
                f"{total_c125 - 16.20*60:.1f}min in calc125\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
