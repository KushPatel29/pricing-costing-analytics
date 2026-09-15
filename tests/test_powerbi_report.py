"""
Hold the generated report to the model it binds to, and to its own schema.

Power BI fails silently on a report definition. A visual bound to a field that
does not exist renders empty; a mistyped property name is ignored; a numeric
formatting literal written without its type suffix is dropped by Desktop on the
next save. None of that raises, and none of it shows in a screenshot until
someone notices the chart has been blank for a month.

The checks here are the ones that can be made offline. The networked one --
validating each file against the schema it names -- is a loud skip when
Microsoft's schema host cannot be reached, because that is not a defect in this
repo; a 404 on a single version while the others answer *is* a failure, because
that is a version nobody published.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pytest

from powerbi.build_pbip import display_name
from powerbi.model_spec import (
    MEASURES,
    TABLES,
    whatif_columns,
)
from powerbi.report_chrome import ui_measures
from powerbi.report_spec import PAGES, VISUAL_TYPES

ROOT = Path(__file__).resolve().parent.parent
PBIP = ROOT / "powerbi" / "pbip"
REPORT = PBIP / "PricingAnalytics.Report"
TIMEOUT = 30

MEASURE_NAMES = {name for name, *_ in MEASURES}

# Visual types verified to render from a hand-authored PBIR. A type outside
# this set is not necessarily wrong, but it has not been seen to work, and a
# wrong one draws nothing rather than erroring.
VERIFIED_VISUAL_TYPES = {
    "card", "clusteredBarChart", "clusteredColumnChart", "columnChart", "lineChart",
    "areaChart", "scatterChart", "donutChart", "treemap", "waterfallChart",
    "tableEx", "pivotTable", "slicer", "gauge", "funnel",
    # The frame: SVG tiles and headers, page and bookmark buttons, the panel.
    # Each was rendered and clicked in Desktop before it was generated here.
    "image", "actionButton", "shape", "textbox",
}


def visual_files() -> list[Path]:
    return sorted(REPORT.rglob("visual.json"))


def _visual_type(path: Path) -> str | None:
    """None for a visual group, which has no visual of its own."""
    return json.loads(path.read_text(encoding="utf-8")).get("visual", {}).get("visualType")


def _image_measure(body: dict) -> str:
    return body["objects"]["image"][0]["properties"]["sourceUrl"]["expr"]["Measure"]["Property"]


def _binds_a_model_field(path: Path) -> bool:
    """A chart, table, slicer or KPI tile -- not the frame drawn around them."""
    body = json.loads(path.read_text(encoding="utf-8")).get("visual")
    if body is None or body["visualType"] in {"actionButton", "shape", "textbox"}:
        return False
    return body["visualType"] != "image" or _image_measure(body).startswith("KPI SVG ")


def _drawn(name: str) -> str:
    """The model measure a KPI tile draws, read out of its SVG measure's name."""
    if not name.startswith("KPI SVG "):
        return name
    return name.removeprefix("KPI SVG ").split(" | ")[0].rsplit(" @", 1)[0]


VISUALS = visual_files()
ALL_CASES = [
    pytest.param(p, id=f"{p.parents[2].name}/{p.parent.name}") for p in VISUALS
]
VISUAL_CASES = [case for case in ALL_CASES if _binds_a_model_field(case.values[0])]
CHROME_CASES = [case for case in ALL_CASES
                if _visual_type(case.values[0]) and not _binds_a_model_field(case.values[0])]
FORMATS = {name: fmt for name, _dax, fmt, _folder in MEASURES}
UI_MEASURE_NAMES = {name for name, *_ in ui_measures(PAGES, FORMATS)}


@pytest.fixture(scope="module")
def source_columns() -> dict[str, set[str]]:
    """Imported tables read from their CSV; what-if tables from the spec, since
    they are calculated and have no CSV to read."""
    out = {
        name: set(pd.read_csv(ROOT / meta["source"] / f"{name}.csv", nrows=1).columns)
        for name, meta in TABLES.items()
    }
    out.update(whatif_columns())
    return out


def test_there_are_visuals_to_check():
    """Every assertion below is vacuous if the traversal finds nothing."""
    assert len(VISUALS) >= 60, f"only {len(VISUALS)} visuals found under {REPORT}"
    assert len(VISUALS) == sum(len(page["visuals"]) for page in PAGES)


# --------------------------------------------------------------------------
# Binding
# --------------------------------------------------------------------------

