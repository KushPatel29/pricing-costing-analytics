"""
Run ``sql/pricing_marts.sql`` against DuckDB and write the marts to ``output/``.

DuckDB reads the CSVs in place, so there is no load step and no database to
keep in sync -- the SQL is executed against exactly the files the Python engine
reads, which is what makes comparing the two meaningful rather than an exercise
in keeping two copies of the data aligned.

Usage::

    python -m engine.run_sql
    python -m engine.run_sql --check      # compare against the Python outputs
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "sql" / "pricing_marts.sql"
OUT_DIR = ROOT / "output"

# Every mart the script defines, and where its Python counterpart lives. A mart
# with no counterpart is fine -- some things are only worth writing in SQL --
# but one that claims a counterpart is checked against it.
MARTS = ("mart_waterfall", "mart_profitability", "mart_price_bands",
         "mart_monthly_trend", "mart_margin_concentration", "mart_exceptions")


def connect(data_root: Path = ROOT):
    """
    A DuckDB connection with the working directory set for the relative paths
    in the SQL. Kept here rather than in the SQL so the script stays runnable
    from a warehouse where the tables are real rather than CSVs.
    """
    import duckdb

    connection = duckdb.connect()
    connection.execute(f"SET file_search_path = '{data_root.as_posix()}'")
    connection.execute(f"SET home_directory = '{data_root.as_posix()}'")
    return connection


def run(data_root: Path = ROOT) -> dict[str, pd.DataFrame]:
    import os

    script = SQL_FILE.read_text(encoding="utf-8")
    previous = Path.cwd()
    os.chdir(data_root)
    try:
        connection = connect(data_root)
        connection.execute(script)
        return {name: connection.execute(f"SELECT * FROM {name}").df() for name in MARTS}
    finally:
        os.chdir(previous)


def statement_count() -> int:
    """How many statements the script defines, for the test that counts them."""
    return len(re.findall(r"CREATE OR REPLACE VIEW", SQL_FILE.read_text(encoding="utf-8")))


# Relative, not absolute. The two implementations sum the same numbers in
# different orders, so they differ in the last bits of a float and the dollar
# gap grows with the size of the book -- an absolute threshold is a number
# tuned to one dataset that fails on the next regeneration for a reason nobody
# can act on. This is the tolerance tests/test_sql_matches_python.py holds every
# mart to; a genuinely wrong join or filter moves a total by percent, not by
# parts per billion.
TOLERANCE = 1e-8


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--check", action="store_true",
                    help="compare the SQL totals against the Python engine's")
    args = ap.parse_args(argv)

    marts = run()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in marts.items():
        frame.to_csv(args.out_dir / f"sql_{name.removeprefix('mart_')}.csv",
                     index=False, lineterminator="\n")

    print(f"ran {statement_count()} views, wrote {len(marts)} marts to {args.out_dir}/")
    for name, frame in marts.items():
        print(f"  {name:28s} {len(frame):>6,} rows x {len(frame.columns):>2} cols")

    if args.check:
        python_waterfall = pd.read_csv(args.out_dir / "price_waterfall.csv")
        sql_total = float(marts["mart_waterfall"]["pocket_revenue"].sum())
        python_total = float(
            python_waterfall.loc[python_waterfall["step"] == "Pocket revenue", "amount"].iloc[0]
        )
        gap = abs(sql_total - python_total)
        relative = gap / abs(python_total) if python_total else gap
        print(f"\n  SQL pocket revenue    ${sql_total:>16,.2f}")
        print(f"  Python pocket revenue ${python_total:>16,.2f}")
        print(f"  difference            ${gap:>16,.2f}  ({relative:.2e} relative)")
        if relative > TOLERANCE:
            print("\n  SQL and Python disagree", file=sys.stderr)
            return 1
        print("\n  SQL and Python agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
