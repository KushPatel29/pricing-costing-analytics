"""
The README's numbers have to be the model's numbers.

Every other claim in this repo is checked by something. The README was not:
it said 191 visuals for most of a week while the spec said 189, and "6
parameter tables -- 4 what-if, 2 field parameters" for a while after the field
parameters were taken out for not binding. Both are the same kind of mistake --
a number written once, by hand, next to a generator that moved on -- and both
were found by reading rather than by CI.

The dashboard table is the whole surface: seven rows, each a count this file
can derive. The screenshots are the other half, because a page added to the
spec without an export leaves the README showing eighteen of nineteen pages and
nothing says so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from powerbi.model_spec import (
    MEASURES,
    RELATIONSHIPS,
    TABLES,
    UNRELATED,
    WHATIF_PARAMETERS,
)
from powerbi.report_spec import PAGES

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SHOTS = ROOT / "docs" / "powerbi" / "screenshots"


def _stated() -> dict[str, int]:
    """The `| **N** | what it counts |` rows of the dashboard table."""
    rows = re.findall(r"^\| \*\*([\d,]+)\*\* \| (.+?) \|$", README, re.MULTILINE)
    assert rows, "the dashboard count table is gone, or its shape changed"
    return {label.strip(): int(number.replace(",", "")) for number, label in rows}


# Anchored at the start of the row's label, because "tables" alone matches
# three of the seven rows and "visuals" matches two.
EXPECTED = {
    r"report pages\b": len(PAGES),
    r"visuals$": sum(len(page["visuals"]) for page in PAGES),
    r"tables, plus": len(TABLES),
    r"measures across": len(MEASURES),
    r"relationships\b": len(RELATIONSHIPS),
    r"what-if parameter": len(WHATIF_PARAMETERS),
    r"tables unrelated": len(UNRELATED),
}


@pytest.mark.parametrize("pattern,expected", sorted(EXPECTED.items()))
def test_the_readme_count_matches_the_spec(pattern, expected):
    stated = _stated()
    matches = [(label, value) for label, value in stated.items()
               if re.match(pattern, label)]
    assert len(matches) == 1, (
        f"expected exactly one dashboard row starting {pattern!r}, found "
        f"{[label for label, _ in matches]}"
    )
    label, value = matches[0]
    assert value == expected, (
        f"README says {value} for {label!r}; the spec says {expected}"
    )


def test_the_display_folder_count_is_right():
    """A second number inside one of those rows, so the parser above misses it."""
    folders = len({measure[3] for measure in MEASURES})
    assert f"across {folders} display folders" in README, (
        f"the model has {folders} display folders; the README says otherwise"
    )


def test_there_is_a_screenshot_for_every_page():
    shots = sorted(path.name for path in SHOTS.glob("*.png"))
    assert len(shots) == len(PAGES), (
        f"{len(PAGES)} report pages but {len(shots)} screenshots -- re-export "
        f"after adding a page: {shots}"
    )
    numbered = [name[:2] for name in shots]
    assert numbered == [f"{i:02d}" for i in range(1, len(PAGES) + 1)], (
        f"the screenshots are not one per page in order: {shots}"
    )


def test_every_screenshot_is_linked_from_the_readme():
    missing = [path.name for path in sorted(SHOTS.glob("*.png"))
               if f"docs/powerbi/screenshots/{path.name}" not in README]
    assert not missing, (
        "screenshots committed but never shown, which is weight with no "
        f"reader: {missing}"
    )


def test_the_zero_broken_visuals_claim_is_stated_where_it_can_be_checked():
    """
    The claim that carries the most weight is the one that cannot be derived
    from the spec -- it comes from opening the report. It stays honest only
    while the export it refers to is the one in the repo, so the README has to
    point at that export rather than assert the number on its own.
    """
    assert "0" in _stated() or re.search(r"\*\*0\*\* visuals that fail to render", README), (
        "the broken-visual claim has been reworded; keep it next to the "
        "screenshots it is evidence for"
    )
    assert "docs/powerbi/screenshots/" in README