def _projections(visual: dict):
    body = visual["visual"]
    if body["visualType"] == "image":
        # A KPI tile binds through the SVG measure it draws, in objects.image
        # rather than in a query -- so that is where its binding is read from.
        name = _image_measure(body)
        yield "image", {
            "field": {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}},
                                  "Property": name}},
            "queryRef": f"_Measures.{name}", "nativeQueryRef": name,
        }
        return
    for role, well in body["query"]["queryState"].items():
        for projection in well.get("projections", []):
            yield role, projection


@pytest.mark.parametrize("path", VISUAL_CASES)
def test_every_field_a_visual_binds_actually_exists(path, source_columns):
    visual = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for role, projection in _projections(visual):
        field = projection["field"]
        if "Measure" in field:
            name = field["Measure"]["Property"]
            if name not in MEASURE_NAMES | UI_MEASURE_NAMES:
                problems.append(f"{role}: measure [{name}] does not exist")
        else:
            entity = field["Column"]["Expression"]["SourceRef"]["Entity"]
            column = field["Column"]["Property"]
            if entity not in source_columns:
                problems.append(f"{role}: table {entity} is not in the model")
            elif column not in source_columns[entity]:
                problems.append(f"{role}: {entity}[{column}] does not exist")
    assert not problems, f"{path.parent.name} binds to nothing: {problems}"


@pytest.mark.parametrize("path", VISUAL_CASES)
def test_query_refs_agree_with_the_field_they_describe(path):
    """
    ``queryRef`` and ``nativeQueryRef`` are denormalised copies of the field
    expression. When they disagree with it the visual binds to nothing and
    renders empty -- which is why they are generated rather than typed.
    """
    visual = json.loads(path.read_text(encoding="utf-8"))
    for role, projection in _projections(visual):
        field = projection["field"]
        if "Measure" in field:
            expected = f"_Measures.{field['Measure']['Property']}"
            native = field["Measure"]["Property"]
        else:
            entity = field["Column"]["Expression"]["SourceRef"]["Entity"]
            expected = f"{entity}.{field['Column']['Property']}"
            native = field["Column"]["Property"]
        assert projection["queryRef"] == expected, f"{role}: queryRef disagrees"
        assert projection["nativeQueryRef"] == native, f"{role}: nativeQueryRef disagrees"


@pytest.mark.parametrize("path", VISUAL_CASES)
def test_every_bound_column_carries_a_readable_label(path):
    """
    A column arrives in the model spelled the way the CSV spelled it, and that
    is the name a visual shows: opening the report showed table headers reading
    ``customer_name`` and ``cost_element``, and slicers titled "Fiscal year"
    sitting over a field called ``fiscal_year_label``.

    Measures are exempt -- they are named by hand and a ``displayName`` that
    repeats the name is one more thing to keep in step.
    """
    visual = json.loads(path.read_text(encoding="utf-8"))
    unlabelled = []
    for role, projection in _projections(visual):
        if "Column" not in projection["field"]:
            continue
        column = projection["field"]["Column"]["Property"]
        label = projection.get("displayName", column)
        if label != display_name(column):
            unlabelled.append(f"{role}: {column} shows as {label!r}")
    assert not unlabelled, (
        f"{path.parent.name} shows a source column name:\n  "
        + "\n  ".join(unlabelled)
    )


@pytest.mark.parametrize("path", VISUAL_CASES)
def test_every_visual_binds_something(path):
    visual = json.loads(path.read_text(encoding="utf-8"))
    assert list(_projections(visual)), f"{path.parent.name} has an empty field well"


def test_every_visual_type_is_one_that_renders():
    used = {_visual_type(p) for p in VISUALS} - {None}
    unknown = sorted(used - VERIFIED_VISUAL_TYPES)
    assert not unknown, f"visual types not known to render from hand-authored PBIR: {unknown}"
    assert used <= set(VISUAL_TYPES.values())


def test_a_matrix_is_a_cross_tab_rather_than_a_long_table():
    """
    Rows, Columns *and* Values. A matrix with no Columns role is a table with
    extra chrome -- it renders, it is not wrong, and it is not the visual the
    page needed either.
    """
    matrices = [
        json.loads(p.read_text(encoding="utf-8")) for p in VISUALS
        if _visual_type(p) == "pivotTable"
    ]
    assert matrices, "no matrix visuals to check"
    for visual in matrices:
        roles = set(visual["visual"]["query"]["queryState"])
        assert {"Rows", "Columns", "Values"} <= roles, f"matrix roles: {roles}"


