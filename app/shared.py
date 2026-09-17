"""
Everything the pages share: the data loaders, the palette, and the chart helpers.

Two things here are load-bearing rather than cosmetic.

**The loaders never recompute an analysis.** Every page reads a CSV that
``engine/build_pricing_analytics.py`` already wrote. A page that recomputed
"pocket margin" with its own groupby would drift from the dashboard reading the
same name off the same fact, and the drift would be invisible until someone put
the two on one slide.

**The palette is fixed and validated, not picked per chart.** Categorical hues
are assigned in slot order and never cycled; sequential encodings use one hue;
diverging uses blue/red across a neutral. The eight categorical slots clear
colour-vision separation on adjacent pairs, which is what a stacked bar and a
multi-series line actually need.

**Dark is a re-step, not a flip.** These are the same eight hues as the light
palette, re-stepped for the dark surface and re-validated against it: worst
adjacent CVD delta-E 8.4, worst adjacent normal-vision delta-E 19.3, and all
eight clear 3:1 against ``#141416``. Inverting a light palette instead would
leave every hue too dark to see and nothing would report it -- the chart still
draws. Sequential runs dark-to-light here rather than light-to-dark, because on
a dark surface the step nearest the surface is the one that has to mean "near
zero".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"
# The ERP extract, before staging. The data-quality page reads it to show the
# rows behind a failed rule, which is the point of a quality report.
RAW_DIR = ROOT / "raw"

# --- Palette -------------------------------------------------------------
# Categorical slots, in fixed order. Never cycled: a ninth series folds into
# "Other" or becomes small multiples instead of inventing a hue.
#
# The order is the colour-vision-safety mechanism, not decoration. Adjacent
# pairs were checked under protanopia, deuteranopia and tritanopia against the
# surface below; re-ordering them breaks that without changing a hex.
SERIES = ("#3987e5", "#d95926", "#199e70", "#c98500",
          "#d55181", "#008300", "#9085e9", "#e66767")
# Scatter and bubble compare every pair at once, not just neighbours, and the
# full eight cannot clear the separation floor that way. The first three can.
SERIES_ALL_PAIRS = SERIES[:3]

# Dark-to-light. On a dark surface the step nearest the surface is the one that
# has to read as "near zero", so the ramp runs the opposite way to a light one.
SEQUENTIAL = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]
# Both arms brighten outward from a neutral, for the same reason.
DIVERGING = ["#cde2fb", "#9ec5f4", "#6da7ec", "#383835", "#f0a3a3", "#e66767", "#d55151"]

# Status colours are reserved and never themed: they mean state, not identity,
# and they never stand in for "series 4". All four clear 3:1 on this surface.
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"

INK = "#f2f2f4"
INK_SECONDARY = "#a6a6ad"
INK_MUTED = "#7d7d85"
GRID = "#26262a"
AXIS = "#3a3a40"
SURFACE = "#141416"           # the chart surface the palette was validated on
PLANE = "#0c0c0f"             # the page behind the cards
RAISED = "#1b1b20"            # inputs, hover, the sidebar
HAIRLINE = "rgba(255,255,255,0.09)"
ACCENT = SERIES[0]

# Verdict colours are status, not series: they never stand in for "series 4",
# and every chart using them also carries the verdict as a text label.
VERDICT_COLOURS = {
    "Above stretch": GOOD, "At target": GOOD, "Below target": WARNING,
    "Below floor": SERIOUS, "Loss-making": CRITICAL,
    "Favourable": GOOD, "Unfavourable": CRITICAL, "On standard": INK_MUTED,
    "Creates value": GOOD, "Destroys value": CRITICAL,
    "Worth it": GOOD, "Destroys margin": CRITICAL,
}

POSITION_COLOURS = {
    "Deep discount": SEQUENTIAL[1], "Below market": SEQUENTIAL[2],
    "At market": SEQUENTIAL[3], "Premium": SEQUENTIAL[4],
    "Super-premium": SEQUENTIAL[5], "No coverage": AXIS,
}


# --- Page setup ----------------------------------------------------------

def configure() -> None:
    """
    Page config, called once by the entry script.

    Streamlit allows exactly one ``set_page_config`` per script run, and under
    ``st.navigation`` the router and the selected view are one run -- so a view
    that also called it raised on every navigation. It lives here, called once,
    rather than in :func:`page`.
    """
    st.set_page_config(page_title="Pricing and costing analytics",
                       page_icon=":material/query_stats:", layout="wide")


# One font request, one weight axis. Inter has real tabular figures, which is
# the difference between a column of numbers that scans and one that wobbles;
# the fallback stack is a system font on every platform, so a blocked CDN costs
# the typeface and nothing else.
_FONT_URL = ("https://fonts.googleapis.com/css2?"
             "family=Inter:wght@400;500;600;700&display=swap")
_FONT_STACK = ('Inter, system-ui, -apple-system, "Segoe UI", Roboto, '
               "Helvetica, Arial, sans-serif")


def page(title: str = "") -> None:
    """
    One call at the top of every view: the shared stylesheet.

    Streamlit re-runs the whole script on every interaction, so this is written
    to be idempotent and cheap -- a style block, no state. It is separate from
    :func:`configure` because ``set_page_config`` may be called exactly once per
    run and ``st.navigation`` makes the router and the view one run.
    """
    st.markdown(
        f"""
        <style>
          @import url("{_FONT_URL}");

          :root {{
            --plane: {PLANE}; --surface: {SURFACE}; --raised: {RAISED};
            --hairline: {HAIRLINE}; --accent: {ACCENT};
            --ink: {INK}; --ink-2: {INK_SECONDARY}; --ink-3: {INK_MUTED};
          }}

          html, body, .stApp, [class*="css"] {{ font-family: {_FONT_STACK}; }}

          /* The plane is not flat: one very wide, very low-opacity wash behind
             the top-left, so the page has a light source and the cards read as
             sitting on something. Any stronger and it competes with the data. */
          .stApp {{
            background:
              radial-gradient(1200px 620px at 12% -8%,
                              rgba(57,135,229,0.13), transparent 62%),
              radial-gradient(900px 520px at 96% 2%,
                              rgba(144,133,233,0.08), transparent 60%),
              var(--plane);
            background-attachment: fixed;
          }}

          .block-container {{ padding-top: 2.0rem; padding-bottom: 3rem;
                              max-width: 1520px; }}

          h1 {{ font-size: 1.9rem !important; font-weight: 700; color: var(--ink);
                letter-spacing: -0.02em; margin-bottom: 0.35rem; }}
          h2 {{ font-size: 1.16rem !important; font-weight: 600; color: var(--ink);
                letter-spacing: -0.01em; margin-top: 2.1rem; padding-left: 0.7rem;
                border-left: 3px solid var(--accent); line-height: 1.35; }}
          h3 {{ font-size: 0.98rem !important; font-weight: 600;
                color: var(--ink-2); letter-spacing: 0.01em; }}

          .lede {{ color: var(--ink-2); font-size: 1.03rem; line-height: 1.6;
                   max-width: 74ch; margin-bottom: 0.5rem; }}
          .caption {{ color: var(--ink-3); font-size: 0.85rem; line-height: 1.55; }}

          /* Numbers everywhere line up in columns. */
          .stDataFrame, .kpi-value, .kpi-delta, [data-testid="stMetricValue"] {{
            font-variant-numeric: tabular-nums;
            font-feature-settings: "tnum" 1, "cv05" 1;
          }}

          /* --- KPI tiles ------------------------------------------------- */
          .kpi {{
            background: var(--surface);
            border: 1px solid var(--hairline);
            border-radius: 12px; padding: 0.85rem 1rem 0.9rem;
            height: 100%; position: relative; overflow: hidden;
          }}
          .kpi::before {{
            content: ""; position: absolute; inset: 0 auto 0 0; width: 3px;
            background: var(--accent); opacity: 0.85;
          }}
          .kpi.good::before {{ background: {GOOD}; }}
          .kpi.warn::before {{ background: {WARNING}; }}
          .kpi.bad::before  {{ background: {CRITICAL}; }}
          .kpi-label {{ color: var(--ink-3); font-size: 0.76rem; font-weight: 500;
                        text-transform: uppercase; letter-spacing: 0.07em; }}
          .kpi-value {{ color: var(--ink); font-size: 1.72rem; font-weight: 650;
                        line-height: 1.22; margin-top: 0.28rem;
                        letter-spacing: -0.02em; }}
          .kpi-delta {{ font-size: 0.83rem; margin-top: 0.2rem; color: var(--ink-3); }}
          .kpi-delta.good {{ color: {GOOD}; }}
          .kpi-delta.warn {{ color: {WARNING}; }}
          .kpi-delta.bad  {{ color: {CRITICAL}; }}

          /* --- Charts and tables sit on the same card as the tiles ------- */
          div[data-testid="stPlotlyChart"] {{
            background: var(--surface); border: 1px solid var(--hairline);
            border-radius: 12px; padding: 0.5rem 0.4rem 0.2rem;
          }}
          div[data-testid="stDataFrame"] {{
            border: 1px solid var(--hairline); border-radius: 12px;
            overflow: hidden;
          }}

          /* --- Sidebar --------------------------------------------------- */
          section[data-testid="stSidebar"] {{
            background: {SURFACE}; border-right: 1px solid var(--hairline);
          }}
          section[data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}

          /* --- Inputs ---------------------------------------------------- */
          .stSelectbox div[data-baseweb="select"] > div,
          .stMultiSelect div[data-baseweb="select"] > div,
          .stTextInput input, .stNumberInput input {{
            background: var(--raised) !important;
            border-color: var(--hairline) !important;
            border-radius: 9px !important;
          }}
          .stSlider [data-baseweb="slider"] div[role="slider"] {{
            border: 2px solid var(--accent);
          }}
          .stButton > button, .stDownloadButton > button {{
            border-radius: 9px; border: 1px solid var(--hairline);
            background: var(--raised); color: var(--ink); font-weight: 550;
            transition: border-color 120ms ease, background 120ms ease;
          }}
          .stButton > button:hover, .stDownloadButton > button:hover {{
            border-color: var(--accent); background: #23232a; color: var(--ink);
          }}

          /* --- Tabs ------------------------------------------------------ */
          .stTabs [data-baseweb="tab-list"] {{ gap: 0.25rem;
                                               border-bottom: 1px solid var(--hairline); }}
          .stTabs [data-baseweb="tab"] {{ color: var(--ink-3); font-weight: 550;
                                          padding: 0.45rem 0.9rem; }}
          .stTabs [aria-selected="true"] {{ color: var(--ink); }}

          /* --- Sidebar navigation ---------------------------------------- */
          /* `aria-current` rather than the emotion class beside it: the class
             is a content hash that changes with the Streamlit version, and a
             stylesheet pinned to one is a nav that loses its selected state on
             an upgrade, silently. */
          a[data-testid="stSidebarNavLink"] {{
            border-radius: 9px; transition: background 120ms ease;
          }}
          a[data-testid="stSidebarNavLink"]:hover {{
            background: rgba(255,255,255,0.05) !important;
          }}
          a[data-testid="stSidebarNavLink"][aria-current="page"] {{
            background: rgba(57,135,229,0.16) !important;
            box-shadow: inset 3px 0 0 var(--accent);
          }}
          a[data-testid="stSidebarNavLink"][aria-current="page"] span {{
            color: var(--ink) !important; font-weight: 600;
          }}

          /* --- Callouts: a tinted left rule rather than a filled block ---- */
          /* The tone lives on the content element's test id, which is the only
             stable thing about a Streamlit alert. Left as Streamlit ships it,
             a warning is a solid olive block that fights every chart near it. */
          div[data-testid="stAlert"] {{
            background: transparent !important; border: 0; color: var(--ink-2);
          }}
          div[data-testid="stAlertContainer"] {{
            background: var(--surface) !important;
            border: 1px solid var(--hairline);
            border-left: 3px solid var(--accent);
            border-radius: 10px; color: var(--ink-2);
          }}
          div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"])
            div[data-testid="stAlertContainer"] {{ border-left-color: {WARNING}; }}
          div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"])
            div[data-testid="stAlertContainer"] {{ border-left-color: {GOOD}; }}
          div[data-testid="stAlert"]:has([data-testid="stAlertContentError"])
            div[data-testid="stAlertContainer"] {{ border-left-color: {CRITICAL}; }}
          div[data-testid="stExpander"] details {{
            background: var(--surface); border: 1px solid var(--hairline);
            border-radius: 12px;
          }}

          /* Streamlit's own running indicator and footer. */
          div[data-testid="stDecoration"] {{
            background: linear-gradient(90deg, {SERIES[0]}, {SERIES[6]}, {SERIES[2]});
          }}
          /* Streamlit's own chrome. The deploy button only renders for the
             account that owns the app, so it is invisible to a visitor and
             present in every screenshot taken locally -- which is the one
             place it does harm. */
          #MainMenu, footer {{ visibility: hidden; }}
          [data-testid="stAppDeployButton"] {{ display: none; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def escape_money(text: str) -> str:
    r"""
    Escape dollar signs so Streamlit does not read them as LaTeX.

    Streamlit's markdown treats ``$...$`` as inline maths. A sentence carrying
    two amounts -- "worth $2.6M of margin, of which $2.4M is the increase" --
    therefore loses **both** dollar signs and renders the text between them in
    a maths font. It is not an error and nothing logs it; the paragraph simply
    reads as though somebody forgot the currency.

    Escaping happens here, at the render boundary, rather than in the analysis:
    a generated paragraph should be plain prose, and the escaping is a property
    of the renderer it is going into.

    Which renderer matters. Streamlit parses markdown in `st.markdown`,
    `st.caption` and the callouts, so those need this. Text handed to
    `unsafe_allow_html` inside a tag does **not** go through the markdown
    parser -- it keeps its dollar signs, and escaping it puts a visible
    backslash on the page. `lede` and `caption` below take that path, so they
    do not call this.
    """
    return text.replace("$", r"\$")


def lede(text: str) -> None:
    st.markdown(f'<div class="lede">{text}</div>', unsafe_allow_html=True)


def caption(text: str) -> None:
    st.markdown(f'<div class="caption">{text}</div>', unsafe_allow_html=True)


def note(text: str, *, kind: str = "info") -> None:
    """
    A callout carrying generated prose.

    Use this rather than `st.info` and friends directly whenever the text can
    contain an amount: those parse markdown, so `$2.6M ... $2.4M` loses both
    dollar signs and sets the words between them in a maths font.
    """
    {"info": st.info, "success": st.success,
     "warning": st.warning, "error": st.error}[kind](escape_money(text))


# --- Data ----------------------------------------------------------------

DATE_COLUMNS = ("month", "date", "week_start", "last_price_change")


def _is_date_column(name: str) -> bool:
    """
    Whether a column holds a date.

    Suffix rather than an exact list: `effective_month` and `billing_date` are
    dates by every reading except the one that matters, and a date left as text
    does not fail where it is read -- it fails several lines later on `.dt`,
    which is a page that raises on a column nobody touched.

    The name is necessary and not sufficient. `pocket_revenue_prior_month` ends
    in `_month` and holds dollars; coercing it would replace a column of money
    with a column of NaT and report nothing at all. The dtype check in `_read`
    is what stops that -- a numeric column is never a date, whatever it is
    called.
    """
    return name in DATE_COLUMNS or name.endswith(("_month", "_date"))


@st.cache_data(show_spinner=False)
def _read(path_str: str, mtime: float) -> pd.DataFrame:
    """Read one CSV. ``mtime`` is in the key so a rebuild invalidates the cache."""
    frame = pd.read_csv(path_str)
    for column in frame.columns:
        if not _is_date_column(column):
            continue
        # Not `is_object_dtype`: pandas reads text as the `str` dtype here, for
        # which that check is False, and the guard silently stopped converting
        # anything at all. Numeric is the property actually being excluded.
        if pd.api.types.is_numeric_dtype(frame[column]):
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce")
        if parsed.notna().any():
            frame[column] = parsed
    return frame


@st.cache_resource(show_spinner="Building the analysis (first run only)...")
def _ensure_built() -> bool:
    """
    Generate the data and run the engine if ``output/`` is not there.

    The repo commits both, so this normally does nothing. It exists because a
    fresh clone that ran the app before running the generator used to show ten
    pages of ``FileNotFoundError``, and "read the README" is not an error
    message.
    """
    if (OUT_DIR / "executive_summary.csv").exists():
        return True
    from engine.build_pricing_analytics import build
    from engine.build_pricing_analytics import write as write_outputs
    from seed.generate_market import generate
    from seed.generate_market import write as write_data

    if not (DATA_DIR / "fact_sales.csv").exists():
        write_data(generate(), DATA_DIR)
    write_outputs(build(DATA_DIR), OUT_DIR)
    return True


def load(name: str) -> pd.DataFrame:
    """An analysis table from ``output/``, a fact from ``data/``, or the ERP
    extract from ``raw/``."""
    _ensure_built()
    for directory in (OUT_DIR, DATA_DIR, RAW_DIR):
        path = directory / f"{name}.csv"
        if path.exists():
            return _read(str(path), path.stat().st_mtime)
    raise FileNotFoundError(
        f"{name}.csv is in none of output/, data/ or raw/. Run: "
        "python -m seed.generate_market && python -m seed.generate_erp && "
        "python -m engine.build_pricing_analytics && python -m engine.stage_erp"
    )


@st.cache_data(show_spinner=False)
def sales_with_dimensions(mtime: float) -> pd.DataFrame:
    """The sales fact joined to product and customer, which most pages want."""
    sales = load("fact_sales")
    products = load("dim_product")
    customers = load("dim_customer")
    joined = sales.merge(
        products[["product_id", "description", "category", "sub_category",
                  "brand_tier", "lifecycle", "target_margin"]],
        on="product_id", how="left",
    ).merge(
        customers[["customer_id", "customer_name", "segment", "channel",
                   "region", "tier", "price_list", "payment_terms"]],
        on="customer_id", how="left",
    )
    joined["fiscal_year"] = joined["month"].dt.year.where(
        joined["month"].dt.month < 7, joined["month"].dt.year + 1
    )
    return joined


def sales() -> pd.DataFrame:
    path = DATA_DIR / "fact_sales.csv"
    _ensure_built()
    return sales_with_dimensions(path.stat().st_mtime)


# --- Formatting ----------------------------------------------------------

def money(value: float, decimals: int = 0) -> str:
    """
    An amount, abbreviated, with the sign in front of the currency.

    `-$32k`, not `$-32k`. The second is what you get by formatting the number
    inside the string and it reads as a typo every time -- which on a page full
    of negative variances is most of the page.
    """
    if pd.isna(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= 1_000_000:
        return f"{sign}${size / 1_000_000:,.{max(decimals, 1)}f}M"
    if size >= 10_000:
        return f"{sign}${size / 1_000:,.0f}k"
    return f"{sign}${size:,.{decimals}f}"


def dollars(value: float, decimals: int = 2) -> str:
    """An exact amount, sign before the currency for the same reason."""
    if pd.isna(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def pct(value: float, decimals: int = 1) -> str:
    return "n/a" if pd.isna(value) else f"{value:.{decimals}%}"


def num(value: float, decimals: int = 0) -> str:
    return "n/a" if pd.isna(value) else f"{value:,.{decimals}f}"


def format_kpi(value: float, unit: str) -> str:
    return {"currency": lambda v: money(v, 1), "percent": pct,
            "index": lambda v: f"{v:,.1f}", "ratio": lambda v: f"{v:,.2f}",
            "count": lambda v: num(v)}.get(unit, num)(value)


# --- Charts --------------------------------------------------------------

def _base_layout(fig, height: int, *, showlegend: bool, y_title: str = "",
                 x_title: str = "") -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        # Transparent rather than SURFACE, so the chart takes the colour of the
        # card CSS puts behind it. Painting it here as well leaves a hairline of
        # the wrong shade at the rounded corners.
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT_STACK, size=13, color=INK_SECONDARY),
        showlegend=showlegend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(size=12, color=INK_SECONDARY), title_text=""),
        hoverlabel=dict(bgcolor=RAISED, bordercolor=AXIS,
                        font=dict(color=INK, size=12, family=_FONT_STACK)),
        hovermode="closest",
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=AXIS,
                     tickfont=dict(color=INK_MUTED, size=12), title_text=x_title,
                     title_font=dict(color=INK_MUTED, size=12))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", tickfont=dict(color=INK_MUTED, size=12),
                     title_text=y_title, title_font=dict(color=INK_MUTED, size=12))


