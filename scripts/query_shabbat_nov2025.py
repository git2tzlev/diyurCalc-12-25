#!/usr/bin/env python3
"""Check shabbat cache for Nov 2025 and compare old vs new chain calculation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.logic import get_shabbat_times_cache


def main() -> None:
    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn)

    out_path = Path(__file__).parent / "shabbat_nov2025_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=== shabbat_cache entries for Oct-Dec 2025 ===\n\n")
        for key in sorted(shabbat_cache.keys()):
            if key.startswith("2025-10") or key.startswith("2025-11") or key.startswith("2025-12"):
                f.write(f"  {key}: {shabbat_cache[key]}\n")

        # Check specifically for exit=00:00
        f.write("\n\n=== entries with exit=00:00 ===\n")
        for key in sorted(shabbat_cache.keys()):
            entry = shabbat_cache[key]
            if entry.get("exit") == "00:00":
                f.write(f"  {key}: {entry}\n")

        # Check for entries with "holiday" key
        f.write("\n\n=== entries with holiday key ===\n")
        for key in sorted(shabbat_cache.keys()):
            entry = shabbat_cache[key]
            if entry.get("holiday"):
                f.write(f"  {key}: {entry}\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
