"""
Hold the generated Power BI project to the data it actually reads.

Every failure mode this file checks is one Power BI does not report. A measure
naming a column that no longer exists gets `state: SemanticError`, every
dependent measure gets `DependencyError`, and the bound visual renders
"Something's wrong with one or more fields" -- at runtime, on a project that
opened cleanly. A visual bound to a mistyped field renders empty. A numeric
column left out of the M query's type list arrives as text, still binds, still
draws, and sorts alphabetically.

None of that is visible in a diff, so it is asserted here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from powerbi import build_pbip
from powerbi.model_spec import (
    FIELD_PARAMETERS,
    MEASURES,
    RELATIONSHIPS,
    TABLES,
    UNRELATED,
    WHATIF_PARAMETERS,
    field_parameter_columns,
    whatif_columns,
)

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "powerbi" / "pbip"
MODEL = PBIP / "PricingAnalytics.SemanticModel" / "definition"
REPORT = PBIP / "PricingAnalytics.Report" / "definition"

MEASURE_NAMES = {name for name, *_ in MEASURES}


@pytest.fixture(scope="module")
def source_columns() -> dict[str, set[str]]:
    """
    The columns each table actually has.

    Read from the CSV for the imported tables, and from the parameter spec for
    the what-if tables, which are calculated and have no CSV. Both kinds go in
    one mapping so a measure naming either is checked the same way -- a measure
    reading a parameter column that was renamed is exactly as broken as one
    reading a CSV column that was.
    """
    out = {}
    for name, meta in TABLES.items():
        path = ROOT / meta["source"] / f"{name}.csv"
        out[name] = set(pd.read_csv(path, nrows=1).columns)
    out.update(whatif_columns())
    out.update(field_parameter_columns())
    return out


# --------------------------------------------------------------------------
# The project exists and is what the spec says
# --------------------------------------------------------------------------

def test_the_project_was_generated():
    assert (PBIP / "PricingAnalytics.pbip").exists(), (
        "no PBIP on disk. Run: python -m powerbi.build_pbip"
    )
    assert (MODEL / "model.tmdl").exists()
    assert (REPORT / "pages" / "pages.json").exists()


def test_the_committed_project_matches_the_spec(tmp_path):
    """
    The generator is the source of truth, so a hand-edit to the committed
    project has to fail rather than survive until the next regeneration
    silently reverts it.
    """
    build_pbip.build(tmp_path, ROOT)
    drift = build_pbip.differences(tmp_path, PBIP)
    assert not drift, (
        "committed PBIP differs from what the spec generates:\n  "
        + "\n  ".join(drift[:20])
        + "\n\nrun: python -m powerbi.build_pbip"
    )


# --------------------------------------------------------------------------
# Measures
# --------------------------------------------------------------------------

def _referenced_columns(dax: str) -> set[tuple[str, str]]:
    """Every `table[column]` in a DAX expression, ignoring `[Measure]`."""
    return set(re.findall(r"(\w+)\[([^\]]+)\]", dax))


def _referenced_measures(dax: str) -> set[str]:
    """Every bare `[Name]` -- a measure reference, not a column."""
    return set(re.findall(r"(?<![\w\]])\[([^\]]+)\]", dax))


@pytest.mark.parametrize("name,dax", [(m[0], m[1]) for m in MEASURES])
def test_every_measure_only_names_columns_that_exist(name, dax, source_columns):
    missing = [
        f"{table}[{column}]"
        for table, column in _referenced_columns(dax)
        if table in source_columns and column not in source_columns[table]
    ]
    assert not missing, f"measure {name!r} reads columns that do not exist: {missing}"


@pytest.mark.parametrize("name,dax", [(m[0], m[1]) for m in MEASURES])
def test_every_measure_only_names_tables_in_the_model(name, dax):
    known = set(TABLES) | {"_Measures"} | set(whatif_columns()) | set(field_parameter_columns())
    unknown = sorted({t for t, _ in _referenced_columns(dax)} - known)
    assert not unknown, f"measure {name!r} reads tables not in the model: {unknown}"


@pytest.mark.parametrize("name,dax", [(m[0], m[1]) for m in MEASURES])
def test_every_measure_reference_resolves(name, dax):
    referenced = _referenced_measures(dax) - MEASURE_NAMES
    # Quoted strings inside FORMAT() are not measure references.
    referenced = {r for r in referenced if not r.startswith('"')}
    assert not referenced, (
        f"measure {name!r} calls measures that do not exist: {sorted(referenced)}"
    )


def test_every_var_is_prefixed_to_dodge_the_reserved_word_list():
    """
    Power BI reserves far more words for VAR names than the four that are
    documented. `Goal`, `Status`, `Trend` and `Variance` are the known ones;
    `Move` and `Scope` fail identically -- "The syntax for 'Move' is incorrect"
    -- and there is no published list. The measure gets a SemanticError, every
    dependent gets a DependencyError, and the visual renders an error at
    runtime on a report that built cleanly.

    Chasing the list is not a strategy. Prefixing every VAR with `v` plus a
    capital sidesteps the whole question, and this test keeps it that way.
    """
    offenders = []
    for name, dax, *_ in MEASURES:
        for var in re.findall(r"\bVAR\s+(\w+)", dax):
            if not re.match(r"^v[A-Z]", var):
                offenders.append(f"{name}: VAR {var}")
    assert not offenders, (
        "VAR names must be v + a capital, to stay clear of the undocumented "
        f"reserved words: {offenders}"
    )


def test_measure_names_are_unique():
    names = [m[0] for m in MEASURES]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"duplicate measure names: {duplicates}"


def test_currency_and_percent_measures_carry_a_format_string():
    """
    A measure with no format string renders as a raw float. 0.2539 on a card
    labelled "Pocket margin %" is not a rounding problem, it is a wrong number
    in front of a reader.
    """
    unformatted = [
        name for name, dax, fmt, folder in MEASURES
        if not fmt and folder != "12 Labels"
    ]
    assert not unformatted, f"measures with no format string: {unformatted}"


# --------------------------------------------------------------------------
# What-if parameters
# --------------------------------------------------------------------------

def _parameter_tmdl(table: str) -> str:
    return (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")


@pytest.mark.parametrize("parameter", WHATIF_PARAMETERS,
                         ids=[p[0] for p in WHATIF_PARAMETERS])
def test_every_parameter_is_a_calculated_table(parameter):
    """
    A GENERATESERIES written into an M partition is not a syntax error. It is a
    table that fails at refresh with a message about an unknown function, on a
    model that opened cleanly.
    """
    table, column, low, high, step, fmt, _measure = parameter
    tmdl = _parameter_tmdl(table)
    assert f"partition {table} = calculated" in tmdl
    assert "= m\n" not in tmdl
    assert "GENERATESERIES" in tmdl
    assert f'"{column}", [Value]' in tmdl, (
        "raw GENERATESERIES names its column Value, so four parameters would put "
        "four fields called Value in the field list"
    )


@pytest.mark.parametrize("parameter", WHATIF_PARAMETERS,
                         ids=[p[0] for p in WHATIF_PARAMETERS])
def test_every_parameter_has_a_measure_that_reads_it(parameter):
    """
    A parameter table with no SELECTEDVALUE measure is a slicer that moves and
    changes nothing on the page.
    """
    table, column, _low, _high, _step, _fmt, measure = parameter
    dax = next((d for name, d, *_ in MEASURES if name == measure), None)
    assert dax is not None, f"{table} has no {measure!r} measure"
    assert f"SELECTEDVALUE({table}[{column}]" in dax
    assert dax.rstrip().endswith(", 0)"), (
        "without a default, an unselected parameter returns BLANK and every "
        "scenario measure downstream goes blank with it"
    )


@pytest.mark.parametrize("parameter", WHATIF_PARAMETERS,
                         ids=[p[0] for p in WHATIF_PARAMETERS])
def test_every_parameter_range_is_usable(parameter):
    _table, _column, low, high, step, _fmt, _measure = parameter
    assert low < 0 < high, "a what-if that cannot go both ways is a filter"
    assert step > 0
    points = (high - low) / step
    assert 4 <= points <= 200, f"{points:.0f} slider positions is unusable"


def test_the_parameters_are_referenced_by_the_model_but_not_ordered():
    """
    `ref table` is what makes TMDL load the file. `PBI_QueryOrder` is the
    refresh order for tables that have a query -- a calculated table named in
    it is refreshed as if it had an M partition.
    """
    model = (MODEL / "model.tmdl").read_text(encoding="utf-8")
    order = model.split("PBI_QueryOrder = ")[1].split("\n")[0]
    for table, *_ in (*WHATIF_PARAMETERS, *FIELD_PARAMETERS):
        assert f"ref table {table}" in model, f"{table} is never referenced"
        assert f'"{table}"' not in order, f"{table} is a calculated table, not a query"


@pytest.mark.parametrize("parameter", FIELD_PARAMETERS,
                         ids=[p[0] for p in FIELD_PARAMETERS])
def test_every_field_parameter_declares_itself_as_one(parameter):
    """
    One extended property is the entire difference between a field parameter
    and three columns of strings. Without it the table still loads, the visual
    still binds it, and the chart draws the measure *names* along an axis --
    scaled, titled and meaningless.
    """
    table, column, _entries = parameter
    tmdl = _parameter_tmdl(table)
    assert "extendedProperty ParameterMetadata" in tmdl
    assert '"kind": 2' in tmdl, "kind 0 is a what-if; a field parameter is kind 2"
    assert f"column '{column} Fields'" in tmdl
    assert f"partition {table} = calculated" in tmdl


@pytest.mark.parametrize("parameter", FIELD_PARAMETERS,
                         ids=[p[0] for p in FIELD_PARAMETERS])
def test_every_field_parameter_names_measures_that_exist(parameter):
    """
    NAMEOF() resolves at model load, so a renamed measure breaks the model
    rather than blanking a visual -- but only if the name was right to begin
    with, and only after someone opens the file.
    """
    _table, _column, entries = parameter
    missing = [measure for _label, measure in entries if measure not in MEASURE_NAMES]
    assert not missing, f"field parameter points at measures that do not exist: {missing}"


@pytest.mark.parametrize("parameter", FIELD_PARAMETERS,
                         ids=[p[0] for p in FIELD_PARAMETERS])
def test_every_field_parameter_sorts_by_its_own_order(parameter):
    """
    Without the sort column the slicer lists the metrics alphabetically, which
    puts "Margin %" above "Pocket revenue" and reads as an accident.
    """
    table, column, entries = parameter
    tmdl = _parameter_tmdl(table)
    assert f"sortByColumn: '{column} Order'" in tmdl
    for order, _ in enumerate(entries):
        assert f", {order})" in tmdl


def test_no_parameter_is_related_to_anything():
    """
    A parameter joined to a fact filters that fact to the rows matching the
    parameter value, which is the opposite of what a what-if is for -- and it
    fails by showing a smaller number rather than an error.
    """
    relationships = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    for table, *_ in (*WHATIF_PARAMETERS, *FIELD_PARAMETERS):
        assert f"{table}." not in relationships


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------

@pytest.mark.parametrize("relationship", RELATIONSHIPS,
                         ids=[f"{r[0]}.{r[1]}->{r[2]}" for r in RELATIONSHIPS])
def test_both_sides_of_every_relationship_exist(relationship, source_columns):
    from_table, from_column, to_table, to_column = relationship
    assert from_column in source_columns[from_table], (
        f"{from_table} has no column {from_column!r}")
    assert to_column in source_columns[to_table], (
        f"{to_table} has no column {to_column!r}")


@pytest.mark.parametrize("relationship", RELATIONSHIPS,
                         ids=[f"{r[2]}.{r[3]}" for r in RELATIONSHIPS])
def test_the_one_side_of_every_relationship_is_actually_unique(relationship):
    """
    Power BI refuses a many-to-one relationship whose "one" side has duplicates,
    and the refusal arrives when the model is first refreshed rather than when
    the file is written.
    """
    _, _, to_table, to_column = relationship
    frame = pd.read_csv(ROOT / TABLES[to_table]["source"] / f"{to_table}.csv")
    assert frame[to_column].is_unique, (
        f"{to_table}[{to_column}] is not unique, so it cannot be the one side"
    )


@pytest.mark.parametrize("relationship", RELATIONSHIPS,
                         ids=[f"{r[0]}.{r[1]}" for r in RELATIONSHIPS])
def test_every_relationship_actually_matches_rows(relationship):
    """
    A relationship between two columns that share no values validates, refreshes
    and filters everything to nothing. The visuals go blank, which reads as "no
    data for this selection" rather than as a broken model.
    """
    from_table, from_column, to_table, to_column = relationship
    left = pd.read_csv(ROOT / TABLES[from_table]["source"] / f"{from_table}.csv",
                       usecols=[from_column])[from_column].astype(str)
    right = pd.read_csv(ROOT / TABLES[to_table]["source"] / f"{to_table}.csv",
                        usecols=[to_column])[to_column].astype(str)
    matched = left.isin(set(right)).mean()
    assert matched > 0.95, (
        f"{from_table}[{from_column}] matches only {matched:.1%} of "
        f"{to_table}[{to_column}]"
    )


def test_every_table_is_either_related_or_explained():
    """
    A disconnected table is usually a modelling mistake. These are not, and the
    reason is written down so the next person does not "fix" them into an
    ambiguous filter path.
    """
    related = {r[0] for r in RELATIONSHIPS} | {r[2] for r in RELATIONSHIPS}
    orphans = sorted(set(TABLES) - related - set(UNRELATED))
    assert not orphans, f"tables with no relationship and no stated reason: {orphans}"


def test_the_month_dimension_is_not_marked_as_a_date_table():
    """
    Power BI needs a contiguous daily date column to mark a date table. Every
    fact here is monthly, so the marking would either be rejected or accepted
    against a column with 30-day gaps. Period comparisons subtract month_index.
    """
    tmdl = (MODEL / "tables" / "dim_month.tmdl").read_text(encoding="utf-8")
    assert "dataCategory: Time" not in tmdl
    assert "month_index" in tmdl
    months = pd.read_csv(ROOT / "data" / "dim_month.csv")["month_index"].tolist()
    assert months == list(range(min(months), max(months) + 1)), (
        "month_index has gaps, so subtracting 12 from it does not reach last year"
    )


# --------------------------------------------------------------------------
# The M queries
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_bound_column_is_also_typed(table, source_columns):
    """
    `Table.PromoteHeaders` keeps every column in the CSV; only those listed in
    `TransformColumnTypes` get a type. A numeric column left off that list
    arrives as text, still binds, still renders, and sorts alphabetically -- and
    any measure multiplying it fails at query time with Power BI's generic
    "this might be caused by a capacity or license issue".
    """
    tmdl = (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
    bound = set(re.findall(r"sourceColumn: (\S+)", tmdl))
    typed = set(re.findall(r'\{"([^"]+)", ', tmdl))
    untyped = sorted(bound - typed)
    assert not untyped, f"{table}: bound but never typed, so they arrive as text: {untyped}"
    assert bound == source_columns[table], (
        f"{table}: the model and the CSV disagree about columns: "
        f"{sorted(bound ^ source_columns[table])}"
    )


@pytest.mark.parametrize("table", sorted(TABLES))
def test_no_identifier_is_summarised(table):
    """
    Summing a product code is never what anyone meant, and Power BI offers to
    do it by default on anything numeric.
    """
    tmdl = (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
    blocks = tmdl.split("\tcolumn ")[1:]
    offenders = []
    for block in blocks:
        name = block.split("\n")[0].strip()
        if name.endswith(("_id", "_code")) and "summarizeBy: none" not in block:
            offenders.append(name)
    assert not offenders, f"{table}: identifiers offered for aggregation: {offenders}"