def show(fig, height: int = 380, *, showlegend: bool = False,
         y_title: str = "", x_title: str = "", key: str | None = None) -> None:
    """Apply the shared chrome and render. Every chart goes through here."""
    _base_layout(fig, height, showlegend=showlegend, y_title=y_title, x_title=x_title)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key)


def _si(value: float) -> str:
    """
    Abbreviate a large number for a bar label: 1.2M, 840k, 92.

    Written out because Python's format mini-language has no SI type -- ``.3s``
    is D3's, which Plotly understands inside its own tickformat and Python does
    not understand at all. Passing it to an f-string raises "Cannot specify ','
    with 's'", which is a runtime error on a chart that looked fine in review.
    """
    if pd.isna(value):
        return ""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{sign}{magnitude / 1_000_000:,.1f}M"
    if magnitude >= 1_000:
        return f"{sign}{magnitude / 1_000:,.0f}k"
    return f"{sign}{magnitude:,.2f}"


def waterfall(steps: pd.DataFrame, *, label: str = "step", amount: str = "amount",
              kind: str = "kind", height: int = 420, prefix: str = "$",
              value_fmt: str = ",.0f"):
    """
    A waterfall with totals as absolute bars and steps as relative ones.

    Plotly's own waterfall trace, because rolling one by hand out of stacked
    bars with invisible bases is the classic way to get a chart that looks
    right and puts one bar in the wrong place when a value goes negative.
    """
    import plotly.graph_objects as go

    measures = ["absolute" if k == "total" else "relative" for k in steps[kind]]
    fig = go.Figure(
        go.Waterfall(
            measure=measures,
            x=steps[label].tolist(),
            y=steps[amount].tolist(),
            connector=dict(line=dict(color=AXIS, width=1)),
            decreasing=dict(marker=dict(color=SERIES[1])),
            increasing=dict(marker=dict(color=SERIES[2])),
            totals=dict(marker=dict(color=SERIES[0])),
            text=[
                f"{prefix}{_si(v)}" if value_fmt == "si" else f"{prefix}{v:{value_fmt}}"
                for v in steps[amount]
            ],
            textposition="outside",
            textfont=dict(color=INK_SECONDARY, size=11),
            hovertemplate="%{x}<br>%{text}<extra></extra>",
        )
    )
    fig.update_xaxes(tickangle=-32)
    return fig


