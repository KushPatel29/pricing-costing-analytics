"""
Write the Power BI project from the specs in this package.

A PBIR report is one JSON file per visual, and this one has seventy-odd of
them. Typing those by hand is how a report ends up carrying three different
``visualContainer`` schema versions, a property name Desktop silently drops on
the next save, and no way to check either -- Power BI treats a bad property as
an empty visual rather than an error, so a broken report looks finished.

So the report is generated from :mod:`powerbi.report_spec`, the model from
:mod:`powerbi.model_spec`, and both are committed. ``--check`` regenerates into
a temporary directory and diffs, so CI fails if the committed project and the
spec have drifted apart.

Column types come from the CSVs themselves rather than from a list kept by
hand. That is not tidiness: a column bound in the M query but left out of
``Table.TransformColumnTypes`` arrives as **text**, still binds, still renders,
and sorts alphabetically -- and any measure multiplying it fails at query time
with Power BI's generic "this might be caused by a capacity or license issue".

Usage::

    python -m powerbi.build_pbip
    python -m powerbi.build_pbip --check
"""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd

from powerbi.model_spec import (
    FIELD_PARAMETERS,
    MEASURES,
    RELATIONSHIPS,
    SORT_BY,
    TABLES,
    WHATIF_PARAMETERS,
)
from powerbi.report_chrome import (
    ACCENT,
    CHROME_KINDS,
    EDGE,
    PANEL,
    PANEL_X,
    PANEL_Y,
    RAISED,
    bookmarks,
    theme_styles,
    tile_measure,
    ui_measures,
)
from powerbi.report_spec import PAGES, VISUAL_TYPES

ROOT = Path(__file__).resolve().parents[1]
PBIP_DIR = ROOT / "powerbi" / "pbip"
PROJECT = "PricingAnalytics"
THEME = "PricingCanvasDark.json"

# Hand-written files that live alongside the generated project. The cleanup
# below removes anything the generator does not own, and the drift check would
# otherwise report these as stale.
KEEP = {"OPEN_ME_FIRST.md"}

# Schema versions Microsoft has actually published. Inventing a version number
# is free until you want to validate against it: the schema sets
# additionalProperties:false, which is the one cheap way to catch a mistyped
# property in a hand-authored report, and a 404 gives that up while looking
# like you still have it.
SCHEMA = {
    "pbip": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
    "platform": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
    "pbism": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
    "pbir": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
    "report": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
    "version": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
    "pages": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
    "page": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.4.0/schema.json",
    "visual": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
    "bookmark": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmark/2.1.0/schema.json",
    "bookmarks": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmarksMetadata/1.0.0/schema.json",
}

# Stable ids: same name in, same GUID out, so a rebuild produces no diff.
NAMESPACE = uuid.UUID("6f1c2d34-5a6b-4c7d-8e9f-0a1b2c3d4e5f")


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(parts)))


# --------------------------------------------------------------------------
# Column typing
# --------------------------------------------------------------------------

# Suffixes and stems that decide a numeric column's format string. Checked in
# order, so the percent test runs before the money one -- "margin_pct" is a
# percentage and "margin" is dollars, and getting that backwards puts 0.253 on
# a card as $0.25.
FORMAT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("_pct", "_percent", "rate", "coverage", "recovery", "elasticity",
      "r_squared", "passthrough", "headroom", "cannibalisation"), "0.0%"),
    (("price", "cost", "revenue", "margin", "amount", "variance", "value",
      "opportunity", "at_risk", "overhead", "budget", "actual", "gap"), "\\$#,0.00"),
    (("_lb", "quantity", "volume", "units", "count", "lines", "quotes",
      "observations", "index", "days", "months", "year"), "#,0"),
)

# These are identifiers that happen to be numeric. Summing a product code is
# never what anyone meant.
ID_SUFFIXES = ("_id", "_code", "_index", "_order")

# Ordering keys. They must stay *numeric*: a sort column read as text sorts
# lexicographically, so a fourteen-step waterfall ordered by `sort_order` came
# out 0, 1, 10, 11, 12, 13, 2, 3 -- with the closing subtotals in the middle
# and no error anywhere. They still summarise to nothing; that is a separate
# rule below.
SORT_SUFFIXES = ("_order", "_rank")


def format_for(column: str, dtype: str) -> str:
    if dtype == "dateTime":
        return "yyyy-mm-dd"
    if dtype == "int64":
        return "0"
    if dtype != "double":
        return ""
    lowered = column.lower()
    for stems, fmt in FORMAT_RULES:
        if any(stem in lowered for stem in stems):
            return fmt
    return "#,0.00"


