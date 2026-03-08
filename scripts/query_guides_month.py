#!/usr/bin/env python3
"""List guides with shifts (משמרות) in given month. Uses production DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn


def main() -> None:
    year, month = 2026, 2  # 02/2026
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT p.name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
            ORDER BY p.name
        """, (year, month)).fetchall()

    names = [r["name"] for r in rows]
    out_path = Path(__file__).parent / "guides_month_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Guides with shifts in {month:02d}/{year} (production):\n")
        f.write("\n".join(names))
        f.write(f"\n\nTotal: {len(names)}\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
