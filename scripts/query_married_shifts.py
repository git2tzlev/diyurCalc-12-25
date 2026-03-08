#!/usr/bin/env python3
"""Shifts of married guides in 02/2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.database import get_conn

with get_conn() as conn:
    rows = conn.execute("""
        SELECT p.name, st.name as shift_name, tr.date, tr.start_time, tr.end_time
        FROM time_reports tr
        JOIN people p ON p.id = tr.person_id
        JOIN shift_types st ON st.id = tr.shift_type_id
        WHERE p.is_married = true
          AND EXTRACT(YEAR FROM tr.date) = 2026
          AND EXTRACT(MONTH FROM tr.date) = 2
        ORDER BY p.name, tr.date, tr.start_time
    """).fetchall()

out = []
current = None
for r in rows:
    if r["name"] != current:
        current = r["name"]
        out.append(f"\n=== {current} ===")
    out.append(f"  {r['date']} | {r['shift_name']} | {r['start_time']}-{r['end_time']}")

text = "\n".join(out).strip()
with open(Path(__file__).parent / "married_feb2026_shifts.txt", "w", encoding="utf-8") as f:
    f.write(text)
print(text)
