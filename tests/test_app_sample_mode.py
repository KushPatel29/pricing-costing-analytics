"""
The app has to be usable the moment someone opens the link.

It needs a cost sheet and an ERP export before it can do anything, and a
visitor following a link has neither, so the hosted app showed two empty file
pickers and nothing else. Sample mode is now the default; these tests pin the
wiring so it cannot quietly regress to upload-only.

Streamlit is not driven here - that would need a browser - so this checks the
data path and the source of the sample, which is where the breakage would be.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from seed.generate_sheets import generate

APP_SOURCE = pathlib.Path("app/streamlit_app.py").read_text(encoding="utf-8")


class TestSampleModeWiring:
    def test_app_parses(self):
        ast.parse(APP_SOURCE)

    def test_sample_is_the_default_source(self):
        """
        The radio lists sample first, and Streamlit selects the first option,
        so a visitor gets data without touching anything.
        """
        assert "SOURCE_SAMPLE" in APP_SOURCE
        sample_at = APP_SOURCE.index("(SOURCE_SAMPLE, SOURCE_UPLOAD)")
        assert sample_at > 0, "sample must be listed before upload to be the default"

    def test_sample_comes_from_the_committed_generator(self):
        """No pickled blob or checked-in xlsx: the sample is generated."""
        assert "from seed.generate_sheets import generate" in APP_SOURCE

    def test_upload_path_is_still_available(self):
        assert "SOURCE_UPLOAD" in APP_SOURCE
        assert 'st.file_uploader("Upload Cost Sheet (XLSX)"' in APP_SOURCE

    def test_sample_mode_says_the_data_is_generated(self):
        """A tool that sets prices must not imply invented numbers are real."""
        assert "Showing generated sample data" in APP_SOURCE

    def test_default_sheet_name_matches_what_the_generator_writes(self):
        """
        The uploader's default sheet name used to be 'Final Copy', which is not
        what seed/generate_sheets.py writes - so following the README and
        uploading the sample failed until you noticed and retyped it.
        """
        from seed.generate_sheets import COST_SHEET, EXPORT_SHEET

        assert f'value="{COST_SHEET}"' in APP_SOURCE
        assert f'value="{EXPORT_SHEET}"' in APP_SOURCE


@pytest.fixture(scope="module")
def frames():
    return generate(items=90)


class TestSampleData:
    def test_sample_carries_every_column_the_app_requires(self, frames):
        """
        Parsed out of the app's own `cost_required` / `export_required` sets, so
        adding a required column there without adding it to the generator fails
        here rather than on the live site.
        """
        cost_df, export_df = frames

        def _required(name: str) -> set[str]:
            start = APP_SOURCE.index(f"{name} = {{")
            end = APP_SOURCE.index("}", start)
            return {
                token.strip().strip('",').strip('"')
                for token in APP_SOURCE[start:end].split("\n")[1:]
                for token in token.split('",')
                if token.strip().strip('",').strip('"')
            }

        for column in _required("cost_required"):
            assert column in cost_df.columns, f"cost sheet missing {column!r}"
        for column in _required("export_required"):
            assert column in export_df.columns, f"export sheet missing {column!r}"

    def test_sample_is_big_enough_to_be_worth_looking_at(self, frames):
        cost_df, _ = frames
        assert len(cost_df) >= 50

    def test_sample_is_deterministic(self):
        first, _ = generate(items=30, seed=613)
        second, _ = generate(items=30, seed=613)
        assert first.equals(second)


class TestEveryNameTheDownloadNeedsExistsOnBothPaths:
    """The download at the bottom runs for sample data too.

    `cost_sheet_name` and `export_sheet_name` were collected by text inputs that
    only render on the upload path, then used unconditionally when writing the
    workbook. So the sample flow — the one a visitor lands on, and the only one
    they can use without files of their own — raised
    `NameError: name 'cost_sheet_name' is not defined` the moment they clicked
    download. Static parsing catches it: the names are read at module level, so
    every read must have an assignment that is not nested inside the upload
    branch.
    """

    @staticmethod
    def _unconditional_assignments(source: str) -> set[str]:
        """Names assigned at module level, outside any if/else or with block."""
        tree = ast.parse(source)
        assigned = set()
        for node in tree.body:  # module level only — not inside a branch
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            assigned.add(name.id)
        return assigned

    @pytest.mark.parametrize("name", ["cost_sheet_name", "export_sheet_name", "other_sheets"])
    def test_the_download_names_are_assigned_outside_the_upload_branch(self, name):
        assert name in APP_SOURCE, f"{name} is no longer used by the app"
        assigned = self._unconditional_assignments(APP_SOURCE)
        assert name in assigned, (
            f"{name} is only assigned inside a branch, so the sample-data path "
            f"reaches the download with it undefined — that is a NameError in "
            f"front of every visitor who does not upload their own files."
        )
