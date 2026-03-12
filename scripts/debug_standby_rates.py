#!/usr/bin/env python3
"""Debug standby rates from database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn


def main():
    with get_conn() as conn:
        # Check table structure
        print("=== standby_rates columns ===")
        rows = conn.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'standby_rates'
            ORDER BY ordinal_position
        """).fetchall()
        for r in rows:
            print(f"  {r['column_name']}: {r['data_type']}")

        # Check all standby_rates
        print("\n=== All standby_rates ===")
        rows = conn.execute("SELECT * FROM standby_rates ORDER BY 1, 2").fetchall()
        for r in rows:
            print(f"  {dict(r)}")

        # Check shift_time_segments for standby
        print("\n=== standby segments ===")
        rows = conn.execute("""
            SELECT seg.id, seg.shift_type_id, seg.segment_type, seg.start_time, seg.end_time,
                   st.name as shift_name
            FROM shift_time_segments seg
            JOIN shift_types st ON st.id = seg.shift_type_id
            WHERE seg.segment_type = 'standby'
            ORDER BY seg.shift_type_id, seg.id
        """).fetchall()
        for r in rows:
            print(f"  seg_id={r['id']} shift_type={r['shift_type_id']} ({r['shift_name']}) "
                  f"{r['start_time']}-{r['end_time']}")

        # Check the get_standby_rate function
        print("\n=== Testing get_standby_rate function ===")
        from app_utils import get_standby_rate
        
        # Test for Kramer: seg_id=10 (Friday standby), apt_type=5, married=True
        rate = get_standby_rate(conn, 10, 5, True, 2025, 11)
        print(f"  Kramer Friday (seg=10, apt=5, married=True): {rate}")
        rate = get_standby_rate(conn, 8, 5, True, 2025, 11)
        print(f"  Kramer Shabbat (seg=8, apt=5, married=True): {rate}")
        
        # Test for Shaliach: seg_id=2 (weekday standby), apt_type=1, married=False
        rate = get_standby_rate(conn, 2, 1, False, 2025, 11)
        print(f"  Shaliach Weekday (seg=2, apt=1, married=False): {rate}")
        rate = get_standby_rate(conn, 13, 1, False, 2025, 11)
        print(f"  Shaliach Night (seg=13, apt=1, married=False): {rate}")
        rate = get_standby_rate(conn, 10, 1, False, 2025, 11)
        print(f"  Shaliach Friday (seg=10, apt=1, married=False): {rate}")
        rate = get_standby_rate(conn, 8, 1, False, 2025, 11)
        print(f"  Shaliach Shabbat (seg=8, apt=1, married=False): {rate}")

        # Also check apartment type 5
        print("\n=== Apartment type 5 info ===")
        rows = conn.execute("""
            SELECT * FROM apartment_types WHERE id = 5
        """).fetchall()
        for r in rows:
            print(f"  {dict(r)}")
        if not rows:
            print("  No apartment_type 5!")
            
        # Check apartment 12 (Kramer's apartment)
        print("\n=== Apartment 12 info ===")
        rows = conn.execute("""
            SELECT a.*, at.name as type_name
            FROM apartments a
            LEFT JOIN apartment_types at ON at.id = a.apartment_type_id
            WHERE a.id = 12
        """).fetchall()
        for r in rows:
            print(f"  {dict(r)}")

        # Check apartment 8 (Shaliach's apartment)
        print("\n=== Apartment 8 info ===")
        rows = conn.execute("""
            SELECT a.*, at.name as type_name
            FROM apartments a
            LEFT JOIN apartment_types at ON at.id = a.apartment_type_id
            WHERE a.id = 8
        """).fetchall()
        for r in rows:
            print(f"  {dict(r)}")


if __name__ == "__main__":
    main()