def infer_columns(path: Path) -> list[dict]:
    """
    Read the CSV and describe each column for TMDL and for the M query.

    Both descriptions come from the same inference, so a column can never be
    typed one way in the model and another in the query that feeds it.
    """
    frame = pd.read_csv(path, nrows=4000)
    columns = []
    for name in frame.columns:
        series = frame[name]
        if name.endswith("month") or name in ("date", "week_start", "last_price_change"):
            dtype, m_type = "dateTime", "type date"
        elif pd.api.types.is_bool_dtype(series):
            dtype, m_type = "boolean", "type logical"
        elif pd.api.types.is_integer_dtype(series):
            dtype, m_type = "int64", "Int64.Type"
        elif pd.api.types.is_float_dtype(series):
            dtype, m_type = "double", "type number"
        else:
            dtype, m_type = "string", "type text"

        # Ids are read as text so a code with a leading zero survives, and so
        # nothing offers to sum them.
        is_sort_key = any(name.lower().endswith(s) for s in SORT_SUFFIXES)
        if (any(name.lower().endswith(s) for s in ID_SUFFIXES)
                and name != "month_index" and not is_sort_key):
            if not name.endswith("_index"):
                dtype, m_type = "string", "type text"

        summarize = "none" if dtype in ("string", "dateTime", "boolean") else "sum"
        if any(name.lower().endswith(s) for s in (*ID_SUFFIXES, *SORT_SUFFIXES)):
            summarize = "none"
        columns.append(
            {"name": name, "dataType": dtype, "mType": m_type,
             "format": format_for(name, dtype), "summarizeBy": summarize}
        )
    return columns


# --------------------------------------------------------------------------
# TMDL
# --------------------------------------------------------------------------

def table_tmdl(name: str, meta: dict, columns: list[dict]) -> str:
    lines = [f"table {name}", f"\tlineageTag: {tag('table', name)}", ""]
    for column in columns:
        lines.append(f"\tcolumn {column['name']}")
        lines.append(f"\t\tdataType: {column['dataType']}")
        if column["format"]:
            lines.append(f"\t\tformatString: {column['format']}")
        lines.append(f"\t\tlineageTag: {tag('column', name, column['name'])}")
        lines.append(f"\t\tsummarizeBy: {column['summarizeBy']}")
        lines.append(f"\t\tsourceColumn: {column['name']}")
        sort_column = SORT_BY.get(name, {}).get(column["name"])
        if sort_column:
            # Without this a text column sorts alphabetically on every axis in
            # the report, and nothing about the chart says so: "Apr 2025"
            # before "Aug 2024", a waterfall with its opening bar in the
            # middle, a traffic light reading amber, green, red.
            lines.append(f"\t\tsortByColumn: {sort_column}")
        lines.append("")

    types = ", ".join(f'{{"{c["name"]}", {c["mType"]}}}' for c in columns)
    source_dir = meta["source"]
    lines += [
        f"\tpartition {name} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document(File.Contents('
        f'DataPath & "\\{source_dir}\\{name}.csv"), '
        "[Delimiter = \",\", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),",
        "\t\t\t\t    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f"\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {{{types}}})",
        "\t\t\t\tin",
        "\t\t\t\t    Typed",
        "",
    ]
    return "\n".join(lines)


def measures_tmdl() -> str:
    lines = ["table _Measures", f"\tlineageTag: {tag('table', '_Measures')}", ""]
    for name, dax, fmt, folder in MEASURES:
        body = dax.split("\n")
        if len(body) == 1:
            lines.append(f"\tmeasure '{name}' = {body[0]}")
        else:
            lines.append(f"\tmeasure '{name}' =")
            lines.extend(f"\t\t\t{line}" if line else "" for line in body)
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
        lines.append(f"\t\tdisplayFolder: {folder}")
        lines.append("")

    # The report's own measures: SVG tiles and headers, filter context and the
    # Filters button's label. Written from the page specs, kept out of MEASURES
    # so the metric reference stays a list of business definitions.
    formats = {name: fmt for name, _dax, fmt, _folder in MEASURES}
    for name, dax, image in ui_measures(PAGES, formats):
        lines.append(f"\tmeasure '{name}' =")
        lines.extend(f"\t\t\t{line}" if line else "" for line in dax.split("\n"))
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
        if image:
            # Without it the image visual shows nothing: the string is a data
            # URI only once the column says it is one.
            lines.append("\t\tdataCategory: ImageUrl")
        lines.append("\t\tdisplayFolder: Report UI")
        lines.append("")

    # A measures table needs one hidden column or Desktop will not show it in
    # the field list at all.
    lines += [
        "\tcolumn _placeholder",
        # dataType is not optional on a column fed by an M partition. Without
        # it the column loads as type Empty, which Power BI allows only on a
        # calculated column, and it refuses the entire project on open:
        # "The column '_Measures[_placeholder]' cannot be of type Empty".
        "\t\tdataType: int64",
        "\t\tisHidden",
        "\t\tformatString: 0",
        f"\t\tlineageTag: {tag('column', '_Measures', '_placeholder')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: _placeholder",
        "",
        "\tpartition _Measures = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        '\t\t\t\t    Source = #table(type table [_placeholder = Int64.Type], {{0}})',
        "\t\t\t\tin",
        "\t\t\t\t    Source",
        "",
    ]
    return "\n".join(lines)


def whatif_tmdl(table: str, column: str, low: float, high: float,
                step: float, fmt: str) -> str:
    """
    A what-if parameter, as a calculated table plus its slicer column.

    Two details are load-bearing. The partition is ``= calculated``, not ``= m``
    -- a GENERATESERIES written into an M partition is not an error, it is a
    table that fails to refresh with a message about an unknown function.

    And the series is wrapped in SELECTCOLUMNS to name the column. Raw
    GENERATESERIES produces a column called ``Value``, so four parameters would
    put four fields called ``Value`` in the field list and the slicer would be
    labelled ``Value`` on the canvas. Naming it here means the column name and
    ``sourceColumn`` agree, which is what ``isNameInferred`` asserts.
    """
    return "\n".join([
        f"table {table}",
        f"\tlineageTag: {tag('table', table)}",
        "",
        f"\tcolumn '{column}'",
        "\t\tdataType: double",
        "\t\tisNameInferred",
        f"\t\tformatString: {fmt}",
        f"\t\tlineageTag: {tag('column', table, column)}",
        "\t\tsummarizeBy: none",
        f"\t\tsourceColumn: [{column}]",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tpartition {table} = calculated",
        "\t\tmode: import",
        f'\t\tsource = SELECTCOLUMNS(GENERATESERIES({low}, {high}, {step}), '
        f'"{column}", [Value])',
        "",
    ])


