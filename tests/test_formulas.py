"""
Tests for the pricing maths.

This is a tool that sets selling prices. The arithmetic had no tests at all,
which is the wrong place to have none — a margin formula that is quietly wrong
does not crash, it just underprices every item until someone notices the
gross margin at month end.
"""

from __future__ import annotations

import math

import pytest

from costing.formulas import (
    FREIGHT_RATES,
    build_cost_stack,
    calculate_actual_inv_cost,
    calculate_margin_percent,
    calculate_price_from_margin,
    calculate_recovery_input,
    calculate_trim_recovery,
    calculate_waste_output,
    clean_item_code,
    get_freight_cost,
    normalize_recovery,
    safe_float,
)


class TestItemCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (13667, "13667"),
            (13667.0, "13667"),          # Excel reads integers as floats
            ("13667", "13667"),
            ("13,667", "13667"),          # thousands separator
            (" 13667 ", "13667"),
            ("A-13667", "A-13667"),       # non-numeric passes through
            (13667.5, "13667.5"),
        ],
    )
    def test_codes_normalise_to_one_string(self, raw, expected):
        """The two uploaded sheets only join if codes land on the same string."""
        assert clean_item_code(raw) == expected


class TestSafeFloat:
    @pytest.mark.parametrize(
        "raw,expected",
        [(1.5, 1.5), ("2.5", 2.5), (None, 0.0), ("", 0.0), ("abc", 0.0), (float("nan"), 0.0)],
    )
    def test_junk_becomes_the_default(self, raw, expected):
        assert safe_float(raw) == expected


class TestRecoveryNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (0.68, 0.68),   # already a fraction
            (68, 0.68),     # a percentage
            (85.0, 0.85),
            (1.0, 1.0),     # 100%, not 1%
            (100, 1.0),
            (0, 0.0),
            (-5, 0.0),
        ],
    )
    def test_percentages_and_fractions_agree(self, raw, expected):
        assert normalize_recovery(raw) == pytest.approx(expected)

    def test_recovery_input_reads_85_and_0_85_the_same(self):
        """
        The regression this exists for: the standalone helper divided by the
        raw value while the spreadsheet auto-fill path divided by the
        normalised one, so an item entered as 85 came out 100x cheaper than
        the identical item entered as 0.85.
        """
        as_percent = calculate_recovery_input(10.0, 0.20, 85)
        as_fraction = calculate_recovery_input(10.0, 0.20, 0.85)
        assert as_percent == pytest.approx(as_fraction)
        assert as_percent == pytest.approx(10.20 / 0.85)

    def test_waste_output_reads_both_forms_the_same(self):
        assert calculate_waste_output(100.0, 80) == pytest.approx(
            calculate_waste_output(100.0, 0.80))

    def test_trim_recovery_reads_both_forms_the_same(self):
        assert calculate_trim_recovery(2.0, 10, 80) == pytest.approx(
            calculate_trim_recovery(2.0, 0.10, 0.80)
        )

    def test_zero_recovery_does_not_divide_by_zero(self):
        assert calculate_recovery_input(10.0, 0.2, 0) == 0.0
        assert calculate_waste_output(10.0, 0) == 0.0
        assert calculate_trim_recovery(1.0, 0.1, 0) == 0.0


class TestRecoveryMaths:
    def test_lower_recovery_costs_more(self):
        """A lower sellable rate means buying more units for every one shipped."""
        high = calculate_recovery_input(10.0, 0.0, 0.90)
        low = calculate_recovery_input(10.0, 0.0, 0.60)
        assert low > high
        assert low == pytest.approx(10.0 / 0.60)

    def test_full_recovery_changes_nothing(self):
        assert calculate_recovery_input(10.0, 0.5, 1.0) == pytest.approx(10.5)

    def test_waste_is_the_gap_between_grossed_up_and_raw(self):
        # At 80% recovery, 100 of raw material really costs 125, so waste is 25.
        assert calculate_waste_output(100.0, 0.80) == pytest.approx(25.0)


