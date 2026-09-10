"""
The product catalogue, customer base and market calendar every generator shares.

The business is **Meridian Supply Co**, a multi-category B2B wholesale
distributor: it imports and buys domestically, holds inventory across two
distribution centres, and sells to marketplace sellers, retail chains,
independent retailers, corporate buyers and sub-distributors. Eight categories,
from consumer electronics to pet supplies. Roughly a quarter of a billion
dollars of revenue.

That shape is chosen for what it exercises rather than for flavour. A
single-category business has no mix effect worth decomposing, no cross-category
elasticity spread, and no interesting cost-to-serve variation. A multi-category
distributor has all three, plus the two things that make wholesale pricing hard:
**an import cost base that moves with freight and commodity indices**, and a
**marketplace channel where every price is one click from a competitor's**.

Split out of the sheet generator so the cost sheet, the market data and the
Power BI model all describe the same business. A SKU in
``sample_data/cost_sheet.xlsx`` is the same SKU in ``data/fact_sales.csv`` is
the same ``product_id`` in the semantic model.

Everything here is a pure function of a seed. Nothing reads the clock.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

# Three *complete* fiscal years, July to June. Complete matters: a range that
# stops mid-year makes every year-on-year comparison a 277-day period against a
# 365-day one, and the resulting "sales are down 24%" is an artefact of the
# calendar rather than a fact about the business.
FY_START_MONTH = 7
DATA_START = date(2023, 7, 1)
DATA_END = date(2026, 6, 30)

DEFAULT_SEED = 613
DEFAULT_ITEMS = 240
DEFAULT_CUSTOMERS = 150

BUSINESS_NAME = "Meridian Supply Co"

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

CATEGORIES = (
    "Consumer Electronics", "Home & Kitchen", "Office & Stationery",
    "Tools & Hardware", "Health & Beauty", "Sporting Goods",
    "Pet Supplies", "Apparel & Accessories",
)

# Share of the catalogue, roughly by SKU count rather than by revenue.
CATEGORY_WEIGHTS = (0.17, 0.16, 0.12, 0.13, 0.12, 0.11, 0.09, 0.10)

SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    "Consumer Electronics": ("Wireless Earbuds", "USB-C Charger", "27in Monitor",
                             "Webcam", "Mechanical Keyboard", "Power Bank",
                             "Smart Plug", "Bluetooth Speaker", "HDMI Cable"),
    "Home & Kitchen": ("Nonstick Cookware Set", "Air Fryer", "Storage Bins",
                       "Bedding Set", "LED Floor Lamp", "Blender",
                       "Vacuum Sealer", "Cutlery Block"),
    "Office & Stationery": ("Copy Paper", "Toner Cartridge", "Desk Organiser",
                            "Filing Boxes", "Dry-Erase Board", "Gel Pens",
                            "Laminator"),
    "Tools & Hardware": ("Cordless Drill", "Socket Set", "Fastener Assortment",
                         "Safety Glasses", "Laser Measure", "Tool Chest",
                         "Work Gloves"),
    "Health & Beauty": ("Vitamin C Serum", "Multivitamin", "Electric Toothbrush",
                        "Hair Dryer", "Sunscreen SPF50", "Body Wash"),
    "Sporting Goods": ("Adjustable Dumbbells", "Yoga Mat", "Camping Tent",
                       "Cycling Helmet", "Resistance Bands", "Cooler Box"),
    "Pet Supplies": ("Dry Dog Food", "Cat Litter", "Chew Toy Pack",
                     "Grooming Kit", "Pet Bed", "Aquarium Filter"),
    "Apparel & Accessories": ("Work Jacket", "Safety Boots", "Backpack",
                              "Cotton T-Shirt 5pk", "Winter Gloves",
                              "Laptop Sleeve"),
}

# Brand tier drives target margin, price premium and how elastic buyers are.
# Private label is where a distributor makes its money and where it competes on
# price against nobody but itself.
BRAND_TIERS = ("Private label", "Value", "National brand", "Premium brand")
BRAND_TIER_WEIGHTS = (0.22, 0.24, 0.36, 0.18)
BRAND_TIER_MARGIN = {"Private label": 0.34, "Value": 0.17,
                     "National brand": 0.19, "Premium brand": 0.26}
BRAND_TIER_PREMIUM = {"Private label": 0.86, "Value": 0.90,
                      "National brand": 1.06, "Premium brand": 1.42}

# Selling unit against billing unit. A distributor quotes per each and bills per
# case, and the conversion between them is where a price goes wrong by a factor
# of twelve without anybody noticing.
PACK_FORMATS = (
    ("Each", 1.0), ("Inner pack 6", 6.0), ("Case 12", 12.0),
    ("Case 24", 24.0), ("Master carton 48", 48.0), ("Bulk pallet 144", 144.0),
)

LIFECYCLE = ("Launch", "Growth", "Mature", "Decline")
LIFECYCLE_WEIGHTS = (0.08, 0.19, 0.58, 0.15)

# Own-price elasticity by category. Consumer electronics and office consumables
# are shopped against a visible marketplace price and barely differentiated; pet
# food and beauty are habitual and brand-loyal, and a point of price moves very
# little of either.
CATEGORY_ELASTICITY = {
    "Consumer Electronics": -2.4,
    "Office & Stationery": -2.2,
    "Apparel & Accessories": -2.0,
    "Home & Kitchen": -1.9,
    "Sporting Goods": -1.7,
    "Tools & Hardware": -1.6,
    "Health & Beauty": -1.3,
    "Pet Supplies": -1.1,
}

# Which input index each category's landed cost tracks. One index per category,
# deliberately: when two categories with different pass-through share an index,
# the "true" pass-through for that index is a blend that neither category
# actually follows, and any attempt to recover it is measuring an average of
# two behaviours against a series that is the sum of both.
CATEGORY_INDEX = {
    "Consumer Electronics": "Semiconductor & Components",
    "Home & Kitchen": "Resin & Plastics",
    "Office & Stationery": "Paper & Pulp",
    "Tools & Hardware": "Steel & Fabricated Metal",
    "Health & Beauty": "Chemicals & Actives",
    "Sporting Goods": "Aluminium & Composites",
    "Pet Supplies": "Agricultural Inputs",
    "Apparel & Accessories": "Cotton & Textiles",
}

# Monthly demand seasonality, January to December. Electronics and home peak
# into the holidays; office peaks at back-to-school and again in January;
# sporting goods and tools peak in spring; health and beauty spikes in January.
SEASONALITY: dict[str, tuple[float, ...]] = {
    "Consumer Electronics": (0.86, 0.82, 0.88, 0.90, 0.94, 0.96,
                             0.98, 1.02, 1.04, 1.14, 1.52, 1.44),
    "Home & Kitchen":       (0.88, 0.86, 0.94, 0.98, 1.04, 1.02,
                             0.98, 1.00, 1.02, 1.10, 1.38, 1.30),
    "Office & Stationery":  (1.16, 1.02, 0.96, 0.94, 0.92, 0.84,
                             0.96, 1.34, 1.28, 1.02, 0.92, 0.84),
    "Tools & Hardware":     (0.84, 0.88, 1.06, 1.18, 1.22, 1.16,
                             1.08, 1.02, 1.00, 0.96, 0.92, 0.88),
    "Health & Beauty":      (1.34, 1.08, 1.00, 0.96, 0.98, 1.02,
                             1.04, 0.96, 0.92, 0.94, 1.10, 1.16),
    "Sporting Goods":       (1.10, 1.02, 1.08, 1.16, 1.24, 1.22,
                             1.14, 1.00, 0.92, 0.86, 0.88, 0.94),
    "Pet Supplies":         (1.00, 0.98, 1.00, 1.02, 1.02, 1.00,
                             1.00, 0.98, 0.98, 1.00, 1.02, 1.04),
    "Apparel & Accessories": (0.88, 0.84, 0.96, 1.02, 1.06, 0.98,
                              0.94, 1.10, 1.14, 1.08, 1.06, 0.98),
}

SUPPLIERS = (
    "Shenzhen Kaiyuan Electronics", "Ningbo Hometech Manufacturing",
    "Guangzhou Vantage Industrial", "Vertex Consumer Products (US)",
    "Ashworth Tools & Hardware", "Silverline Personal Care",
    "Northbrook Paper & Board", "Harlow Textile Group",
    "Cascadia Pet Products", "Meridian Private Label",
)

# Inbound lanes, matched by costing.formulas.get_freight_cost. An import lane
# costs several times a domestic one per unit, and the mix between them is one
# of the largest single drivers of landed cost in a distribution business.
VENDOR_LANES = (
    "Import Ocean FCL", "Import Ocean LCL", "Import Air Freight",
    "Domestic LTL", "Domestic FTL", "Cross-dock Consolidator",
)

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

SEGMENTS = (
    "Marketplace Seller", "Regional Retail Chain", "Independent Retailer",
    "Corporate & B2B Buyer", "Government & Education", "Sub-Distributor",
    "Subscription Box Operator", "E-commerce Pure-Play",
)
SEGMENT_WEIGHTS = (0.22, 0.11, 0.21, 0.13, 0.07, 0.10, 0.06, 0.10)

SEGMENT_CHANNEL = {
    "Marketplace Seller": "Marketplace",
    "Regional Retail Chain": "Retail",
    "Independent Retailer": "Retail",
    "Corporate & B2B Buyer": "B2B Direct",
    "Government & Education": "B2B Direct",
    "Sub-Distributor": "Wholesale",
    "Subscription Box Operator": "Wholesale",
    "E-commerce Pure-Play": "Marketplace",
}

# How price-sensitive each segment is when quoting. A marketplace seller is
# reselling into a buy-box auction and shops every quote; a government buyer is
# on a tendered contract and a school district is buying to a specification.
SEGMENT_PRICE_SENSITIVITY = {
    "Marketplace Seller": 16.5,
    "E-commerce Pure-Play": 14.5,
    "Sub-Distributor": 15.0,
    "Regional Retail Chain": 12.5,
    "Independent Retailer": 9.0,
    "Subscription Box Operator": 8.0,
    "Corporate & B2B Buyer": 7.0,
    "Government & Education": 6.0,
}

REGIONS = ("West", "Southwest", "Midwest", "Northeast", "Southeast")
REGION_WEIGHTS = (0.26, 0.14, 0.19, 0.23, 0.18)

# Volume tier. Drives range breadth, discount depth and rebate eligibility.
TIERS = ("A", "B", "C", "D")
TIER_WEIGHTS = (0.08, 0.19, 0.36, 0.37)
TIER_RANGE = {"A": (30, 46), "B": (18, 31), "C": (10, 19), "D": (4, 11)}
TIER_VOLUME_WEIGHT = {"A": 7.4, "B": 3.1, "C": 1.35, "D": 0.55}
# Base on-invoice discount as a fraction of list, before product and promotion.
TIER_DISCOUNT = {"A": 0.115, "B": 0.082, "C": 0.048, "D": 0.021}
TIER_REBATE = {"A": 0.028, "B": 0.016, "C": 0.005, "D": 0.0}

PRICE_LISTS = ("Contract", "Negotiated", "List")
PAYMENT_TERMS = ("Net 15", "Net 30", "2/10 Net 30", "Net 45", "Net 60")
PAYMENT_TERM_DISCOUNT = {
    "Net 15": 0.0, "Net 30": 0.0, "2/10 Net 30": 0.018, "Net 45": 0.004, "Net 60": 0.009,
}

# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------

COMPETITORS = (
    ("C1", "Atlas Wholesale Group", "Mainstream", 1.005, 0.72),
    ("C2", "PriceBreak Distribution", "Discount", 0.918, 0.61),
    ("C3", "Sterling Trade Supply", "Premium", 1.124, 0.44),
    ("C4", "Continental B2B Direct", "Mainstream", 0.978, 0.55),
    ("C5", "Vantage Specialty Supply", "Premium", 1.168, 0.28),
)

OBSERVATION_SOURCES = ("Marketplace scrape", "Distributor price list",
                       "Field report", "Lost quote")

# ---------------------------------------------------------------------------
# Input cost indices
# ---------------------------------------------------------------------------

# name -> (annual drift, weekly volatility, seasonal amplitude, seasonal peak month)
# Ocean freight is the volatile one and it touches every imported category, which
# is why it is modelled separately from the goods themselves.
COMMODITY_INDICES: dict[str, tuple[float, float, float, int]] = {
    "Semiconductor & Components": (0.031, 0.0125, 0.030, 10),
    "Resin & Plastics":           (0.048, 0.0140, 0.036, 6),
    "Steel & Fabricated Metal":   (0.062, 0.0160, 0.044, 4),
    "Cotton & Textiles":          (0.027, 0.0135, 0.052, 9),
    "Paper & Pulp":               (0.055, 0.0090, 0.028, 8),
    "Chemicals & Actives":        (0.039, 0.0105, 0.022, 1),
    "Agricultural Inputs":        (0.034, 0.0115, 0.041, 7),
    "Aluminium & Composites":     (0.052, 0.0145, 0.033, 5),
    "Ocean Freight Container":    (0.089, 0.0265, 0.085, 9),
}

# ---------------------------------------------------------------------------
# Sales team
# ---------------------------------------------------------------------------

# (name, home region, tenure in years, discount appetite). Discount appetite is
# a multiplier on the discount a rep grants beyond what the customer's tier
# entitles them to. It is the reason a salesperson dimension belongs in a
# pricing model at all: two reps working the same segment at the same volume
# can be four margin points apart, and no product- or customer-level cut of the
# data will show you that.
SALESPEOPLE: tuple[tuple[str, str, float, float], ...] = (
    ("Dana Whitfield", "West", 11.0, 0.82),
    ("Marcus Oyelaran", "West", 6.5, 1.24),
    ("Priya Raghunathan", "West", 3.0, 1.41),
    ("Tom Beauchemin", "Southwest", 8.0, 0.95),
    ("Alix Fontaine", "Southwest", 2.0, 1.33),
    ("Rowan Achterberg", "Midwest", 14.0, 0.74),
    ("Simone Delacroix", "Midwest", 4.5, 1.12),
    ("Yusuf Kandemir", "Northeast", 9.0, 0.88),
    ("Nikita Petrenko", "Northeast", 1.5, 1.52),
    ("Hollis Marchetti", "Southeast", 7.0, 1.03),
    ("Bea Okonkwo", "Southeast", 5.0, 1.18),
    ("Jonah Stavrides", "Midwest", 12.0, 0.79),
)

# ---------------------------------------------------------------------------
# Cost elements
# ---------------------------------------------------------------------------

# (element, behaviour, whose number it is). Behaviour is what break-even
# analysis turns on: only the variable elements scale with the next unit sold,
# and a break-even computed on fully absorbed cost is wrong by the whole fixed
# pool. `owner` is who gets asked about a variance in it.
COST_ELEMENTS: tuple[tuple[str, str, str], ...] = (
    ("Goods", "Variable", "Merchandising"),
    ("Shrink and damage", "Variable", "Operations"),
    ("Inbound freight and duty", "Variable", "Logistics"),
    ("Pick, pack and handling", "Variable", "Operations"),
    ("Packaging and labelling", "Variable", "Operations"),
    ("Variable warehouse overhead", "Variable", "Operations"),
    ("Fixed warehouse overhead", "Fixed", "Operations"),
    ("Selling and admin", "Fixed", "Commercial"),
)

VARIABLE_ELEMENTS = tuple(name for name, behaviour, _ in COST_ELEMENTS if behaviour == "Variable")
FIXED_ELEMENTS = tuple(name for name, behaviour, _ in COST_ELEMENTS if behaviour == "Fixed")

# ---------------------------------------------------------------------------
# Price-list operations
# ---------------------------------------------------------------------------

PRICE_CHANGE_REASONS = (
    "Cost pass-through", "Annual review", "Market correction",
    "Customer negotiation", "Promotional reset", "Competitive response",
)
APPROVAL_STATES = ("Auto-approved", "Approved", "Pending", "Rejected")
# (mechanic, volume lift, discount depth range). The lift and the depth have to
# be set together, and they are what decides whether a promotion pays: a deal
# event buys a lot of traffic for a deep cut, a volume bracket buys very little
# for a shallow one. Setting one lift for every mechanic is what produces a
# promotion analysis where nothing ever works, which is as unbelievable as one
# where everything does.
PROMO_MECHANICS: tuple[tuple[str, float, tuple[float, float]], ...] = (
    ("Marketplace deal event", 2.40, (0.10, 0.18)),
    ("Bundle offer", 1.46, (0.06, 0.12)),
    ("Temporary price reduction", 1.34, (0.05, 0.12)),
    ("Rebate accelerator", 1.24, (0.02, 0.05)),
    ("Volume bracket", 1.17, (0.03, 0.07)),
)
PROMO_MECHANIC_NAMES = tuple(name for name, _, _ in PROMO_MECHANICS)
PROMO_MECHANIC_WEIGHTS = (0.14, 0.16, 0.34, 0.16, 0.20)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def fiscal_year(day: date) -> int:
    """FY2026 runs 2025-07-01 to 2026-06-30, and is named for the year it ends."""
    return day.year + 1 if day.month >= FY_START_MONTH else day.year


def fiscal_period(day: date) -> int:
    """1 in July through 12 in June."""
    return (day.month - FY_START_MONTH) % 12 + 1


def month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def add_months(day: date, months: int) -> date:
    total = (day.year * 12 + day.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def month_range(start: date = DATA_START, end: date = DATA_END) -> list[date]:
    """Every month start in the window, inclusive."""
    out, cursor = [], month_start(start)
    while cursor <= end:
        out.append(cursor)
        cursor = add_months(cursor, 1)
    return out


def day_range(start: date = DATA_START, end: date = DATA_END) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def week_starts(start: date = DATA_START, end: date = DATA_END) -> list[date]:
    """Monday-anchored week starts covering the window."""
    first = start - timedelta(days=start.weekday())
    out, cursor = [], first
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


# ---------------------------------------------------------------------------
# Weighted choice that does not depend on pandas
# ---------------------------------------------------------------------------


def pick(rng: np.random.Generator, options, weights=None, size=None):
    """
    Deterministic weighted choice over a tuple of labels.

    Uses ``rng.choice`` on integer indices rather than on the object array:
    numpy's choice over object dtype has changed its consumption of the bit
    stream between releases, so indexing keeps a seed reproducing the same
    catalogue on a different numpy.
    """
    idx = rng.choice(len(options), size=size, p=weights)
    if size is None:
        return options[int(idx)]
    return [options[int(i)] for i in np.atleast_1d(idx)]