def field_parameter_tmdl(table: str, column: str,
                         entries: tuple[tuple[str, str], ...]) -> str:
    """
    A field parameter: a calculated table of NAMEOF() references.

    The `extendedProperty ParameterMetadata` on the hidden Fields column is the
    whole mechanism. Without it the table is three columns of strings, the
    visual binds them, and it draws the measure *names* along an axis -- a
    chart that renders, scales and means nothing. `kind: 2` is a field
    parameter; `kind: 0` would be a what-if.

    NAMEOF() resolves at model load, so a measure renamed without renaming it
    here fails as a model error rather than as a blank visual. That is the
    better of the two failures and the reason the entries name measures rather
    than repeating their DAX.
    """
    lines = [
        f"table {table}",
        f"\tlineageTag: {tag('table', table)}",
        "",
        # sourceColumn is Value1/Value2/Value3, NOT the display name. A DAX
        # table constructor names its columns that way, and declaring them
        # against the display names is what made both pages that used a field
        # parameter render "Something's wrong with one or more fields".
        f"\tcolumn '{column}'",
        "\t\tdataType: string",
        f"\t\tlineageTag: {tag('column', table, column)}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: [Value1]",
        f"\t\tsortByColumn: '{column} Order'",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tcolumn '{column} Fields'",
        "\t\tdataType: string",
        "\t\tisHidden",
        f"\t\tlineageTag: {tag('column', table, column + ' Fields')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: [Value2]",
        "",
        "\t\textendedProperty ParameterMetadata =",
        "\t\t\t\t{",
        '\t\t\t\t  "version": 3,',
        '\t\t\t\t  "kind": 2',
        "\t\t\t\t}",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tcolumn '{column} Order'",
        "\t\tdataType: int64",
        "\t\tisHidden",
        "\t\tformatString: 0",
        f"\t\tlineageTag: {tag('column', table, column + ' Order')}",
        "\t\tsummarizeBy: sum",
        "\t\tsourceColumn: [Value3]",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tpartition {table} = calculated",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\t{",
    ]
    rows = [
        f'\t\t\t\t    ("{label}", NAMEOF(\'_Measures\'[{measure}]), {order})'
        for order, (label, measure) in enumerate(entries)
    ]
    lines.append(",\n".join(rows))
    lines += ["\t\t\t\t}", ""]
    return "\n".join(lines)


def relationships_tmdl() -> str:
    blocks = []
    for from_table, from_column, to_table, to_column in RELATIONSHIPS:
        blocks.append(
            f"relationship {tag('relationship', from_table, from_column, to_table)}\n"
            f"\tfromColumn: {from_table}.{from_column}\n"
            f"\ttoColumn: {to_table}.{to_column}\n"
        )
    return "\n".join(blocks)


def model_tmdl(table_names: list[str], parameter_names: list[str]) -> str:
    """
    ``PBI_QueryOrder`` lists only the tables that have a query. A calculated
    table named in it is refreshed as if it had an M partition, which fails on
    a model that otherwise loads, so the parameters are referenced but not
    ordered.
    """
    order = json.dumps(table_names)
    refs = "\n".join(f"ref table {name}" for name in table_names + parameter_names)
    return (
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n"
        "\n"
        f"annotation PBI_QueryOrder = {order}\n"
        "\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n"
        "\n"
        'annotation PBI_ProTooling = ["DevMode"]\n'
        "\n"
        f"{refs}\n"
    )


# --------------------------------------------------------------------------
# PBIR
# --------------------------------------------------------------------------

def literal(value) -> dict:
    """
    A formatting literal, with the type suffix Power BI requires.

    A number written as ``{"Value": "11"}`` is dropped by Desktop on the next
    save, with no error and no visible change until someone reopens the file.
    It has to be ``"11D"``. Strings are single-quoted *inside* the value;
    booleans are bare.
    """
    if isinstance(value, bool):
        return {"expr": {"Literal": {"Value": "true" if value else "false"}}}
    if isinstance(value, (int, float)):
        return {"expr": {"Literal": {"Value": f"{value}D"}}}
    return {"expr": {"Literal": {"Value": f"'{value}'"}}}


