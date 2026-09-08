# Cost-to-Price Calculator

[![CI](https://github.com/KushPatel29/cost-to-price-calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/KushPatel29/cost-to-price-calculator/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-67-brightgreen)
![python](https://img.shields.io/badge/python-3.12-blue)

**Live app:** [cost-to-price-calculator.streamlit.app](https://cost-to-price-calculator.streamlit.app/)

A Streamlit tool that walks a product from a vendor invoice to a selling price
at a meat processor, and pushes the result back into the ERP's price list.

Vendor invoices arrive in cases; products are sold by the pound. Between the
two sit unit conversion, a market adjustment, inbound freight that depends on
the lane, processing loss, trim credit, labour, sticker cost, and finally a
target margin. Getting any of those wrong changes the price of everything.

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

It opens on generated sample data, so there is nothing to prepare — 90 items
built by `seed/generate_sheets.py` with a fixed seed. Switch the radio to
**Upload my own files** to run it on your own cost sheet and ERP export.

---

## The cost stack

```
vendor invoice price
  ÷ lb per billing UOM      →  actual invoice cost per lb
  + market adjustment       →  market cost
  + freight (by lane)       →  landed cost
  ÷ recovery                →  recovery input      ← processing loss
  + labour + sticker        →  final cost
  ÷ (1 − margin)            →  base price
```

Two of those steps are where this kind of tool usually goes wrong.

**Recovery is a divisor, not a discount.** A 100 lb primal that trims to 68 lb
of saleable product has a recovery of 0.68. You need 1/0.68 lb of raw material
for every pound you sell, so cost is *divided* by recovery. At 60% recovery a
$10/lb input costs $16.67/lb; at 90% it costs $11.11.

**Margin is a fraction of price, not of cost.** At a 25% margin a $10 cost
prices at `10 / 0.75 = $13.33`, not `10 × 1.25 = $12.50`. Using markup where
margin is meant underprices every item, and it never announces itself — the
tool keeps working, the gross margin just comes in light.

Both are pinned by tests, including a round-trip that prices at a target margin
and then recomputes the realised margin from the result.

---

## What I changed

The tool worked. It was a single 517-line Streamlit script with **no tests** —
which is the wrong place to have none, because pricing arithmetic that is
quietly wrong does not crash.

**Extracted the maths.** All the costing functions moved to
[`costing/formulas.py`](costing/formulas.py), which imports nothing from
Streamlit. The app imports them back, so behaviour is unchanged, but the
arithmetic can now be asserted on directly instead of only through a UI.

**Fixed a percent/fraction inconsistency.** Recovery arrives from spreadsheets
as either `85` or `0.85`. The spreadsheet auto-fill path normalised it
(`x/100 if x > 1 else x`); the standalone helpers divided by the raw value. So
the *same item* produced two different costs depending on which path ran — one
of them 100× off. There is now a single `normalize_recovery` that both paths
use, and a test asserting `85` and `0.85` give the same answer.

**Documented the ambiguous boundary instead of guessing at it.** Under the
"above 1 means percent" convention, `1.5` reads as 1.5% and `1.0` has to mean
100% — if `1.0` were read as 1% there would be no way to express a full margin
at all. My own test caught this while I was writing it; rather than paper over
it, the convention is now stated in the code and pinned by a test.

**67 tests** over unit conversion, freight lanes, recovery, margin, the full
stack, and the generated sample sheets.

---

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
python -m venv .venv && source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt

python -m seed.generate_sheets
streamlit run app/streamlit_app.py
```

Upload `sample_data/cost_sheet.xlsx` (sheet `Cost Sheet`) and
`sample_data/export_sheet.xlsx` (sheet `AllProducts`).

```bash
pytest -q
```

---

## The sample data

`seed/generate_sheets.py` writes the two workbooks the app validates against —
a 49-column cost sheet and the ERP export — with every required column present.
A test mirrors the app's own `cost_required` set and fails if the generator
drifts from it, so the "generate and upload" instruction above cannot quietly
stop working.

The prices are built by running the real `build_cost_stack`, so the sample data
is internally consistent by construction: a test asserts each step of the stack
is at least the one before it, and that every base price realises exactly the
margin it claims.

The export sheet deliberately holds five product codes the cost sheet has never
seen. Reporting what it could not price is part of the tool's job, and without
orphans in the data that path is never exercised.

---

## Notes

Originally built for a live ERP. Employer identifiers, vendor names and real
cost data have been removed; the freight lanes are generic and everything in
`sample_data/` is generated.
