#!/usr/bin/env python3
"""מציאה מדויקת של מקור 70 הדק' בין 162.04 ל-163.2."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.database import get_conn
from core.logic import get_shabbat_times_cache
from core.shift_hours import calculate_shift_hours, calculate_tagbur_segments
from app_utils import get_daily_segments_data


def main() -> None:
    year, month = 2026, 3
    out_path = Path(__file__).parent / "debug_yifrach_diff_result.txt"

    with get_conn() as conn:
        person = conn.execute(
            "SELECT id, name FROM people WHERE name LIKE %s",
            ("%יפרח%תהיל%",),
        ).fetchone()
        person_id = person["id"]

        shabbat_cache = get_shabbat_times_cache(conn)
        wage = conn.execute(
            "SELECT hourly_rate FROM minimum_wage_rates ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        minimum_wage = float(wage["hourly_rate"]) / 100

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
            ph = ",".join(["%s"] * len(shift_ids))
            for s in conn.execute(
                f"SELECT shift_type_id, segment_type, start_time, end_time "
                f"FROM shift_time_segments WHERE shift_type_id IN ({ph}) "
                f"ORDER BY shift_type_id, order_index",
                tuple(shift_ids),
            ).fetchall():
                segments_by_shift.setdefault(s["shift_type_id"], []).append(s)

        daily_segments, _ = get_daily_segments_data(
            conn, person_id, year, month,
            shabbat_cache, minimum_wage,
            preloaded_reports=reports,
        )

    # חישוב מסלול 1 (משמרות) ו-2 (רצפים) עבור כל יום
    per_day_shift = {}
    for r in reports:
        shift_name = (r["shift_type_name"] or "").replace("משמרת ", "")
        is_tagbor = "תגבור" in shift_name
        seg_list = segments_by_shift.get(r["shift_type_id"], [])

        wh = 0.0
        if is_tagbor and seg_list and r["start_time"] and r["end_time"]:
            for sd in calculate_tagbur_segments(
                r["start_time"], r["end_time"],
                r["shift_type_id"], segments_by_shift,
            ):
                wh += sd["work_hours"]
        elif r["start_time"] and r["end_time"]:
            wh, _ = calculate_shift_hours(
                r["start_time"], r["end_time"],
                r["shift_type_id"], segments_by_shift,
                apartment_type_id=r.get("apartment_type_id"),
                housing_array_id=r.get("housing_array_id"),
            )
        per_day_shift.setdefault(r["date"], 0.0)
        per_day_shift[r["date"]] += wh

    # סיכום הרצפים לכל יום (ללא מחלה/חופשה)
    per_day_chain_work = {}
    for day in daily_segments:
        d = day.get("date_obj")
        work_min = 0
        for ch in day.get("chains", []):
            if ch.get("type") == "work":
                work_min += ch.get("total_minutes", 0) or 0
        per_day_chain_work[d] = work_min

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=== השוואה יום-יום (רק שעות עבודה, בלי מחלה/חופשה/כוננות) ===\n\n")
        f.write(f"{'תאריך':<12} {'משמרות (h)':>12} {'רצפים (h)':>12} {'הפרש (min)':>12}\n")
        all_dates = sorted(set(list(per_day_shift.keys()) + list(per_day_chain_work.keys())))
        total_diff = 0.0
        for d in all_dates:
            sh = per_day_shift.get(d, 0.0)
            ch = per_day_chain_work.get(d, 0) / 60.0
            diff_min = (ch - sh) * 60
            total_diff += diff_min
            mark = "  <--" if abs(diff_min) > 0.5 else ""
            f.write(f"{str(d):<12} {sh:>12.4f} {ch:>12.4f} {diff_min:>12.2f}{mark}\n")
        f.write(f"\n{'סה\"כ':<12} {sum(per_day_shift.values()):>12.4f} "
                f"{sum(per_day_chain_work.values())/60:>12.4f} "
                f"{total_diff:>12.2f}\n")

        # פירוט מלא של הימים עם הפרש
        f.write("\n\n=== פירוט מלא של רצפים ב-ימים עם הפרש ===\n")
        for day in daily_segments:
            d = day.get("date_obj")
            sh = per_day_shift.get(d, 0.0)
            ch_min = per_day_chain_work.get(d, 0)
            diff_min = ch_min - sh * 60
            if abs(diff_min) <= 0.5:
                continue
            f.write(f"\n--- {d} (משמרות={sh:.2f}h | רצפים={ch_min/60:.2f}h | "
                    f"הפרש={diff_min:.2f}min) ---\n")
            for ch in day.get("chains", []):
                if ch.get("type") != "work":
                    continue
                f.write(f"  [{ch.get('type')}] {ch.get('start_time')}-{ch.get('end_time')} "
                        f"min={ch.get('total_minutes')} | "
                        f"100/125/150shabbat/150ot/175/200="
                        f"{ch.get('calc100',0)}/{ch.get('calc125',0)}/"
                        f"{ch.get('calc150_shabbat',0)}/{ch.get('calc150_overtime',0)}/"
                        f"{ch.get('calc175',0)}/{ch.get('calc200',0)}\n")
                for seg in ch.get("segments", []):
                    f.write(f"      seg: {seg}\n")

    print(f"Results: {out_path}")


if __name__ == "__main__":
    main()
