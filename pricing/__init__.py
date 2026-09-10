"""
Pricing analysis, layered on top of the cost stack in ``costing/``.

``costing`` answers *what does this cost*. ``pricing`` answers the questions a
pricing analyst is actually paid for: what is the market charging, what does
the customer really pay us after every deduction, how much volume moves when we
change the price, which of last quarter's margin miss was price and which was
mix, and where is a quote about to go out below the floor.

Every module here is pure — lists, dicts and floats in, dicts out — so the
arithmetic can be asserted on directly rather than through a UI or a dashboard.
"""