def test_a_scatter_puts_its_identity_field_in_the_category_role():
    """
    Power BI's scatter labels the field well "Details" but names the role
    "Category". Putting the identity field under "Details" binds nothing and
    the chart draws a single point at the average of everything.
    """
    scatters = [
        json.loads(p.read_text(encoding="utf-8")) for p in VISUALS
        if _visual_type(p) == "scatterChart"
    ]
    assert scatters, "no scatter charts to check"
    for visual in scatters:
        roles = set(visual["visual"]["query"]["queryState"])
        assert "Category" in roles, f"scatter without a Category role: {roles}"
        assert {"X", "Y"} <= roles


# --------------------------------------------------------------------------
# Formatting properties
# --------------------------------------------------------------------------

def _literals(node, found=None):
    """Every ``Literal.Value`` anywhere in a visual."""
    found = [] if found is None else found
    if isinstance(node, dict):
        if "Literal" in node and isinstance(node["Literal"], dict):
            found.append(node["Literal"].get("Value"))
        for value in node.values():
            _literals(value, found)
    elif isinstance(node, list):
        for item in node:
            _literals(item, found)
    return found


@pytest.mark.parametrize("path", ALL_CASES)
def test_numeric_literals_carry_their_type_suffix(path):
    """
    A property written as ``{"Value": "11"}`` is DROPPED by Desktop on the next
    save, with no error. It has to be ``"11D"``. Strings are single-quoted
    inside the value; booleans are bare.
    """
    bad = []
    for value in _literals(json.loads(path.read_text(encoding="utf-8"))):
        if not isinstance(value, str):
            continue
        if value in ("true", "false"):
            continue
        if value.startswith("'") and value.endswith("'"):
            continue
        if re.fullmatch(r"-?\d+(\.\d+)?[DLM]", value):
            continue
        bad.append(value)
    assert not bad, (
        f"{path.parent.name}: literals that Desktop will drop or misread: {bad}"
    )


@pytest.mark.parametrize("path", VISUAL_CASES)
def test_every_visual_has_alt_text_naming_a_field_it_binds(path):
    """
    Alt text lives at ``visualContainerObjects.general[0].properties.altText``.
    Boilerplate would pass a presence check, so this also demands the
    description mention something the visual actually binds -- which catches a
    plausible sentence pasted onto the wrong chart.
    """
    visual = json.loads(path.read_text(encoding="utf-8"))
    general = visual["visual"].get("visualContainerObjects", {}).get("general", [])
    assert general, f"{path.parent.name} has no alt text"
    alt = general[0]["properties"]["altText"]["expr"]["Literal"]["Value"].strip("'")
    assert len(alt) > 25, f"{path.parent.name}: alt text is too short to be a description"

    def normalise(text: str) -> str:
        # Field names carry punctuation a sentence does not: "Pocket margin %",
        # "Margin gap $", "quantity_units". Compare on letters and digits only, so
        # the check is about naming the field rather than about typography.
        return re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("_", " ")).split()

    haystack = " ".join(normalise(alt))
    bound = [_drawn(projection["nativeQueryRef"]) for _, projection in _projections(visual)]
    assert any(" ".join(normalise(name)) in haystack for name in bound), (
        f"{path.parent.name}: alt text names none of the fields it binds "
        f"({bound}); alt text was {alt!r}"
    )


SLICER_CASES = [
    case for case in VISUAL_CASES
    if json.loads(case.values[0].read_text(encoding="utf-8"))["visual"]["visualType"] == "slicer"
]


@pytest.mark.parametrize("path", SLICER_CASES)
def test_a_slicer_says_how_it_renders(path):
    """Parametrised over the slicers only. A test that skips on 58 of 75 cases
    reports as a suite that mostly does not run."""
    visual = json.loads(path.read_text(encoding="utf-8"))
    mode = visual["visual"]["objects"]["data"][0]["properties"]["mode"]
    assert mode["expr"]["Literal"]["Value"] == "'Dropdown'", (
        "slicer.data.mode -- not slicer.mode, which validates and does nothing"
    )


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES, ids=[p["name"] for p in PAGES])
def test_nothing_falls_off_the_canvas(page):
    """The canvas is 1280x720 at FitToPage; anything past it is cropped."""
    overflowing = []
    for spec in page["visuals"]:
        x, y, width, height = spec["pos"]
        if x + width > 1280 or x < 0:
            overflowing.append(f"{spec.get('title', spec['type'])}: x {x}+{width}")
    assert not overflowing, f"{page['name']}: past the right edge: {overflowing}"