def bar(frame: pd.DataFrame, x: str, y: str, *, colour: str = SERIES[0],
        horizontal: bool = False, text: str | None = None,
        colours: list[str] | None = None):
    """
    A single-series bar with rounded data-ends and optional direct labels.

    Labels sit *outside* the bar, and Plotly does not widen the axis to make
    room for them: the longest bar is the one whose label runs off the plot,
    which is reliably the one the reader most wants to read. So the value axis
    gets a headroom pad whenever there are labels -- 18% of the range, which
    holds a "$12.96M" beside the longest bar at the sizes used here.
    """
    import plotly.graph_objects as go

    marker = dict(color=colours if colours else colour,
                  line=dict(width=2, color=SURFACE), cornerradius=4)
    values = pd.to_numeric(frame[y], errors="coerce")
    low = float(min(0.0, values.min())) if len(values) else 0.0
    high = float(max(0.0, values.max())) if len(values) else 1.0
    pad = (high - low) * 0.18 if text and high > low else 0.0

    if horizontal:
        fig = go.Figure(go.Bar(y=frame[x], x=frame[y], orientation="h", marker=marker,
                               text=frame[text] if text else None,
                               textposition="outside",
                               textfont=dict(color=INK_SECONDARY, size=11),
                               hovertemplate="%{y}: %{x:,.2f}<extra></extra>"))
        fig.update_yaxes(showgrid=False, autorange="reversed")
        fig.update_xaxes(showgrid=True, gridcolor=GRID)
        if pad:
            fig.update_xaxes(range=[low - (pad if low < 0 else 0), high + pad])
    else:
        fig = go.Figure(go.Bar(x=frame[x], y=frame[y], marker=marker,
                               text=frame[text] if text else None,
                               textposition="outside",
                               textfont=dict(color=INK_SECONDARY, size=11),
                               hovertemplate="%{x}: %{y:,.2f}<extra></extra>"))
        if pad:
            fig.update_yaxes(range=[low - (pad if low < 0 else 0), high + pad])
    return fig


