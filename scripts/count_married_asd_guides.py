#!/usr/bin/env python3
"""סופר מדריכים נשואים שעבדו במערך דיור ASD בחודש כלשהו (כל היסטוריה)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn
from core.constants import ASD_HOUSING_ARRAY_ID


def main() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.id_number,
                   MIN(tr.date) AS first_date,
                   MAX(tr.date) AS last_date,
                   COUNT(*)     AS report_count
            FROM time_reports tr
            JOIN people p     ON p.id  = tr.person_id
            JOIN apartments a ON a.id  = tr.apartment_id
            WHERE p.is_married = TRUE
              AND a.housing_array_id = %s
            GROUP BY p.id, p.name, p.id_number
            ORDER BY p.name
            """,
            (ASD_HOUSING_ARRAY_ID,),
        ).fetchall()

        unique_by_id_number = {
            (r["id_number"] or f"_no_id_{r['id']}") for r in rows
        }

    out = [
        f"מדריכים נשואים (is_married=TRUE) שיש להם דיווחי time_reports "
        f"במערך דיור ASD (id={ASD_HOUSING_ARRAY_ID}), בכל חודש שהוא:",
        "",
        f"סה\"כ רשומות people:        {len(rows)}",
        f"סה\"כ ת\"ז ייחודיות (אנשים): {len(unique_by_id_number)}",
        "",
        "פירוט:",
    ]
    for r in rows:
        out.append(
            f"  id={r['id']:>4}  ת\"ז={r['id_number']!s:<10}  "
            f"דיווחים={r['report_count']:>4}  "
            f"מ-{r['first_date']} עד {r['last_date']}  "
            f"שם={r['name']}"
        )

    out_path = Path(__file__).parent / "married_asd_guides.txt"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Results written to {out_path}")
    print(f"Unique people (by id_number): {len(unique_by_id_number)}")
    print(f"people rows:                  {len(rows)}")


if __name__ == "__main__":
    main()