@pytest.mark.parametrize("page", PAGES, ids=[p["name"] for p in PAGES])
def test_nothing_falls_off_the_bottom(page):
    """
    A 76-pixel slicer whose top is at y=712 shows eight pixels of itself. It
    reads as a missing filter rather than a clipped one, and no test that only
    looks at the right edge will ever mention it.
    """
    overflowing = []
    for spec in page["visuals"]:
        _x, y, _width, height = spec["pos"]
        if y + height > 720 or y < 0:
            overflowing.append(f"{spec.get('title', spec['type'])}: y {y}+{height}")
    assert not overflowing, f"{page['name']}: past the bottom edge: {overflowing}"


@pytest.mark.parametrize("page", PAGES, ids=[p["name"] for p in PAGES])
def test_no_two_visuals_overlap(page):
    """
    Two visuals on the same rectangle look like one visual with the other
    hidden underneath, and the hidden one is never noticed again.
    """
    # The filter panel is hidden until opened and floats over the page by
    # design; test_the_filter_panel_holds_its_members covers it instead.
    boxes = [(spec["pos"], spec.get("title") or spec.get("field") or spec["type"])
             for spec in page["visuals"]
             if not spec.get("group") and spec["type"] != "filter_panel"]
    collisions = []
    for i, ((x1, y1, w1, h1), name1) in enumerate(boxes):
        for (x2, y2, w2, h2), name2 in boxes[i + 1:]:
            if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                collisions.append(f"{name1} over {name2}")
    assert not collisions, f"{page['name']}: overlapping visuals: {collisions}"


def test_a_kpi_tile_is_tall_enough_to_read():
    """
    A legacy card with a reference subtitle needed 118px or its label clipped.
    A tile is an SVG drawn at the tile's own aspect and scaled to fit, so what it
    needs is height enough for its figure to stay legible once the page header
    has taken the top of the canvas.
    """
    short = [spec["field"] for page in PAGES for spec in page["visuals"]
             if spec["type"] == "card" and spec["pos"][3] < 96]
    assert not short, f"KPI tiles too short to read: {short}"


def test_every_tile_draws_a_measure_the_model_has():
    tiles = {spec["field"].strip("[]") for page in PAGES for spec in page["visuals"]
             if spec["type"] == "card"}
    assert tiles, "no KPI tiles to check"
    missing = sorted(tiles - MEASURE_NAMES)
    assert not missing, f"tiles drawing measures that do not exist: {missing}"


def test_every_svg_measure_encodes_percent_before_hash():
    """
    `%` first. A literal "73.6%" left in the data URI breaks its decoding, the
    `%23` colours stay encoded, and every fill renders black while the shapes
    and text still draw -- it looks like a colour bug and is an encoding one.
    """
    images = [(name, dax) for name, dax, image in ui_measures(PAGES, FORMATS) if image]
    assert images, "no SVG measures to check"
    order = 'SUBSTITUTE(SUBSTITUTE(vSvg, "%", "%25"), "#", "%23")'
    wrong = [name for name, dax in images if order not in dax]
    assert not wrong, f"SVG measures that do not encode % before #: {wrong}"


def test_every_page_opens_with_a_header_and_its_neighbours():
    names = [page["name"] for page in PAGES]
    problems = []
    for i, page in enumerate(PAGES):
        headers = sum(spec["type"] == "page_header" for spec in page["visuals"])
        if headers != 1:
            problems.append(f"{page['name']}: {headers} headers")
        targets = {spec["target"] for spec in page["visuals"] if spec["type"] == "nav"}
        expected = set(names[max(0, i - 1):i] + names[i + 1:i + 2])
        if targets != expected:
            problems.append(f"{page['name']}: buttons go to {sorted(targets)}, "
                            f"expected {sorted(expected)}")
    assert not problems, problems


def test_the_filter_panel_holds_its_members():
    problems = []
    for page in PAGES:
        panels = {spec["id"]: spec["pos"] for spec in page["visuals"]
                  if spec["type"] == "filter_panel"}
        for spec in page["visuals"]:
            if not spec.get("group"):
                continue
            px, py, pw, ph = panels[spec["group"]]
            x, y, w, h = spec["pos"]
            if x < px or y < py or x + w > px + pw or y + h > py + ph:
                problems.append(f"{page['name']}: {spec['id']} sits outside {spec['group']}")
    assert not problems, problems