def lines(frame: pd.DataFrame, x: str, series: dict[str, str], *,
          hover_fmt: str = ",.2f"):
    """
    Multi-series lines on ONE axis. ``series`` maps column name to legend label.

    One axis is not a stylistic choice: two y-scales let any pair of series be
    made to cross wherever the author wants, and the reader cannot tell. When
    two measures are on different scales, index them to a common base first.
    """
    import plotly.graph_objects as go

    fig = go.Figure()
    for slot, (column, label) in enumerate(series.items()):
        fig.add_trace(
            go.Scatter(
                x=frame[x], y=frame[column], name=label, mode="lines",
                line=dict(color=SERIES[slot % len(SERIES)], width=2),
                hovertemplate=f"{label}<br>%{{x}}: %{{y:{hover_fmt}}}<extra></extra>",
            )
        )
    fig.update_layout(hovermode="x unified")
    return fig


def scatter(frame: pd.DataFrame, x: str, y: str, *, size: str | None = None,
            colour_by: str | None = None, hover: str | None = None,
            colour_map: dict[str, str] | None = None):
    """
    A scatter, capped at three colour groups.

    Scatter compares every pair of colours at once rather than only neighbours,
    and past three slots the palette cannot hold the separation floor that way.
    A fourth group folds into "Other" rather than getting a made-up hue.
    """
    import plotly.graph_objects as go

    fig = go.Figure()
    sizes = None
    if size and frame[size].max() > 0:
        sizes = 9 + 26 * (frame[size] / frame[size].max()) ** 0.5

    groups = [(None, frame)] if not colour_by else list(frame.groupby(colour_by))
    for slot, (name, group) in enumerate(groups):
        marker_colour = (
            (colour_map or {}).get(name, SERIES[slot % len(SERIES)])
            if colour_by else SERIES[0]
        )
        if colour_by and not colour_map:
            marker_colour = SERIES_ALL_PAIRS[slot % len(SERIES_ALL_PAIRS)]
        fig.add_trace(
            go.Scatter(
                x=group[x], y=group[y], mode="markers", name=str(name) if name else "",
                text=group[hover] if hover else None,
                marker=dict(
                    color=marker_colour, size=sizes.loc[group.index] if sizes is not None else 10,
                    line=dict(width=2, color=SURFACE), opacity=0.86,
                ),
                hovertemplate=(
                    (f"%{{text}}<br>{x}: %{{x:,.2f}}<br>{y}: %{{y:,.3f}}<extra></extra>")
                    if hover else f"{x}: %{{x:,.2f}}<br>{y}: %{{y:,.3f}}<extra></extra>"
                ),
            )
        )
    return fig


