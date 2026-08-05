"""Import old-system monthly guide hours for clothing pay."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from core.clothing_pay import import_legacy_clothing_hours, parse_legacy_clothing_xlsx
from core.database import get_conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy monthly hours for clothing pay")
    parser.add_argument("xlsx_path", type=Path)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=7)
    args = parser.parse_args()

    rows = parse_legacy_clothing_xlsx(args.xlsx_path)
    with get_conn() as conn:
        stats = import_legacy_clothing_hours(
            conn.conn, rows, payment_year=args.year, payment_month=args.month
        )
    print(
        f"Read {stats['read']} rows; matched {stats['matched']}; "
        f"unmatched {stats['unmatched']} for {args.month:02d}/{args.year}"
    )


if __name__ == "__main__":
    main()
