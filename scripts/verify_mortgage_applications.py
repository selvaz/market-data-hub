"""Mortgage-application entry point for the stable-byte production-copy verifier.

All SQLite opens and collection writes target a private copy under D:\\Temp.
The source main/WAL are only read as bytes. Pass --families to verify a subset.
"""
import sys

from verify_macro_m9f import main


if __name__ == "__main__":
    if "--families" not in sys.argv:
        sys.argv.extend(["--families", "us_fannie_pali"])
    raise SystemExit(main())
