"""Ensure runtime DB defaults without starting the web server."""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.runtime_defaults import ensure_runtime_defaults


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensure payment codes and additive runtime schema defaults."
    )
    parser.add_argument(
        "--skip-demo",
        action="store_true",
        help="Only update the production database.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    ensure_runtime_defaults(include_demo=not args.skip_demo)
    print("Runtime defaults ensured successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
