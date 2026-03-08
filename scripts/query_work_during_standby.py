#!/usr/bin/env python3
"""Find guides with shifts where work overlaps standby (עבודה בזמן כוננות) in 02/2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.time_utils import get_shabbat_times_cache
from core.history import get_minimum_wage_for_month
from app_utils import get_daily_segments_data


def main() -> None:
    year, month = 2026, 2
    with get_conn() as conn:
        # Get people with reports in 02/2026
        person_rows = conn.execute("""
            SELECT DISTINCT p.id, p.name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s AND EXTRACT(MONTH FROM tr.date) = %s
            ORDER BY p.name
        """, (year, month)).fetchall()

        minimum_wage = get_minimum_wage_for_month(conn.conn, year, month)
        shabbat_cache = get_shabbat_times_cache(conn.conn)

        guides_with_overlap = []
        for row in person_rows:
            pid, pname = row["id"], row["name"]
            try:
                daily_segments, _ = get_daily_segments_data(
                    conn, pid, year, month, shabbat_cache, minimum_wage
                )
                for day in daily_segments:
                    cancelled = day.get("cancelled_standbys") or []
                    for c in cancelled:
                        reason = c.get("reason", "")
                        if "חפיפה" in reason:
                            guides_with_overlap.append((pname, day.get("date_obj"), reason))
                            break
            except Exception as e:
                print(f"Error for {pname}: {e}", file=sys.stderr)

    guides = sorted(set(g[0] for g in guides_with_overlap))
    out_path = Path(__file__).parent / "work_during_standby_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("מדריכים עם משמרת שכולל כוננות שבזמן הכוננות יש עבודה (02/2026):\n")
        f.write("(כוננות שמתבטלת/מצטמצמת בגלל חפיפה עם עבודה)\n\n")
        if guides:
            f.write("\n".join(guides))
            f.write(f"\n\nסה\"כ: {len(guides)} מדריכים\n")
            f.write("\nפירוט ימים:\n")
            for name, d, reason in sorted(guides_with_overlap, key=lambda x: (x[0], x[1])):
                f.write(f"  {name} | {d} | {reason}\n")
        else:
            f.write("(אין)\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
