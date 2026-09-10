"""
Draw every report page as an SVG wireframe, from the spec that builds it.

Nobody has opened this project in Power BI Desktop, so there are no screenshots
of it and this file does not pretend otherwise. What it can show honestly is the
*layout*: every visual's type, position, title and the fields it binds, taken
from ``powerbi.report_spec`` -- the same module ``build_pbip`` generates the
report from. A wireframe that disagrees with the report is impossible, because
they are the same data.

That is worth more than it sounds. A reader can see that the canvas is used,
that nothing overlaps, that each page carries a slicer, and what each page is
actually *for*, without installing Desktop or trusting a picture.

Usage::

    python -m powerbi.render_layouts
    python -m powerbi.render_layouts --check
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from powerbi.report_spec import PAGES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "powerbi"

CANVAS_W, CANVAS_H = 1280, 720

# The report's own palette, so a wireframe and the thing it describes are the
# same colours. Visual types are grouped by what they do rather than given one
# hue each: eight hues for eight chart types would be a legend nobody reads.
PLANE = "#0c0c0f"
SURFACE = "#141416"
HAIRLINE = "#2c2c31"
INK = "#f2f2f4"
INK_2 = "#a6a6ad"
INK_3 = "#7d7d85"

ROLE_COLOUR: dict[str, str] = {
    "card": "#3987e5",        # the headline numbers
    "line": "#199e70",        # something over time
    "area": "#199e70",
    "bar": "#c98500",         # something compared across members
    "column": "#c98500",
    "stacked_column": "#c98500",
    "waterfall": "#d95926",   # something decomposed
    "treemap": "#d95926",
    "donut": "#d95926",
    "scatter": "#9085e9",     # two measures against each other
    "table": "#7d7d85",       # the detail underneath
    "matrix": "#7d7d85",
    "slicer": "#d55181",      # the controls
    "gauge": "#3987e5",
}


def fields_of(spec: dict) -> list[str]:
    """Every field a visual binds, in the order a reader would name them."""
    out: list[str] = []
    for key in ("field", "x", "rows", "columns_by", "category", "series", "size"):
        if spec.get(key):
            out.append(spec[key])
    out += list(spec.get("y", ()))
    out += list(spec.get("values", ()))
    out += list(spec.get("columns", ()))
    return out


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def visual_svg(spec: dict) -> str:
    x, y, width, height = spec["pos"]
    colour = ROLE_COLOUR.get(spec["type"], INK_3)
    fields = fields_of(spec)
    # Cards and slicers carry no title in the spec -- Power BI labels them from
    # the field. A wireframe of eight boxes all captioned "Card" says nothing.
    title = spec.get("title") or (fields[0].strip("[]") if fields
                                  else spec["type"].replace("_", " ").title())

    # How many field lines fit, at 15px each under a title and a type label.
    room = max(0, (height - 52) // 15)
    shown = fields[:room]

    parts = [
        f'<g><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" '
        f'fill="{SURFACE}" stroke="{HAIRLINE}" stroke-width="1"/>',
        # A colour bar rather than a filled block: the wireframe should read as
        # a plan, not as a chart that happens to have no data in it.
        f'<rect x="{x}" y="{y}" width="4" height="{height}" rx="2" fill="{colour}"/>',
        f'<text x="{x + 14}" y="{y + 21}" fill="{INK}" font-size="12.5" '
        f'font-weight="600">{html.escape(truncate(title, max(8, width // 8)))}</text>',
        f'<text x="{x + 14}" y="{y + 37}" fill="{colour}" font-size="10.5" '
        f'letter-spacing="0.06em">{spec["type"].replace("_", " ").upper()}</text>',
    ]
    for index, field in enumerate(shown):
        parts.append(
            f'<text x="{x + 14}" y="{y + 55 + index * 15}" fill="{INK_2}" '
            f'font-size="10.5" font-family="ui-monospace, SFMono-Regular, Menlo, '
            f'monospace">{html.escape(truncate(field, max(8, width // 6)))}</text>'
        )
    if len(fields) > len(shown):
        parts.append(
            f'<text x="{x + 14}" y="{y + 55 + len(shown) * 15}" fill="{INK_3}" '
            f'font-size="10.5">+{len(fields) - len(shown)} more</text>'
        )
    parts.append("</g>")
    return "\n  ".join(parts)


def page_svg(page: dict, number: int, total: int) -> str:
    header = 44
    body = "\n  ".join(visual_svg(spec) for spec in page["visuals"])
    kinds = sorted({spec["type"] for spec in page["visuals"]})
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} '
        f'{CANVAS_H + header}" width="{CANVAS_W}" height="{CANVAS_H + header}" '
        f'role="img" aria-label="Layout of the {html.escape(page["display"])} page: '
        f'{len(page["visuals"])} visuals on a {CANVAS_W} by {CANVAS_H} canvas">\n'
        f'  <rect width="{CANVAS_W}" height="{CANVAS_H + header}" fill="{PLANE}"/>\n'
        f'  <text x="20" y="27" fill="{INK}" font-size="15" font-weight="700">'
        f'{html.escape(page["display"])}</text>\n'
        f'  <text x="{CANVAS_W - 20}" y="27" fill="{INK_3}" font-size="11.5" '
        f'text-anchor="end">page {number} of {total} · {len(page["visuals"])} '
        f'visuals · {html.escape(", ".join(kinds))}</text>\n'
        f'  <g transform="translate(0 {header})">\n  {body}\n  </g>\n'
        f'  <text x="20" y="{CANVAS_H + header - 8}" fill="{INK_3}" font-size="10">'
        f'Layout generated from powerbi/report_spec.py — not a screenshot of '
        f'Power BI Desktop.</text>\n'
        "</svg>\n"
    )


def build() -> dict[str, str]:
    total = len(PAGES)
    return {
        f"{index + 1:02d}-{page['name'].removeprefix('section_')}.svg":
            page_svg(page, index + 1, total)
        for index, page in enumerate(PAGES)
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed wireframes have drifted from the spec")
    args = ap.parse_args(argv)

    pages = build()
    if args.check:
        stale = []
        for name, content in pages.items():
            path = OUT / name
            if not path.exists():
                stale.append(f"missing: {name}")
            elif path.read_text(encoding="utf-8") != content:
                stale.append(f"differs: {name}")
        for path in sorted(OUT.glob("*.svg")):
            if path.name not in pages:
                stale.append(f"stale: {path.name}")
        if stale:
            print("report wireframes are out of date:", file=sys.stderr)
            for line in stale[:20]:
                print(f"  {line}", file=sys.stderr)
            print("\nrun: python -m powerbi.render_layouts", file=sys.stderr)
            return 1
        print(f"report wireframes match the spec ({len(pages)} pages)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for path in OUT.glob("*.svg"):
        if path.name not in pages:
            path.unlink()
    for name, content in pages.items():
        with open(OUT / name, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    print(f"wrote {len(pages)} page layouts to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