def field_expr(reference: str) -> tuple[dict, str, str]:
    """
    Parse ``table[column]`` or ``[Measure]`` into a PBIR field expression.

    Returns the expression, its ``queryRef`` and its ``nativeQueryRef`` -- all
    three have to agree or the visual binds to nothing and renders empty.
    """
    reference = reference.strip()
    if reference.startswith("["):
        name = reference.strip("[]")
        return (
            {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}},
                         "Property": name}},
            f"_Measures.{name}", name,
        )
    entity, _, rest = reference.partition("[")
    column = rest.rstrip("]")
    return (
        {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": column}},
        f"{entity}.{column}", column,
    )


# Tokens that are not words. Sentence-casing "sku" gives "Sku", which reads
# as a typo rather than as an abbreviation.
LABEL_TOKENS = {
    "pct": "%", "id": "ID", "sku": "SKU", "wtp": "WTP", "cogs": "COGS",
    "uom": "UoM", "moq": "MOQ", "yoy": "YoY", "asp": "ASP", "roi": "ROI",
    "wape": "WAPE", "mape": "MAPE", "rag": "RAG", "erp": "ERP", "qty": "Qty",
    "a": "A", "b": "B",
}

# Where sentence case is right but the source name carries a word that only
# meant something to the pipeline.
LABEL_OVERRIDES = {
    "fiscal_year_label": "Fiscal year",
    "category_a": "Category A",
    "month_name": "Month",
}


def display_name(column: str) -> str:
    """
    The label a column wears in a visual.

    A model built from CSVs inherits their spelling, and `customer_name` on a
    table header is the clearest sign that nobody looked at the report. This
    is applied per projection rather than by renaming the column, because the
    column name is also what DAX, the relationships and every `sortByColumn`
    refer to.
    """
    if column in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[column]
    # Already a display name: the what-if parameter columns are written by
    # hand ("Price change %") and re-casing them would only damage them.
    if " " in column or any(c.isupper() for c in column):
        return column
    words = [LABEL_TOKENS.get(w, w) for w in column.split("_")]
    first, rest = words[0], words[1:]
    first = first if first in LABEL_TOKENS.values() else first.capitalize()
    return " ".join([first, *rest]).strip()


def projection(reference: str, *, active: bool = False) -> dict:
    expression, query_ref, native = field_expr(reference)
    out = {"field": expression, "queryRef": query_ref, "nativeQueryRef": native}
    if not reference.strip().startswith("["):
        # Columns only. A measure is already named by hand, and an override
        # that repeats the name is a second place to keep in step.
        label = display_name(reference.split("[", 1)[1].rstrip("]"))
        if label != native:
            out["displayName"] = label
    if active:
        out["active"] = True
    return out


def colour(hex_code: str) -> dict:
    return {"solid": {"color": literal(hex_code)}}


def raw(value: str) -> dict:
    """A literal written exactly, for the integers (`8L`) some shape properties take."""
    return {"expr": {"Literal": {"Value": value}}}


def _off(*names: str) -> dict[str, list]:
    return {name: [{"properties": {"show": literal(False)}}] for name in names}


def _padding(size: int) -> list[dict]:
    return [{"properties": {side: literal(size) for side in ("top", "bottom", "left", "right")}}]


def _alt(text: str) -> list[dict]:
    return [{"properties": {"altText": literal(text)}}]


def _measure(name: str) -> dict:
    return field_expr(f"[{name}]")[0]


def _image(measure: str, alt: str, *, framed: bool) -> dict:
    containers = {"general": _alt(alt), "padding": _padding(0), **_off("title")}
    if not framed:
        containers.update(_off("background", "border", "dropShadow"))
    return {
        "visualType": "image",
        "objects": {"image": [{"properties": {
            "sourceType": literal("imageUrl"),
            "sourceUrl": {"expr": _measure(measure)},
            "fit": literal("Fit"),
        }}]},
        "visualContainerObjects": containers,
    }


def _button(text: dict, alt: str, link: dict) -> dict:
    return {
        "visualType": "actionButton",
        "objects": {
            "icon": [{"properties": {"show": literal(False)}, "selector": {"id": "default"}}],
            "text": [{"properties": {"show": literal(True), "text": text,
                                     "fontColor": colour(INK), "fontSize": literal(10)},
                      "selector": {"id": "default"}}],
            "fill": [{"properties": {"show": literal(True), "fillColor": colour(RAISED),
                                     "transparency": literal(0)},
                      "selector": {"id": "default"}},
                     {"properties": {"show": literal(True), "fillColor": colour(EDGE),
                                     "transparency": literal(0)},
                      "selector": {"id": "hover"}}],
            "outline": [{"properties": {"show": literal(True), "lineColor": colour(EDGE),
                                        "weight": literal(1)},
                         "selector": {"id": "default"}},
                        {"properties": {"show": literal(True), "lineColor": colour(ACCENT),
                                        "weight": literal(1)},
                         "selector": {"id": "hover"}}],
            "shape": [{"properties": {"tileShape": literal("rectangleRounded"),
                                      "roundEdge": raw("8L")}}],
        },
        "visualContainerObjects": {
            # visualLink is how a button acts; in Desktop's edit mode it takes
            # Ctrl+click, in reading view and the Service a plain click.
            "visualLink": [{"properties": {"show": literal(True), **link}}],
            "general": _alt(alt),
            "padding": _padding(0),
            **_off("background", "border", "dropShadow", "title"),
        },
    }


def chrome_visual_json(spec: dict, index: int) -> dict:
    """The frame report_chrome adds, and every card drawn as an SVG tile."""
    kind = spec["type"]
    x, y, width, height = spec["pos"]
    z = 30000 + index if (spec.get("group") or kind == "filter_panel") else 1000 + index
    if spec.get("group"):
        # A group's children are positioned relative to the group. Absolute
        # page coordinates put every one of them off the canvas.
        x, y = x - PANEL_X, y - PANEL_Y
    doc: dict = {
        "$schema": SCHEMA["visual"],
        "name": spec["id"],
        "position": {"x": x, "y": y, "z": z, "height": height, "width": width, "tabOrder": z},
    }
    if kind == "filter_panel":
        doc["visualGroup"] = {"displayName": "Filter panel", "groupMode": "ScaleMode",
                              "objects": {"background": [{"properties": {"show": literal(False)}}]}}
        doc["isHidden"] = True
        return doc

    if kind == "card":
        alt = spec["alt"]
        if alt.startswith("Card."):
            alt = "KPI tile." + alt[len("Card."):]
        visual = _image(tile_measure(spec), alt, framed=True)
    elif kind == "page_header":
        visual = _image(spec["measure"], spec["alt"], framed=False)
    elif kind == "nav":
        visual = _button(literal(spec["label"]), spec["alt"],
                         {"type": literal("PageNavigation"),
                          "navigationSection": literal(spec["target"])})
    elif kind == "filters_button":
        visual = _button({"expr": _measure(spec["measure"])}, spec["alt"],
                         {"type": literal("Bookmark"), "bookmark": literal(spec["bookmark"])})
    elif kind == "panel_close":
        visual = _button(literal(spec["label"]), spec["alt"],
                         {"type": literal("Bookmark"), "bookmark": literal(spec["bookmark"])})
    elif kind == "panel_clear":
        visual = _button(literal(spec["label"]), spec["alt"], {"type": literal("ClearAllSlicers")})
    elif kind == "panel_background":
        visual = {
            "visualType": "shape",
            "objects": {
                "shape": [{"properties": {"tileShape": literal("rectangleRounded"),
                                          "rectangleRoundedCurve": raw("12L")},
                           "selector": {"id": "default"}}],
                "fill": [{"properties": {"show": literal(True), "fillColor": colour(PANEL),
                                         "transparency": literal(0)},
                          "selector": {"id": "default"}}],
                "outline": [{"properties": {"show": literal(True), "lineColor": colour(EDGE),
                                            "weight": literal(1)},
                             "selector": {"id": "default"}}],
            },
            "visualContainerObjects": {"general": _alt(spec["alt"]), **_off("title")},
        }
    elif kind == "panel_title":
        visual = {
            "visualType": "textbox",
            "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{
                "value": spec["text"],
                "textStyle": {"fontFamily": "Segoe UI Semibold", "fontSize": "13pt", "color": INK},
            }]}]}}]},
            "visualContainerObjects": {"general": _alt(spec["alt"]), "padding": _padding(0),
                                       **_off("background", "border", "dropShadow", "title")},
        }
    else:
        raise ValueError(f"no writer for a {kind!r} visual")
    if spec.get("group"):
        doc["parentGroupName"] = spec["group"]
    doc["visual"] = visual
    return doc


