#!/usr/bin/env python3
"""אילו מדריכים מASD קיבלו דוחות במייל לפי הלוגים."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn

# person_ids שנשלח אליהם דוח לפי הלוגים (מ send-guide-email)
PERSON_IDS_FROM_LOGS = [
    78, 231, 82, 83, 84, 87, 90, 92, 93, 91, 98, 100, 251, 238, 101,
    258, 111, 109, 230, 116, 249, 125, 126, 131, 130, 299, 135, 134, 139, 138,
    143, 312, 147, 140, 252, 152, 162, 236, 158, 159, 260, 170, 169, 168, 165,
    259, 181, 240, 173, 182, 186, 189, 199, 246, 187, 248, 200, 207, 237, 239,
    244, 222, 241, 217, 223,
]


def main() -> None:
    with get_conn() as conn:
        # שליפת מערכי דיור
        arrays = conn.execute(
            "SELECT id, name FROM housing_arrays ORDER BY name"
        ).fetchall()
        print("מערכי דיור:")
        for r in arrays:
            print(f"  id={r['id']}: {r['name']}")

        # מציאת id של ASD (חיפוש לפי שם)
        asd_id = None
        for r in arrays:
            if "ASD" in (r["name"] or "").upper():
                asd_id = r["id"]
                print(f"\nמזהה ASD: {asd_id} ({r['name']})")
                break
        if asd_id is None:
            print("\nלא נמצא מערך בשם ASD")
            return

        # שליפת מדריכים שנשלח אליהם - עם housing_array_id
        placeholders = ",".join(["%s"] * len(PERSON_IDS_FROM_LOGS))
        rows = conn.execute(f"""
            SELECT p.id, p.name, p.housing_array_id, ha.name AS housing_array_name
            FROM people p
            LEFT JOIN housing_arrays ha ON ha.id = p.housing_array_id
            WHERE p.id IN ({placeholders})
        """, tuple(PERSON_IDS_FROM_LOGS)).fetchall()

        # סינון מדריכי ASD
        asd_guides = [r for r in rows if r["housing_array_id"] == asd_id]
        print(f"\nמדריכים מ-ASD שקיבלו דוח (סה\"כ {len(asd_guides)}):")
        for r in sorted(asd_guides, key=lambda x: x["name"] or ""):
            print(f"  {r['id']}: {r['name']}")

        # פילוח לפי מערך
        from collections import Counter
        by_array = Counter((r["housing_array_name"] or "ללא מערך") for r in rows)
        print("\nפילוח לפי מערך דיור (מכל הנמענים):")
        for name, cnt in by_array.most_common():
            print(f"  {name}: {cnt}")


if __name__ == "__main__":
    main()
