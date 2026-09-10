"""
Run every view the way Streamlit runs it, and fail on the ones that raise.

There are seventeen pages here, each reading a different set of generated
tables. A column renamed in the engine, a table dropped from ``output/``, an
import moved -- none of that is caught by the maths tests, and none of it is
visible in a diff. What it produces is a red exception box on one page of a
dashboard that opened fine, found by whoever clicks that page next.

``AppTest`` runs a page headlessly through Streamlit's own script runner, so
this is the real execution path: the same imports, the same caching, the same
widgets. It is not a rendering check -- nothing here says a chart looks right --
but a page that raises cannot look right either, and that is the failure worth
a gate.

The generated data has to exist first. It is committed, so normally it does;
when it is not, these skip with a message that says which command to run rather
than reporting seventeen identical failures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEWS = ROOT / "app" / "views"

# Two minutes. The first view to run pays for reading every CSV into the shared
# cache; the default of a few seconds fails on that one and passes on the rest,
# which reads as a flaky test rather than a slow fixture.
TIMEOUT = 120

VIEW_FILES = sorted(p for p in VIEWS.glob("*.py") if not p.name.startswith("_"))


def _data_is_built() -> bool:
    return (ROOT / "output" / "executive_summary.csv").exists()


def test_there_are_views_to_check():
    """Every case below is vacuous if the glob finds nothing."""
    assert len(VIEW_FILES) >= 15, f"only {len(VIEW_FILES)} views found under {VIEWS}"


@pytest.mark.parametrize("view", VIEW_FILES, ids=[p.stem for p in VIEW_FILES])
def test_the_view_runs_without_raising(view):
    if not _data_is_built():
        pytest.skip("run: python -m seed.generate_market && "
                    "python -m engine.build_pricing_analytics")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(view), default_timeout=TIMEOUT)
    app.run()
    assert not app.exception, (
        f"{view.name} raised: "
        + " | ".join(f"{e.type}: {e.message}" for e in app.exception)
    )


@pytest.mark.parametrize("view", VIEW_FILES, ids=[p.stem for p in VIEW_FILES])
def test_the_view_puts_something_on_the_page(view):
    """
    A page that runs clean and renders nothing is the other failure mode: a
    filter defaulting to an empty selection, or a frame that came back empty,
    leaves a title and white space and raises nothing at all.
    """
    if not _data_is_built():
        pytest.skip("generated data is not built")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(view), default_timeout=TIMEOUT)
    app.run()
    drawn = (len(app.markdown) + len(app.dataframe) + len(app.table)
             + len(app.metric) + len(app.title) + len(app.header))
    assert drawn > 3, f"{view.name} rendered almost nothing ({drawn} elements)"


# --------------------------------------------------------------------------
# The loader underneath them
# --------------------------------------------------------------------------

class TestTheLoaderReadsDates:
    """
    Both directions of the same rule, because each has already bitten once.

    A date left as text fails several lines later on `.dt`, on a page that
    never touched the column. A money column coerced *to* a date is worse: it
    becomes NaT, nothing raises, and the page reports blanks where the numbers
    were.
    """

    @staticmethod
    def _load(name):
        if not _data_is_built():
            pytest.skip("generated data is not built")
        from app import shared as sh
        return sh.load(name)

    def test_a_date_column_that_is_not_called_month_is_still_a_date(self):
        import pandas as pd
        frame = self._load("price_change_log")
        assert pd.api.types.is_datetime64_any_dtype(frame["effective_month"])

    def test_a_money_column_that_ends_in_month_stays_money(self):
        """
        `pocket_revenue_prior_month` ends in `_month` and holds dollars. A
        name-only rule turns all thirty-five of its values into NaT.
        """
        import pandas as pd
        frame = self._load("sql_monthly_trend")
        column = frame["pocket_revenue_prior_month"]
        assert pd.api.types.is_numeric_dtype(column)
        assert column.notna().sum() > 20

    def test_the_erp_extract_is_reachable(self):
        """The data-quality page shows the rows behind a failed rule, which
        means reading the extract the rules ran against."""
        frame = self._load("erp_billing_items")
        assert len(frame) > 1000


class TestGeneratedProseSurvivesTheRenderer:
    """
    Two ways a generated paragraph goes wrong on screen without raising.
    """

    def test_two_amounts_in_one_sentence_keep_their_dollar_signs(self):
        """
        Streamlit's markdown reads `$...$` as inline maths, so a sentence with
        two amounts loses **both** dollar signs and sets the words between them
        in a maths font. The executive note on the recommendations page carries
        three amounts and was rendering exactly that way.
        """
        from app.shared import escape_money

        sentence = "worth $2.6M of margin, of which $2.4M is the increase"
        escaped = escape_money(sentence)
        assert escaped.count(r"\$") == 2
        assert "$" not in escaped.replace(r"\$", "")

    def test_the_executive_note_counts_in_the_singular(self):
        """
        "1 small items lose money" tells the reader the paragraph was
        generated, and they discount everything else in it.
        """
        from pricing.recommend import executive_note

        one = executive_note([
            {"product_id": "A", "action": "Discontinue", "margin_delta": 10.0,
             "revenue_delta": -5.0, "volume_units": 3.0},
        ])
        assert "1 small item loses money" in one
        assert "it holds a listing" in one

        two = executive_note([
            {"product_id": "A", "action": "Discontinue", "margin_delta": 10.0,
             "revenue_delta": -5.0, "volume_units": 3.0},
            {"product_id": "B", "action": "Discontinue", "margin_delta": 8.0,
             "revenue_delta": -4.0, "volume_units": 2.0},
        ])
        assert "2 small items lose money" in two
        assert "they hold a listing" in two


class TestAmountsRead:
    def test_a_negative_amount_puts_the_sign_before_the_currency(self):
        """`$-32k` is what you get by formatting the number inside the string,
        and it reads as a typo -- on a page of variances, most of the page."""
        from app.shared import dollars, money

        assert money(-2_290_000, 2) == "-$2.29M"
        assert money(-32_000) == "-$32k"
        assert dollars(-450.5) == "-$450.50"
        assert money(2_650_000, 2) == "$2.65M"
        assert money(0) == "$0"


class TestWhichRendererEatsDollars:
    r"""
    Measured against a running Streamlit, not assumed. `st.markdown`,
    `st.caption` and the callouts parse markdown and lose `$...$` pairs; text
    inside an `unsafe_allow_html` tag does not go through the parser and keeps
    them. Escaping the second kind puts a visible backslash on the page, which
    is how the leakage caption on the landing page came to read "\$2.2M".
    """

    def test_the_html_helpers_do_not_escape(self):
        import inspect

        from app import shared as sh

        for helper in (sh.lede, sh.caption):
            source = inspect.getsource(helper)
            assert "unsafe_allow_html=True" in source, helper.__name__
            assert "escape_money" not in source, (
                f"{helper.__name__} writes into a tag, where an escape renders "
                "as a literal backslash"
            )

    def test_the_callout_helper_does_escape(self):
        import inspect

        from app import shared as sh

        assert "escape_money" in inspect.getsource(sh.note)