def visual_json(spec: dict, index: int) -> dict:
    kind = spec["type"]
    if kind == "card" or kind in CHROME_KINDS:
        return chrome_visual_json(spec, index)
    visual_type = VISUAL_TYPES[kind]
    x, y, width, height = spec["pos"]
    z = 1000 + index
    if spec.get("group"):
        # A slicer in the filter panel: above the page, relative to its group.
        z = 30000 + index
        x, y = x - PANEL_X, y - PANEL_Y

    query_state: dict[str, dict] = {}
    if kind == "card":
        query_state["Values"] = {"projections": [projection(spec["field"])]}
    elif kind == "slicer":
        query_state["Values"] = {"projections": [projection(spec["field"], active=True)]}
    elif kind == "table":
        query_state["Values"] = {
            "projections": [projection(c) for c in spec["columns"]]
        }
    elif kind == "matrix":
        # Rows / Columns / Values, which is what makes this a cross-tab rather
        # than a long table. A measure put in "Columns" is accepted and pivots
        # on nothing, giving one column headed by the measure name.
        query_state["Rows"] = {"projections": [projection(spec["rows"], active=True)]}
        query_state["Columns"] = {
            "projections": [projection(spec["columns_by"], active=True)]
        }
        query_state["Values"] = {"projections": [projection(v) for v in spec["values"]]}
    elif kind == "scatter":
        # A scatter's identity field goes in the role named "Category" -- the
        # field well Desktop labels "Details". Putting it in "Details" binds
        # nothing and the chart draws one point.
        query_state["Category"] = {"projections": [projection(spec["category"], active=True)]}
        query_state["X"] = {"projections": [projection(spec["x"])]}
        query_state["Y"] = {"projections": [projection(f) for f in spec["y"]]}
        if spec.get("size"):
            query_state["Size"] = {"projections": [projection(spec["size"])]}
    elif kind == "treemap":
        # Group and Values, not Category and Y. A treemap given the cartesian
        # role names binds nothing and draws an empty box with a title on it --
        # no error, no warning, and it passes every structural check because
        # the fields it names all exist.
        query_state["Group"] = {"projections": [projection(spec["x"], active=True)]}
        query_state["Values"] = {"projections": [projection(f) for f in spec["y"]]}
    else:
        query_state["Category"] = {"projections": [projection(spec["x"], active=True)]}
        query_state["Y"] = {"projections": [projection(f) for f in spec["y"]]}
        if spec.get("series"):
            query_state["Series"] = {"projections": [projection(spec["series"], active=True)]}

    container_objects: dict[str, list] = {}
    if spec.get("title"):
        container_objects["title"] = [
            {"properties": {"text": literal(spec["title"]), "show": literal(True)}}
        ]
    if spec.get("subtitle"):
        expression, _, _ = field_expr(spec["subtitle"])
        container_objects["subTitle"] = [
            {"properties": {"text": {"expr": expression}, "show": literal(True)}}
        ]
    container_objects["general"] = [{"properties": {"altText": literal(spec["alt"])}}]

    objects: dict[str, list] = {}
    if kind in ("bar", "column", "stacked_column", "line", "area", "waterfall"):
        objects["categoryAxis"] = [{"properties": {"showAxisTitle": literal(False)}}]
        objects["valueAxis"] = [{"properties": {"showAxisTitle": literal(False)}}]
    if kind in ("bar", "column", "donut"):
        objects["labels"] = [{"properties": {"show": literal(True)}}]
    if kind == "slicer":
        # slicer.data.mode, not slicer.mode. The property dictionary is the
        # report theme schema, and a name that is merely plausible validates
        # and does nothing.
        objects["data"] = [{"properties": {"mode": literal("Dropdown")}}]
        objects["items"] = [{"properties": {"textSize": literal(10)}}]

    if kind == "slicer" and spec.get("group"):
        objects["items"] = [{"properties": {"textSize": literal(10), "background": colour(PANEL),
                                            "fontColor": colour(INK)}}]
        container_objects["background"] = [{"properties": {
            "show": literal(True), "color": colour(SURFACE), "transparency": literal(0)}}]
        container_objects["padding"] = _padding(8)

    query: dict = {"queryState": query_state}
    if spec.get("sort"):
        # A waterfall, a traffic light and a banded cross-tab all mean their
        # own order, not the order of their values. `sortByColumn` in the model
        # is necessary and not sufficient: it decides how the *column* sorts,
        # while the visual keeps sorting by its measure until this says
        # otherwise. Both are needed, and only one of them is visible in TMDL.
        field, direction = spec["sort"]
        expression, _ref, _native = field_expr(field)
        # The sort field is the bare query expression, not the `{"expr": ...}`
        # wrapper a formatting property takes. The schema catches the
        # difference; Desktop would have ignored the sort silently.
        query["sortDefinition"] = {
            "sort": [{"field": expression, "direction": direction}],
            "isDefaultSort": False,
        }

    body: dict = {
        "visualType": visual_type,
        "query": query,
        "drillFilterOtherVisuals": True,
        "visualContainerObjects": container_objects,
    }
    if objects:
        body["objects"] = objects

    doc = {
        "$schema": SCHEMA["visual"],
        "name": spec["id"],
        "position": {"x": x, "y": y, "z": z, "height": height, "width": width,
                     "tabOrder": z},
        "visual": body,
    }
    if spec.get("group"):
        doc["parentGroupName"] = spec["group"]
    return doc


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------