class TestPricing:
    def test_price_is_margin_not_markup(self):
        """
        The distinction that costs money. At a 25% margin, a $10 cost prices at
        $13.33 (10 / 0.75), not $12.50 (10 x 1.25). Using markup here would
        underprice every item by the difference.
        """
        assert calculate_price_from_margin(10.0, 0.25) == pytest.approx(13.3333, rel=1e-4)
        assert calculate_price_from_margin(10.0, 0.25) != pytest.approx(12.50)

    def test_price_round_trips_to_the_requested_margin(self):
        for margin in (0.05, 0.17, 0.25, 0.40, 0.60):
            price = calculate_price_from_margin(12.34, margin)
            assert calculate_margin_percent(price, 12.34) == pytest.approx(margin)

    def test_margin_accepts_percentage_or_fraction(self):
        assert calculate_price_from_margin(10.0, 25) == pytest.approx(
            calculate_price_from_margin(10.0, 0.25)
        )

    def test_margin_of_100_percent_or_more_has_no_finite_price(self):
        assert calculate_price_from_margin(10.0, 1.0) == 10.0    # 100%
        assert calculate_price_from_margin(10.0, 150) == 10.0    # 150%
        assert math.isfinite(calculate_price_from_margin(10.0, 0.999))

    def test_the_ambiguous_boundary_is_documented_not_guessed(self):
        """
        Above 1, a margin is read as a percentage — so 1.5 means 1.5%, not
        150%. That is the same convention recovery uses, and it is the reason
        1.0 has to mean 100%: if 1.0 were read as 1% there would be no way to
        express a full margin at all. Pinned here because the boundary is the
        kind of thing a later refactor quietly flips.
        """
        assert calculate_price_from_margin(10.0, 1.5) == pytest.approx(10.0 / (1 - 0.015))
        assert calculate_price_from_margin(10.0, 1.0) == 10.0

    def test_zero_margin_prices_at_cost(self):
        assert calculate_price_from_margin(10.0, 0.0) == 10.0


class TestFreight:
    @pytest.mark.parametrize(
        "vendor,expected",
        [
            ("Import Ocean FCL", FREIGHT_RATES["Import Ocean FCL"]),
            ("FCL Shanghai", FREIGHT_RATES["Import Ocean FCL"]),
            ("Import Ocean LCL", FREIGHT_RATES["Import Ocean LCL"]),
            ("Import Air Freight", FREIGHT_RATES["Import Air Freight"]),
            ("Domestic LTL", FREIGHT_RATES["Domestic LTL"]),
            ("Domestic FTL", FREIGHT_RATES["Domestic FTL"]),
            ("Cross-dock Consolidator", FREIGHT_RATES["Cross-dock Consolidator"]),
            ("", 0.0),
            (None, 0.0),
            ("Somewhere else", 0.0),
        ],
    )
    def test_lane_lookup(self, vendor, expected):
        assert get_freight_cost(vendor) == expected

    def test_farther_lanes_cost_more(self):
        assert (
            FREIGHT_RATES["Domestic FTL"]
            < FREIGHT_RATES["Domestic LTL"]
            < FREIGHT_RATES["Import Ocean FCL"]
            < FREIGHT_RATES["Import Ocean LCL"]
            < FREIGHT_RATES["Import Air Freight"]
        )


class TestUnitConversion:
    def test_invoice_price_converts_to_per_pound(self):
        # A $110 case holding 10 lb is $11/unit.
        assert calculate_actual_inv_cost(110.0, 10.0) == pytest.approx(11.0)

    def test_zero_weight_leaves_the_price_alone(self):
        """Guard against a divide-by-zero on a row with a missing case weight."""
        assert calculate_actual_inv_cost(110.0, 0.0) == 110.0


class TestCostStack:
    def test_walks_invoice_to_price(self):
        stack = build_cost_stack(
            vendor_invoice_price=110.0,
            units_per_billing_uom=10.0,
            adj=0.50,
            vendor="Import Ocean FCL",
            recovery=0.80,
            handling_per_unit=0.35,
            labelling_per_unit=0.05,
            base_margin=0.17,
        )
        assert stack["actual_inv_cost"] == pytest.approx(11.0)
        assert stack["market_cost"] == pytest.approx(11.50)
        assert stack["freight"] == pytest.approx(0.42)
        assert stack["landed_cost"] == pytest.approx(11.92)
        # Grossed up for an 80% sellable rate, then handling and labelling.
        assert stack["recovery_input"] == pytest.approx(11.92 / 0.80)
        assert stack["final_cost"] == pytest.approx(11.92 / 0.80 + 0.40)
        assert stack["realised_base_margin"] == pytest.approx(0.17)

    def test_every_step_is_monotonic(self):
        """Cost only accumulates: no step in the stack may reduce it."""
        stack = build_cost_stack(
            vendor_invoice_price=90.0,
            units_per_billing_uom=9.0,
            adj=0.25,
            vendor="Import Air Freight",
            recovery=0.72,
            handling_per_unit=0.40,
            labelling_per_unit=0.10,
        )
        assert (
            stack["actual_inv_cost"]
            < stack["market_cost"]
            < stack["landed_cost"]
            < stack["recovery_input"]
            < stack["final_cost"]
            < stack["base_price"]
        )

    def test_price_covers_cost_at_every_plausible_margin(self):
        for margin in (0.05, 0.17, 0.25, 0.35):
            stack = build_cost_stack(
                vendor_invoice_price=100.0,
                units_per_billing_uom=10.0,
                recovery=0.75,
                base_margin=margin,
            )
            assert stack["base_price"] > stack["final_cost"]
            assert stack["base_margin_dollars"] > 0
