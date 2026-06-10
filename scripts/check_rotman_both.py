#!/usr/bin/env python3
"""בודק כמה דיווחים יש לכל אחת משתי הרשומות של רוטמן יצחק שלמה זלמן."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn

ROTMAN_IDS = (212, 334)


def main() -> None:
    out = []
    with get_conn() as conn:
        for pid in ROTMAN_IDS:
            person = conn.execute(
                "SELECT id, name, id_number, is_married, type FROM people WHERE id = %s",
                (pid,),
            ).fetchone()
            out.append("=" * 80)
            out.append(f"רשומת אדם id={pid}")
            if not person:
                out.append("  לא קיימת רשומה כזו")
                continue
            out.append(f"  שם='{person['name']}'  ת\"ז={person['id_number']}  "
                       f"is_married={person['is_married']}  type={person['type']}")

            tr_rows = conn.execute(
                """
                SELECT COUNT(*)   AS total,
                       MIN(date)  AS first_date,
                       MAX(date)  AS last_date
                FROM time_reports
                WHERE person_id = %s
                """,
                (pid,),
            ).fetchone()
            out.append(f"  time_reports: total={tr_rows['total']}  "
                       f"first={tr_rows['first_date']}  last={tr_rows['last_date']}")

            by_array = conn.execute(
                """
                SELECT ha.id   AS array_id,
                       ha.name AS array_name,
                       COUNT(*) AS cnt,
                       MIN(tr.date) AS first_date,
                       MAX(tr.date) AS last_date
                FROM time_reports tr
                JOIN apartments ap     ON ap.id = tr.apartment_id
                JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE tr.person_id = %s
                GROUP BY ha.id, ha.name
                ORDER BY ha.name
                """,
                (pid,),
            ).fetchall()
            for row in by_array:
                out.append(f"    array='{row['array_name']}' (id={row['array_id']})  "
                           f"דיווחים={row['cnt']}  "
                           f"מ-{row['first_date']} עד {row['last_date']}")

            pc_rows = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       MIN(date) AS first_date,
                       MAX(date) AS last_date
                FROM payment_components
                WHERE person_id = %s
                """,
                (pid,),
            ).fetchone()
            out.append(f"  payment_components: total={pc_rows['total']}  "
                       f"first={pc_rows['first_date']}  last={pc_rows['last_date']}")

            pc_by_type = conn.execute(
                """
                SELECT pct.name AS type_name,
                       COUNT(*) AS cnt,
                       MIN(pc.date) AS first_date,
                       MAX(pc.date) AS last_date
                FROM payment_components pc
                LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
                WHERE pc.person_id = %s
                GROUP BY pct.name
                ORDER BY pct.name
                """,
                (pid,),
            ).fetchall()
            for row in pc_by_type:
                out.append(f"    type='{row['type_name']}'  cnt={row['cnt']}  "
                           f"מ-{row['first_date']} עד {row['last_date']}")

    out_path = Path(__file__).parent / "rotman_both_records.txt"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