def test_a_panel_bookmark_restores_visibility_not_filters():
    """
    A bookmark that also captures data puts back the slicer selections it was
    saved with, so opening the panel would reset every filter the reader had
    set. And a button naming a bookmark that does not exist does nothing at all.
    """
    folder = REPORT / "definition" / "bookmarks"
    saved = {path.name.removesuffix(".bookmark.json"): json.loads(path.read_text(encoding="utf-8"))
             for path in folder.glob("*.bookmark.json")}
    buttons = [spec for page in PAGES for spec in page["visuals"]
               if spec["type"] in ("filters_button", "panel_close")]
    assert buttons, "no filter panel buttons to check"
    problems = []
    for spec in buttons:
        bookmark = saved.get(spec["bookmark"])
        if bookmark is None:
            problems.append(f"{spec['id']} opens {spec['bookmark']}, which does not exist")
            continue
        options = bookmark["options"]
        if not options.get("suppressData") or not options.get("applyOnlyToTargetVisuals"):
            problems.append(f"{spec['bookmark']} restores more than the panel's visibility")
        if not any(name.endswith("FilterPanel") for name in options["targetVisualNames"]):
            problems.append(f"{spec['bookmark']} does not target a filter panel")
    assert not problems, problems


@pytest.mark.parametrize("path", CHROME_CASES)
def test_the_frame_carries_alt_text_too(path):
    visual = json.loads(path.read_text(encoding="utf-8"))
    general = visual["visual"].get("visualContainerObjects", {}).get("general", [])
    assert general, f"{path.parent.name} has no alt text"
    alt = general[0]["properties"]["altText"]["expr"]["Literal"]["Value"].strip("'")
    assert len(alt) > 12, f"{path.parent.name}: alt text {alt!r} says nothing"


def test_every_page_carries_a_slicer():
    """A page with no way to filter it is a picture, not a dashboard."""
    without = [
        page["name"] for page in PAGES
        if not any(spec["type"] == "slicer" for spec in page["visuals"])
    ]
    assert not without, f"pages with no slicer: {without}"


def _tables_behind_measures() -> dict[str, set[str]]:
    """
    Every table each measure reads, following measure references through.

    A slicer often reaches a visual through a measure rather than through a
    bound column -- a what-if parameter reaches the whole page that way, and it
    is the only way it can. Walking only the bound columns would call those
    slicers stranded and would miss a genuinely stranded one on a table that
    happens to share a name with a bound column.
    """
    direct, calls = {}, {}
    for name, dax, *_ in MEASURES:
        direct[name] = {t for t, _ in re.findall(r"(\w+)\[([^\]]+)\]", dax)} - {"_Measures"}
        calls[name] = {
            m for m in re.findall(r"(?<![\w\]])\[([^\]]+)\]", dax) if m in MEASURE_NAMES
        }

    resolved: dict[str, set[str]] = {}

    def walk(name: str, seen: frozenset[str]) -> set[str]:
        if name in resolved:
            return resolved[name]
        if name in seen:                       # a measure cycle would not load
            return set()
        out = set(direct.get(name, ()))
        for called in calls.get(name, ()):
            out |= walk(called, seen | {name})
        resolved[name] = out
        return out

    return {name: walk(name, frozenset()) for name in direct}


def test_every_slicer_reaches_a_visual_on_its_own_page():
    """
    A slicer on a table nothing else on the page reads filters nothing. It
    looks live, it highlights on click, and every number stays put.
    """
    behind = _tables_behind_measures()
    stranded = []
    for page in PAGES:
        entities = set()
        for spec in page["visuals"]:
            if spec["type"] == "slicer":
                continue
            fields = [spec[k] for k in ("x", "category", "series", "size", "field",
                                        "rows", "columns_by")
                      if spec.get(k)]
            fields += (list(spec.get("y", [])) + list(spec.get("values", []))
                       + [c for c in spec.get("columns", [])
                          if isinstance(spec.get("columns"), list)])
            for field in fields:
                if field.startswith("["):
                    entities |= behind.get(field.strip("[]"), set())
                elif "[" in field:
                    entities.add(field.split("[")[0])
        for spec in page["visuals"]:
            if spec["type"] != "slicer":
                continue
            entity = spec["field"].split("[")[0]
            # A slicer on a dimension reaches every fact through the model, so
            # only a slicer on an unrelated analysis table can strand itself.
            related = entity.startswith("dim_") or entity in entities
            if not related:
                stranded.append(f"{page['name']}: {spec['field']}")
    assert not stranded, f"slicers that filter nothing on their page: {stranded}"


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

