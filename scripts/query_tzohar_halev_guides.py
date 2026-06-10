#!/usr/bin/env python3
"""בודק אילו מהמדריכים מהרשימה עבדו במערך דיור צוהר הלב בחודש הרלוונטי."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401 - loads .env
from core.database import get_conn
from core.constants import TZOHAR_HALEV_HOUSING_ARRAY_ID


GUIDES_FROM_IMAGE = [
    ("אשכול נתן", 2025, 12),
    ("אברהמי דניאל", 2026, 1),
    ("פאקס דוד", 2026, 1),
    ("אטיאס אסתר", 2026, 2),
    ("אברהמי דניאל", 2026, 3),
    ("אבשלומוב יאיר", 2026, 3),
    ("גולדברג ישראל", 2026, 3),
    ("זביב אלעזר מנחם", 2026, 3),
    ("יהב רות", 2026, 3),
    ("פאקס דוד", 2026, 3),
    ("פילו סהר משה", 2026, 3),
    ("אהרונוביץ תהילה", 2026, 4),
    ("ברסקי שמחה בונם", 2026, 4),
    ("דהאן יאיר", 2026, 4),
    ("רוטמן יצחק שלמה זלמן", 2026, 4),
    ("שער רות", 2026, 5),
]


def main() -> None:
    results = []
    with get_conn() as conn:
        for name, year, month in GUIDES_FROM_IMAGE:
            rows = conn.execute(
                """
                SELECT DISTINCT ha.id, ha.name
                FROM time_reports tr
                JOIN people p ON p.id = tr.person_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE p.name = %s
                  AND EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                ORDER BY ha.name
                """,
                (name, year, month),
            ).fetchall()

            arrays = [(r["id"], r["name"]) for r in rows]
            in_tzohar = any(a[0] == TZOHAR_HALEV_HOUSING_ARRAY_ID for a in arrays)
            results.append((name, year, month, in_tzohar, arrays))

    out_path = Path(__file__).parent / "tzohar_halev_guides_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"בדיקת השתייכות למערך דיור צוהר הלב (id={TZOHAR_HALEV_HOUSING_ARRAY_ID})\n")
        f.write("=" * 80 + "\n\n")

        in_tzohar_list = [r for r in results if r[3]]
        not_in_tzohar_list = [r for r in results if not r[3]]

        f.write(f"== מדריכים מצוהר הלב ({len(in_tzohar_list)}) ==\n")
        for name, year, month, _, arrays in in_tzohar_list:
            arr_names = ", ".join(a[1] for a in arrays)
            f.write(f"  {name} ({month:02d}/{year}) - מערכים: {arr_names}\n")

        f.write(f"\n== מדריכים שלא עבדו בצוהר הלב באותו חודש ({len(not_in_tzohar_list)}) ==\n")
        for name, year, month, _, arrays in not_in_tzohar_list:
            if arrays:
                arr_names = ", ".join(a[1] for a in arrays)
                f.write(f"  {name} ({month:02d}/{year}) - מערכים: {arr_names}\n")
            else:
                f.write(f"  {name} ({month:02d}/{year}) - לא נמצאו דיווחים\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