def reference_line(fig, *, y: float | None = None, x: float | None = None,
                   label: str = "", colour: str = INK_MUTED):
    """A dashed rule with its label -- a target, a floor, parity on an index."""
    if y is not None:
        fig.add_hline(y=y, line=dict(color=colour, width=1, dash="dot"),
                      annotation_text=label, annotation_position="top left",
                      annotation_font=dict(color=colour, size=11))
    if x is not None:
        # The label is its own annotation. add_vline(annotation_text=...) places
        # it by averaging x, and a Timestamp cannot be averaged: plotly 6.3.1
        # raised TypeError on the forecast page's date axis, so the page only
        # rendered on the plotly this repository happens to pin.
        fig.add_vline(x=x, line=dict(color=colour, width=1, dash="dot"))
        if label:
            fig.add_annotation(x=x, y=1, xref="x", yref="paper", yanchor="bottom",
                               text=label, showarrow=False,
                               font=dict(color=colour, size=11))
    return fig


def _delta_tone(delta: str, requested: str) -> str:
    """
    Which colour a delta wears.

    Streamlit's own metric colours a delta green whenever it parses as
    positive, and a *word* like "Unfavourable" parses as positive -- so an
    unfavourable cost variance used to render with a green up arrow beside it.
    Here a caller can name the tone outright, and anything that is not a signed
    number defaults to no tone at all rather than to green.
    """
    if requested in ("good", "warn", "bad"):
        return requested
    if requested == "off" or not delta:
        return ""
    head = delta.strip()[:1]
    if head not in "+-":
        return ""
    good = head == "+"
    if requested == "inverse":
        good = not good
    return "good" if good else "bad"


