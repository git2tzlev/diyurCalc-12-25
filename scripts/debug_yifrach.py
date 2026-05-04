#!/usr/bin/env python3
"""השוואת חישוב סה"כ שעות בשני המסלולים עבור יפרח תהילה 03/2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from core.shift_hours import calculate_shift_hours, calculate_tagbur_segments
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly


def main() -> None:
    year, month = 2026, 3

    with get_conn() as conn:
        person_row = conn.execute(
            "SELECT id, name FROM people WHERE name LIKE %s",
            ("%יפרח%תהיל%",),
        ).fetchone()
        if not person_row:
            print("לא נמצא")
            return
        person_id = person_row["id"]
        person_name = person_row["name"]

        shabbat_cache = get_shabbat_times_cache(conn)
        wage_row = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(wage_row["hourly_rate"]) / 100 if wage_row else 34.40

        reports = conn.execute("""
            SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   tr.apartment_id, st.name AS shift_name, st.name AS shift_type_name,
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

        shift_ids = list({r["shift_type_id"] for r in reports if r["shift_type_id"]})
        segments_by_shift = {}
        if shift_ids:
            placeholders = ",".join(["%s"] * len(shift_ids))
            segs = conn.execute(f"""
                SELECT shift_type_id, segment_type, start_time, end_time
                FROM shift_time_segments
                WHERE shift_type_id IN ({placeholders})
                ORDER BY shift_type_id, order_index
            """, tuple(shift_ids)).fetchall()
            for s in segs:
                segments_by_shift.setdefault(s["shift_type_id"], []).append(s)

        daily_segments, _ = get_daily_segments_data(
            conn, person_id, year, month,
            shabbat_cache, minimum_wage,
            preloaded_reports=reports,
        )

        monthly_totals = aggregate_daily_segments_to_monthly(
            conn, daily_segments, person_id, year, month, minimum_wage
        )

    out_path = Path(__file__).parent / "debug_yifrach_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== {person_name} - {month:02d}/{year} ===\n\n")
        f.write(f"דיווחים: {len(reports)}\n\n")

        # ===== מסלול 1: דוח משמרות (כמו ב-routes/guide.py — תגבור עם calculate_tagbur_segments) =====
        f.write("=== מסלול 1: דוח משמרות (משחזר routes/guide.py) ===\n")
        per_shift_total = 0.0
        per_day_shift = {}
        for r in reports:
            shift_name_clean = (r["shift_type_name"] or "").replace("משמרת ", "")
            is_tagbor = "תגבור" in shift_name_clean
            seg_list = segments_by_shift.get(r["shift_type_id"], [])

            wh = sh = 0.0
            if is_tagbor and seg_list and r["start_time"] and r["end_time"]:
                tagbur_segs = calculate_tagbur_segments(
                    r["start_time"], r["end_time"],
                    r["shift_type_id"], segments_by_shift,
                )
                for seg_data in tagbur_segs:
                    wh += seg_data["work_hours"]
                    sh += seg_data["standby_hours"]
                f.write(f"  {r['date']} {r['start_time']}-{r['end_time']} "
                        f"shift={r['shift_type_id']} ({r['shift_type_name']}) [TAGBUR] "
                        f"-> work={wh:.3f}h standby={sh:.3f}h\n")
                for sd in tagbur_segs:
                    f.write(f"      seg {sd['display_start']}-{sd['display_end']} "
                            f"work={sd['work_hours']} standby={sd['standby_hours']}\n")
            else:
                if r["start_time"] and r["end_time"]:
                    wh, sh = calculate_shift_hours(
                        r["start_time"], r["end_time"],
                        r["shift_type_id"], segments_by_shift,
                        apartment_type_id=r.get("apartment_type_id"),
                        housing_array_id=r.get("housing_array_id"),
                    )
                f.write(f"  {r['date']} {r['start_time']}-{r['end_time']} "
                        f"shift={r['shift_type_id']} ({r['shift_type_name']}) "
                        f"-> work={wh:.3f}h standby={sh:.3f}h\n")

            per_shift_total += wh
            per_day_shift.setdefault(r["date"], 0.0)
            per_day_shift[r["date"]] += wh

        f.write(f"\n>> סה\"כ שעות עבודה (מסלול 1): {per_shift_total:.4f}h\n\n")

        # ===== מסלול 2: סיכום חודשי (chains) =====
        f.write("=== מסלול 2: סיכום חודשי (chains) ===\n")
        per_day_chain = {}  # date -> minutes
        for day in daily_segments:
            d = day.get("date_obj")
            tmin = day.get("total_minutes_no_standby", 0) or 0
            per_day_chain[d] = tmin

        raw_total = sum(per_day_chain.values())
        non_eff_sick = monthly_totals.get("non_effective_sick_minutes", 0)
        eff_sick = monthly_totals.get("effective_sick_minutes", 0)
        sick = monthly_totals.get("sick_minutes", 0)
        vac = monthly_totals.get("vacation_minutes", 0)
        total_hours_min = monthly_totals.get("total_hours", 0)

        f.write(f"  raw_total_minutes (sum of total_minutes_no_standby): "
                f"{raw_total} = {raw_total/60:.4f}h\n")
        f.write(f"  sick (raw): {sick}min   effective: {eff_sick}min   "
                f"non-effective: {non_eff_sick}min\n")
        f.write(f"  vacation: {vac}min\n")
        f.write(f"  total_hours (=raw - non_effective_sick): {total_hours_min} = "
                f"{total_hours_min/60:.4f}h\n")
        without_sick_vac = (total_hours_min - eff_sick - vac)
        f.write(f"  ללא מחלה/חופשה: {without_sick_vac}min = "
                f"{without_sick_vac/60:.4f}h\n\n")

        # ===== השוואה יום-יום =====
        f.write("=== השוואה יום-יום ===\n")
        f.write(f"{'יום':<12} {'משמרות (h)':>12} {'רצפים-no_sb (h)':>16} "
                f"{'הפרש (min)':>12}\n")
        all_dates = sorted(set(list(per_day_shift.keys()) + list(per_day_chain.keys())))
        total_diff = 0.0
        for d in all_dates:
            sh_h = per_day_shift.get(d, 0.0)
            ch_h = per_day_chain.get(d, 0) / 60.0
            diff_min = (ch_h - sh_h) * 60
            total_diff += diff_min
            marker = "  <-- " if abs(diff_min) > 0.5 else ""
            f.write(f"{str(d):<12} {sh_h:>12.4f} {ch_h:>16.4f} "
                    f"{diff_min:>12.2f}{marker}\n")
        f.write(f"\nסה\"כ הפרש: {total_diff:.2f} דק' = {total_diff/60:.4f}h\n")

        # ===== פירוט רצפים לימים עם הפרש =====
        f.write("\n=== פירוט רצפים לימים עם הפרש ===\n")
        for day in daily_segments:
            d = day.get("date_obj")
            sh_h = per_day_shift.get(d, 0.0)
            ch_h = per_day_chain.get(d, 0) / 60.0
            diff_min = (ch_h - sh_h) * 60
            if abs(diff_min) <= 0.5:
                continue
            f.write(f"\n--- {d} (הפרש {diff_min:.2f} דק') ---\n")
            f.write(f"  total_minutes: {day.get('total_minutes')}, "
                    f"total_minutes_no_standby: {day.get('total_minutes_no_standby')}\n")
            for ch in day.get("chains", []):
                f.write(f"  [{ch.get('type')}] {ch.get('start_time')}-{ch.get('end_time')} "
                        f"min={ch.get('total_minutes')} shift={ch.get('shift_name')} "
                        f"100/125/150/175/200={ch.get('calc100')}/{ch.get('calc125')}/"
                        f"{ch.get('calc150')}/{ch.get('calc175')}/{ch.get('calc200')}\n")
                for seg in ch.get("segments", []):
                    f.write(f"      seg: {seg}\n")
            f.write(f"  דיווחים גולמיים ביום:\n")
            for r in reports:
                if r["date"] == d:
                    f.write(f"      {r['start_time']}-{r['end_time']} "
                            f"shift={r['shift_type_id']} ({r['shift_type_name']})\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