def definition_files() -> list[Path]:
    return sorted(
        p for p in PBIP.rglob("*.json")
        if not any(part in ("StaticResources", ".pbi") for part in p.parts)
    )


FILES = definition_files()
_cache: dict[str, object] = {}


def schema(uri: str):
    """Fetch and cache a schema. None when the host cannot be reached at all."""
    if uri not in _cache:
        try:
            with urllib.request.urlopen(uri, timeout=TIMEOUT) as response:
                _cache[uri] = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            _cache[uri] = error.code            # 404 means an invented version
        except Exception:
            _cache[uri] = None                  # offline, DNS, timeout
    return _cache[uri]


def declared(path: Path):
    return json.loads(path.read_text(encoding="utf-8")).get("$schema")


def test_every_definition_file_names_a_schema():
    silent = [str(p.relative_to(PBIP)) for p in FILES if not declared(p)]
    assert not silent, (
        "no $schema, so Power BI will open these and nothing can check them:\n  "
        + "\n  ".join(silent)
    )


def test_the_report_speaks_one_visual_container_version():
    """Three versions in one report means the number was typed, not copied."""
    versions = defaultdict(list)
    for path in FILES:
        uri = declared(path) or ""
        if "visualContainer/" in uri:
            versions[uri.split("visualContainer/")[1].split("/")[0]].append(path.parent.name)
    assert len(versions) == 1, f"visuals declare {dict(versions)}"


def test_every_schema_the_project_names_exists():
    """A 404 here is a version Microsoft never published, and it costs the
    project every structural check below."""
    uris = sorted({declared(p) for p in FILES if declared(p)})
    codes = {uri: schema(uri) for uri in uris}
    if all(value is None for value in codes.values()):
        pytest.skip("schema host unreachable - not a claim about this repo")
    missing = sorted(uri for uri, value in codes.items() if isinstance(value, int))
    assert not missing, "the project names schema versions that do not exist:\n  " + "\n  ".join(
        f"{uri.rsplit('/definition/', 1)[-1]} -> HTTP {codes[uri]}" for uri in missing
    )


@pytest.mark.parametrize(
    "path", [pytest.param(p, id=str(p.relative_to(PBIP)).replace("\\", "/")) for p in FILES]
)
def test_every_file_validates_against_the_schema_it_names(path):
    """
    ``additionalProperties: false`` throughout, so this catches the mistyped
    property that would otherwise render as a silently empty visual.
    """
    jsonschema = pytest.importorskip(
        "jsonschema", reason="validation needs jsonschema; the checks above still ran")
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    uri = declared(path)
    root = schema(uri)
    if root is None:
        pytest.skip("schema host unreachable")
    if isinstance(root, int):
        pytest.skip("covered by test_every_schema_the_project_names_exists")

    def retrieve(reference):
        got = schema(reference)
        if got is None or isinstance(got, int):
            raise LookupError(reference)
        return Resource.from_contents(got, default_specification=DRAFT7)

    document = json.loads(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(root, registry=Registry(retrieve=retrieve))
    try:
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    except Exception as error:                  # a $ref we could not fetch
        pytest.skip(f"could not resolve a referenced schema: {error}")
    assert not errors, "\n".join(
        f"  {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors[:5]
    )


def test_every_table_in_the_model_is_reached_by_a_visual():
    """
    A table in the model that no page binds is dead weight: it refreshes, it
    takes memory, it appears in the field list, and nothing on the report
    depends on it being right.

    Following measure references is what makes this meaningful -- most tables
    are reached through a measure rather than a bound column, so a check on
    bound columns alone would flag almost everything.
    """
    behind = _tables_behind_measures()
    reached = set()
    for page in PAGES:
        for spec in page["visuals"]:
            fields = [spec[k] for k in ("x", "category", "series", "size", "field",
                                        "rows", "columns_by", "subtitle")
                      if spec.get(k)]
            fields += (list(spec.get("y", [])) + list(spec.get("values", []))
                       + list(spec.get("columns", [])))
            for field in fields:
                if field.startswith("["):
                    reached |= behind.get(field.strip("[]"), set())
                elif "[" in field:
                    reached.add(field.split("[")[0])

    orphans = sorted(set(TABLES) - reached)
    assert not orphans, (
        "tables in the model that no visual reaches -- either bind them or drop "
        f"them from model_spec: {orphans}"
    )