def kpis(items) -> None:
    """
    A row of KPI tiles: ``(label, value, delta)`` or ``(label, value, delta,
    tone)``.

    `tone` is ``"normal"``, ``"inverse"``, ``"off"``, or one of the status
    names ``"good"``/``"warn"``/``"bad"`` when the caller already knows which
    way is up -- which is most of the time here, because "favourable" and
    "unfavourable" are properties of the variance, not of its sign.
    """
    for column, item in zip(st.columns(len(items)), items, strict=False):
        label, value, delta = item[0], item[1], item[2]
        requested = item[3] if len(item) > 3 else "normal"
        tone = _delta_tone(str(delta or ""), requested)
        rule = tone if tone in ("good", "warn", "bad") else ""
        body = (
            f'<div class="kpi {rule}">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            + (f'<div class="kpi-delta {tone}">{delta}</div>' if delta else
               '<div class="kpi-delta">&nbsp;</div>')
            + "</div>"
        )
        column.markdown(body, unsafe_allow_html=True)


def table(frame: pd.DataFrame, *, height: int | None = None, **column_config):
    """
    A dataframe with the shared column config.

    Present under every chart that leans on a low-contrast hue -- that is the
    documented relief for the palette's contrast warning, and it is also just
    what an analyst wants when the chart raises a question.
    """
    # height must be omitted rather than passed as None: Streamlit validates it
    # eagerly and rejects None with an error rather than defaulting.
    kwargs = {"height": height} if height else {}
    st.dataframe(frame, width="stretch", hide_index=True,
                 column_config=column_config or None, **kwargs)


def sidebar_filters(frame: pd.DataFrame, fields: dict[str, str]) -> pd.DataFrame:
    """
    Multiselect filters in the sidebar, applied in order.

    Empty means "all", which is the behaviour that avoids the empty-chart
    trap: a filter cleared to nothing should show everything, not nothing.
    """
    filtered = frame
    with st.sidebar:
        st.markdown("### Filters")
        for column, label in fields.items():
            if column not in frame.columns:
                continue
            options = sorted(frame[column].dropna().unique().tolist())
            chosen = st.multiselect(label, options, default=[], key=f"filter_{column}")
            if chosen:
                filtered = filtered[filtered[column].isin(chosen)]
        if len(filtered) != len(frame):
            st.caption(f"{len(filtered):,} of {len(frame):,} rows")
    return filtered


def footer() -> None:
    st.markdown("---")
    caption(
        "Every figure on this page is generated. The cost stack is the real one from a "
        "distributor's ERP; the products, customers, competitors and prices are "
        "synthetic and reproducible from a fixed seed."
    )