# The dark palette, shared with the Streamlit app. Same eight hues, same fixed
# order, same surface -- a chart in the app and the same chart on the dashboard
# are the same colour, which is the only reason anyone believes they are the
# same number.
SURFACE = "#141416"          # visual background; the palette is validated on it
PLANE = "#0c0c0f"            # the canvas behind the visuals
HAIRLINE = "#26262a"
INK = "#f2f2f4"
INK_2 = "#a6a6ad"
INK_3 = "#7d7d85"
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500",
          "#d55181", "#008300", "#9085e9", "#e66767"]


def theme_json() -> dict:
    """
    The validated palette, as a Power BI theme.

    The slot order is the colour-vision-safety mechanism, not decoration:
    adjacent slots were checked for separation under protanopia, deuteranopia
    and tritanopia against this surface, and reordering them breaks that
    without changing a hex.

    `minimum`/`center`/`maximum` run dark to light, the opposite way round to
    the light theme. A conditional-format scale reads "nearest the surface" as
    "near zero", and on a dark canvas that end is the dark one -- keeping the
    light theme's order would put the *strongest* colour on the smallest
    number, which no reader would think to question.
    """
    theme = {
        # The theme's name has to be its file name, .json included: report.json
        # refers to it that way, and Microsoft's validator reports the mismatch
        # as one that stops the theme loading.
        "name": THEME,
        "dataColors": list(SERIES),
        "background": PLANE,
        "foreground": INK,
        "tableAccent": SERIES[0],
        "good": "#0ca30c",
        "neutral": "#fab219",
        "bad": "#d03b3b",
        "minimum": "#0d366b",
        "center": "#3987e5",
        "maximum": "#cde2fb",
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": INK},
            "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": INK_2},
            "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 30, "color": INK},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": SURFACE}},
                                    "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": HAIRLINE}},
                                "radius": 8}],
                    "visualHeader": [{"show": False}],
                    "title": [{"show": True, "fontColor": {"solid": {"color": INK}},
                               "fontSize": 12, "alignment": "left"}],
                    "categoryAxis": [{"gridlineShow": False,
                                      "labelColor": {"solid": {"color": INK_3}},
                                      "fontSize": 9}],
                    "valueAxis": [{"gridlineColor": {"solid": {"color": HAIRLINE}},
                                   "labelColor": {"solid": {"color": INK_3}},
                                   "fontSize": 9}],
                    "legend": [{"show": True, "position": "Top",
                                "labelColor": {"solid": {"color": INK_2}},
                                "fontSize": 9}],
                    "labels": [{"color": {"solid": {"color": INK_2}}, "fontSize": 9}],
                }
            },
            "card": {
                "*": {
                    "labels": [{"color": {"solid": {"color": INK}}, "fontSize": 26}],
                    "categoryLabels": [{"color": {"solid": {"color": INK_3}},
                                        "fontSize": 10}],
                }
            },
            "tableEx": {
                "*": {
                    "grid": [{"gridVertical": False,
                              "gridHorizontalColor": {"solid": {"color": HAIRLINE}},
                              "outlineColor": {"solid": {"color": HAIRLINE}}}],
                    "columnHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                       "backColor": {"solid": {"color": SURFACE}},
                                       "fontSize": 9}],
                    "values": [{"fontColor": {"solid": {"color": INK}},
                                "backColor": {"solid": {"color": SURFACE}},
                                "fontSize": 9}],
                }
            },
            "pivotTable": {
                "*": {
                    "grid": [{"gridVertical": False,
                              "gridHorizontalColor": {"solid": {"color": HAIRLINE}},
                              "outlineColor": {"solid": {"color": HAIRLINE}}}],
                    "columnHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                       "backColor": {"solid": {"color": SURFACE}},
                                       "fontSize": 9}],
                    "rowHeaders": [{"fontColor": {"solid": {"color": INK_2}},
                                    "backColor": {"solid": {"color": SURFACE}},
                                    "fontSize": 9}],
                    "values": [{"fontColor": {"solid": {"color": INK}},
                                "backColor": {"solid": {"color": SURFACE}},
                                "fontSize": 9}],
                }
            },
            "slicer": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": SURFACE}},
                                    "transparency": 0}],
                    # `items` styles the list rows; a dropdown slicer draws its
                    # closed control from these instead, which is why every
                    # slicer came out white on a dark page.
                    "items": [{"fontColor": {"solid": {"color": INK}},
                               "background": {"solid": {"color": "#1b1b20"}}}],
                    # Off. It prints the *field* name under a visual title
                    # that already names the filter, and the two stacked leave
                    # a 76px slicer with its dropdown hanging off the bottom.
                    "header": [{"show": False}],
                }
            },
        },
    }
    theme["visualStyles"].update(theme_styles())
    return theme


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" so the committed project is identical on Windows and Linux;
    # without it every file in the report differs by a carriage return per line
    # and `--check` fails in CI for a reason nobody can see in a diff.
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build(out_dir: Path, data_root: Path) -> dict[str, int]:
    model_dir = out_dir / f"{PROJECT}.SemanticModel"
    report_dir = out_dir / f"{PROJECT}.Report"

    # --- semantic model ---------------------------------------------------
    table_names = []
    for name, meta in TABLES.items():
        csv_path = data_root / meta["source"] / f"{name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(
                f"{csv_path} is missing. Run `python -m seed.generate_market` and "
                "`python -m engine.build_pricing_analytics` first."
            )
        columns = infer_columns(csv_path)
        write_text(model_dir / "definition" / "tables" / f"{name}.tmdl",
                   table_tmdl(name, meta, columns))
        table_names.append(name)

    write_text(model_dir / "definition" / "tables" / "_Measures.tmdl", measures_tmdl())
    table_names.append("_Measures")

    parameter_names = []
    for table, column, low, high, step, fmt, _measure in WHATIF_PARAMETERS:
        write_text(model_dir / "definition" / "tables" / f"{table}.tmdl",
                   whatif_tmdl(table, column, low, high, step, fmt))
        parameter_names.append(table)
    for table, column, entries in FIELD_PARAMETERS:
        write_text(model_dir / "definition" / "tables" / f"{table}.tmdl",
                   field_parameter_tmdl(table, column, entries))
        parameter_names.append(table)

    write_text(model_dir / "definition" / "relationships.tmdl", relationships_tmdl())
    write_text(model_dir / "definition" / "model.tmdl",
               model_tmdl(table_names, parameter_names))
    write_text(model_dir / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1606\n")
    write_text(
        model_dir / "definition" / "expressions.tmdl",
        f'expression DataPath = "{data_root}" meta [IsParameterQuery=true, Type="Text", '
        "IsParameterQueryRequired=true]\n"
        f"\tlineageTag: {tag('expression', 'DataPath')}\n"
        "\n"
        "\tannotation PBI_ResultType = Text\n",
    )
    write_json(model_dir / ".platform", {
        "$schema": SCHEMA["platform"],
        "metadata": {"type": "SemanticModel", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": tag("model", PROJECT)},
    })
    write_json(model_dir / "definition.pbism", {"$schema": SCHEMA["pbism"],
                                                "version": "4.2", "settings": {}})

    # --- report -----------------------------------------------------------
    visual_count = 0
    for page_index, page in enumerate(PAGES):
        page_dir = report_dir / "definition" / "pages" / page["name"]
        write_json(page_dir / "page.json", {
            "$schema": SCHEMA["page"],
            "name": page["name"],
            "displayName": page["display"],
            "displayOption": "FitToPage",
            "height": 720,
            "width": 1280,
        })
        for visual_index, spec in enumerate(page["visuals"]):
            spec = dict(spec)
            spec["id"] = spec.get("id") or f"v{page_index + 1:02d}{visual_index + 1:02d}"
            write_json(page_dir / "visuals" / spec["id"] / "visual.json",
                       visual_json(spec, page_index * 100 + visual_index))
            visual_count += 1

    write_json(report_dir / "definition" / "pages" / "pages.json", {
        "$schema": SCHEMA["pages"],
        "pageOrder": [p["name"] for p in PAGES],
        "activePageName": PAGES[0]["name"],
    })
    saved = bookmarks(PAGES)
    for bookmark in saved:
        write_json(report_dir / "definition" / "bookmarks" / f"{bookmark['name']}.bookmark.json",
                   {"$schema": SCHEMA["bookmark"], **bookmark})
    if saved:
        write_json(report_dir / "definition" / "bookmarks" / "bookmarks.json",
                   {"$schema": SCHEMA["bookmarks"], "items": [{"name": b["name"]} for b in saved]})
    write_json(report_dir / "definition" / "version.json",
               {"$schema": SCHEMA["version"], "version": "2.0.0"})
    # `reportVersionAtImport` is required on every entry in themeCollection.
    # Omitting it fails the published report schema, which is the whole reason
    # for declaring a $schema at all -- Desktop itself opens the file happily.
    versions = {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"}
    write_json(report_dir / "definition" / "report.json", {
        "$schema": SCHEMA["report"],
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": versions,
                          "type": "SharedResources"},
            "customTheme": {"name": THEME, "reportVersionAtImport": versions,
                            "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json",
                        "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": THEME, "path": THEME, "type": "CustomTheme"}]},
        ],
        "settings": {
            "useStylableVisualContainerHeader": True,
            "defaultDrillFilterOtherVisuals": True,
            "useEnhancedTooltips": True,
        },
    })
    write_json(report_dir / "StaticResources" / "RegisteredResources" / THEME, theme_json())
    # definition.pbir binds the report to its semantic model, and the schema
    # calls it "required" in as many words. Without it Power BI Desktop refuses
    # the whole project on open -- "Required artifact is missing" -- and every
    # other check in this repo passed while it was absent, because they all
    # validate the files that ARE there. Nothing had opened the project.
    write_json(report_dir / "definition.pbir", {
        "$schema": SCHEMA["pbir"],
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
    })
    write_json(report_dir / ".platform", {
        "$schema": SCHEMA["platform"],
        "metadata": {"type": "Report", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": tag("report", PROJECT)},
    })

    write_json(out_dir / f"{PROJECT}.pbip", {
        "$schema": SCHEMA["pbip"],
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })

    return {"tables": len(table_names), "parameters": len(parameter_names),
            "measures": len(MEASURES), "relationships": len(RELATIONSHIPS),
            "pages": len(PAGES), "visuals": visual_count}


# The one line in the project that cannot be the same on two machines. The
# model reads CSVs off disk through an absolute path, so the committed file
# names whichever machine generated it -- and a byte-for-byte gate over it can
# only ever pass there. Everything else in the project is machine-independent
# and is still compared byte for byte;
# tests/test_powerbi_model.py::test_only_the_data_path_varies_between_machines
# regenerates against a different root and asserts that this exemption covers
# exactly one line of one file, so it cannot quietly widen.
DATA_PATH_LINE = re.compile(r'^expression DataPath = ".*?" meta', re.MULTILINE)


def _comparable(path: Path) -> str:
    """A file's contents with the machine-specific data root normalised out."""
    text = path.read_text(encoding="utf-8")
    if path.name == "expressions.tmdl":
        return DATA_PATH_LINE.sub('expression DataPath = "<data root>" meta', text)
    return text


def differences(left: Path, right: Path) -> list[str]:
    """Every path under `left` whose file differs from `right`, or is missing."""
    out = []
    for path in sorted(left.rglob("*")):
        if path.is_dir():
            continue
        # localSettings and the .abf cache are Desktop's machine-local state and
        # are gitignored; they are not part of what the generator owns.
        if ".pbi" in path.parts:
            continue
        relative = path.relative_to(left)
        other = right / relative
        if not other.exists():
            out.append(f"missing: {relative}")
        elif path.name == "expressions.tmdl":
            if _comparable(path) != _comparable(other):
                out.append(f"differs: {relative}")
        elif not filecmp.cmp(path, other, shallow=False):
            out.append(f"differs: {relative}")
    for path in sorted(right.rglob("*")):
        if path.is_dir() or ".pbi" in path.parts:
            continue
        relative = path.relative_to(right)
        if relative.name in KEEP:
            continue
        if not (left / relative).exists():
            out.append(f"stale: {relative}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp dir and diff against the committed project")
    ap.add_argument("--out-dir", type=Path, default=PBIP_DIR)
    ap.add_argument("--data-root", type=Path, default=ROOT)
    args = ap.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory() as temporary:
            stats = build(Path(temporary), args.data_root)
            drift = differences(Path(temporary), args.out_dir)
        if drift:
            print("the committed project does not match the spec:", file=sys.stderr)
            for line in drift[:40]:
                print(f"  {line}", file=sys.stderr)
            print("\nrun: python -m powerbi.build_pbip", file=sys.stderr)
            return 1
        print(f"pbip matches the spec ({stats['visuals']} visuals, {stats['tables']} tables)")
        return 0

    if args.out_dir.exists():
        # A page removed from the spec leaves its directory behind otherwise,
        # and Power BI opens it as a page that nothing generates.
        for child in args.out_dir.iterdir():
            if child.name.startswith(".") or child.name in KEEP:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    stats = build(args.out_dir, args.data_root)
    print(f"wrote {args.out_dir}")
    for key, value in stats.items():
        print(f"  {key:16s} {value:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
