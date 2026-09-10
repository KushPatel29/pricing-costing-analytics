"""
The SQL marts and the Python engine have to agree.

Two implementations of the same definition are only worth having if something
checks them against each other -- otherwise they are two answers waiting to be
put on one slide. That is the whole value of this file: it is not testing SQL,
it is testing that "pocket revenue" means the same thing in the warehouse as it
does in the notebook.

Comparisons are **relative**. Summing 53,000 doubles in two different orders --
DuckDB's vectorised aggregate and pandas' -- lands a few tenths of a cent apart
on three quarters of a billion. An absolute tolerance tight enough to be
meaningful on a small slice fails on a large one, and a relative gap of 1e-8
cannot hide a definitional difference.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb", reason="the SQL layer needs duckdb")

from engine import run_sql  # noqa: E402 - after the importorskip guard

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
RELATIVE = 1e-8


@pytest.fixture(scope="module")
def marts() -> dict[str, pd.DataFrame]:
    return run_sql.run(ROOT)


@pytest.fixture(scope="module")
def sales() -> pd.DataFrame:
    frame = pd.read_csv(ROOT / "data" / "fact_sales.csv",
                        dtype={"product_id": str, "customer_id": str})
    frame["month"] = pd.to_datetime(frame["month"])
    frame["fiscal_year"] = frame["month"].dt.year.where(
        frame["month"].dt.month < 7, frame["month"].dt.year + 1)
    return frame


def close(left: float, right: float, tolerance: float = RELATIVE) -> bool:
    denominator = max(abs(left), abs(right), 1.0)
    return abs(left - right) / denominator <= tolerance


# --------------------------------------------------------------------------
# The script itself
# --------------------------------------------------------------------------

def test_the_script_exists_and_defines_marts():
    assert run_sql.SQL_FILE.exists()
    assert run_sql.statement_count() >= 10


def test_every_named_mart_is_actually_created(marts):
    assert set(marts) == set(run_sql.MARTS)
    for name, frame in marts.items():
        assert len(frame) > 0, f"{name} came back empty"


def test_the_sql_uses_window_functions_rather_than_re_reading_the_fact():
    """
    Some of this is genuinely better in SQL, and that is the reason it is here.
    Running shares, ranks within a partition and a twelve-month lag are one
    clause each; expressing them as self-joins would be slower and much harder
    to read.
    """
    script = run_sql.SQL_FILE.read_text(encoding="utf-8")
    for clause in ("OVER (", "PERCENTILE_CONT", "GROUPING SETS", "LAG(", "RANK()"):
        assert clause in script, f"expected {clause} somewhere in the marts"


# --------------------------------------------------------------------------
# Agreement with pandas
# --------------------------------------------------------------------------

def test_the_waterfall_totals_agree(marts, sales):
    sql = marts["mart_waterfall"].set_index("fiscal_year")
    grouped = sales.groupby("fiscal_year")
    for year, group in grouped:
        row = sql.loc[year]
        assert close(row["list_value"], group["list_value"].sum())
        assert close(row["invoice_revenue"], group["revenue"].sum())
        assert close(row["pocket_revenue"], group["pocket_revenue"].sum())
        assert close(row["cogs"], group["cogs"].sum())


def test_the_deductions_are_extended_by_quantity_in_both(marts, sales):
    """
    The single most common way a waterfall is built wrong: deductions are stored
    per unit, and summing the per-unit column weights a twelve-unit line the
    same as a twelve-thousand-unit one. Both implementations extend first.
    """
    sql = marts["mart_waterfall"].set_index("fiscal_year")
    for year, group in sales.groupby("fiscal_year"):
        expected = float((group["on_invoice_discounts"] * group["quantity_units"]).sum())
        assert close(sql.loc[year, "on_invoice_discounts"], expected)
        expected = float((group["cost_to_serve"] * group["quantity_units"]).sum())
        assert close(sql.loc[year, "cost_to_serve"], expected)


def test_the_waterfall_levels_descend_in_the_sql_too(marts):
    for row in marts["mart_waterfall"].itertuples():
        assert row.list_value >= row.invoice_revenue >= row.net_revenue >= row.pocket_revenue


def test_profitability_by_category_agrees(marts, sales):
    sql = marts["mart_profitability"]
    sql = sql[sql["dimension"] == "category"].set_index("member")
    recent = sales[sales["fiscal_year"] == sales["fiscal_year"].max()]
    products = pd.read_csv(ROOT / "data" / "dim_product.csv", dtype={"product_id": str})
    joined = recent.merge(products[["product_id", "category"]], on="product_id")
    for category, group in joined.groupby("category"):
        assert close(sql.loc[category, "pocket_revenue"], group["pocket_revenue"].sum())
        assert close(sql.loc[category, "cogs"], group["cogs"].sum())


def test_profitability_shares_sum_to_one_within_each_dimension(marts):
    """
    The window partition has to be by dimension, not global. Getting that wrong
    produces shares that sum to six, which is visible -- and shares that sum to
    one sixth, which is not.
    """
    sql = marts["mart_profitability"]
    for dimension, group in sql.groupby("dimension"):
        assert close(float(group["revenue_share"].sum()), 1.0, tolerance=1e-6), dimension


def test_the_salesperson_cut_exists_and_covers_the_whole_team(marts):
    sql = marts["mart_profitability"]
    reps = sql[sql["dimension"] == "salesperson"]
    people = pd.read_csv(ROOT / "data" / "dim_salesperson.csv")
    assert set(reps["member"]) <= set(people["salesperson"])
    assert len(reps) >= len(people) - 2, "most of the team should have sold something"


def test_the_monthly_trend_lag_reaches_the_right_month(marts):
    """
    LAG 12 over a contiguous month index. If the index skipped a month the lag
    would silently reach the wrong one, and the year-on-year column would be
    wrong by exactly one month with nothing to show for it.
    """
    trend = marts["mart_monthly_trend"].sort_values("month_index").reset_index(drop=True)
    assert trend["month_index"].tolist() == list(range(len(trend)))
    for i in range(12, len(trend)):
        assert close(trend.loc[i, "pocket_revenue_prior_year"],
                     trend.loc[i - 12, "pocket_revenue"])
    assert trend.loc[:11, "pocket_revenue_prior_year"].isna().all()


def test_the_running_margin_share_reaches_one(marts):
    """
    The *last* row, not the maximum. A book with any loss-making product has a
    running sum that peaks above the total part-way down and comes back to it,
    so asserting on the max would fail on exactly the data worth having.
    """
    concentration = marts["mart_margin_concentration"].sort_values("margin_rank")
    assert close(float(concentration["running_margin_share"].iloc[-1]), 1.0,
                 tolerance=1e-6)
    assert concentration["margin_rank"].tolist() == list(range(1, len(concentration) + 1))


def test_margin_concentration_is_concentrated(marts):
    """
    The finding this mart exists for: a minority of the catalogue carries most
    of the margin, and knowing which minority changes what the week is spent on.
    """
    concentration = marts["mart_margin_concentration"].sort_values("margin_rank")
    top_quarter = concentration.head(max(1, len(concentration) // 4))
    share = float(top_quarter["gross_margin"].sum()
                  / concentration["gross_margin"].sum())
    assert share > 0.45, f"the top quarter of SKUs carries only {share:.0%} of margin"


def test_the_sql_exceptions_overlap_the_python_ones(marts):
    """
    Not identical -- the SQL mart applies a subset of the rules -- but the
    lines it flags have to be lines the Python scan also flags. A disjoint set
    means the two disagree about what a breach is.
    """
    sql = marts["mart_exceptions"]
    python = pd.read_csv(OUT / "guardrail_exceptions.csv",
                         dtype={"product_id": str, "customer_id": str})
    if python.empty or sql.empty:
        pytest.skip("no exceptions in one of the two implementations")
    sql_keys = set(zip(sql["product_id"].astype(str), sql["customer_id"].astype(str),
                       strict=False))
    python_keys = set(zip(python["product_id"].astype(str),
                          python["customer_id"].astype(str), strict=False))
    overlap = len(sql_keys & python_keys) / len(sql_keys)
    assert overlap > 0.85, (
        f"only {overlap:.0%} of the SQL exceptions are also Python exceptions"
    )


def test_price_bands_agree_on_the_median_they_both_compute(marts, sales):
    """
    The two use different weighting on purpose -- SQL takes the unweighted
    customer median, Python the volume-weighted one -- so this checks the SQL
    against its own definition rather than pretending they should match.
    """
    sql = marts["mart_price_bands"].copy()
    # DuckDB infers a numeric product_id from the CSV; the pandas side reads it
    # as text so a code with a leading zero survives. Compare on one type.
    sql["product_id"] = sql["product_id"].astype(str)
    sql = sql.set_index("product_id")
    recent = sales[sales["fiscal_year"] == sales["fiscal_year"].max()]
    for product_id in list(sql.index)[:25]:
        lines = recent[recent["product_id"] == product_id]["pocket_price"]
        assert close(sql.loc[product_id, "median_price"], float(lines.median()),
                     tolerance=1e-6)


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------

# The columns each mart ends its ORDER BY on. A total order is one no two rows
# can tie on; anything less and the engine is free to return them in any order,
# which is fine for a query and not fine for a file that is committed.
ORDERING: dict[str, tuple[str, ...]] = {
    "mart_waterfall": ("fiscal_year",),
    "mart_profitability": ("dimension", "member"),
    "mart_price_bands": ("product_id",),
    "mart_monthly_trend": ("month_index",),
    "mart_margin_concentration": ("gross_margin", "product_id"),
    "mart_exceptions": ("extended_margin", "month", "product_id", "customer_id"),
}


@pytest.mark.parametrize("name,columns", sorted(ORDERING.items()))
def test_every_mart_comes_back_in_a_total_order(name, columns, marts):
    """
    These CSVs are committed, so "any order the engine likes" is not good
    enough: the same query returned a different first row on Linux than on
    Windows, and the only thing that noticed was a data dictionary built from
    the first row of each file.

    A tie on the ordering key is the failure. Nothing about the numbers is
    wrong when it happens, which is exactly why it survives review.
    """
    frame = marts[name]
    key = frame[list(columns)]
    ties = key.duplicated(keep=False)
    assert not ties.any(), (
        f"{name} ties on {list(columns)} for {int(ties.sum())} rows, so its row "
        f"order is whatever the engine felt like:\n{frame[ties].head(4).to_string()}"
    )


@pytest.mark.parametrize("name", sorted(ORDERING))
def test_the_committed_mart_is_the_one_the_sql_produces(name, marts):
    """The CSV on disk is what a reader sees. If it is not what the SQL
    returns, the SQL is documentation rather than the source."""
    committed = pd.read_csv(OUT / f"sql_{name.removeprefix('mart_')}.csv")
    fresh = marts[name]
    assert len(committed) == len(fresh)
    assert list(committed.columns) == list(fresh.columns)
    for column in ORDERING[name]:
        left, right = committed[column], fresh[column].reset_index(drop=True)
        if pd.api.types.is_numeric_dtype(right):
            # Compared as numbers, not as text. A float written to CSV and read
            # back is the same number and not the same string, so a string
            # comparison here would fail on the round trip rather than on the
            # order, which is what is being checked.
            assert ((left - right).abs() <= right.abs() * RELATIVE + 1e-6).all(), (
                f"sql_{name.removeprefix('mart_')}.csv is in a different order "
                "than the SQL returns. Run: python -m engine.run_sql"
            )
        else:
            assert list(left.astype(str)) == list(right.astype(str)), (
                f"sql_{name.removeprefix('mart_')}.csv is in a different order "
                "than the SQL returns. Run: python -m engine.run_sql"
            )
