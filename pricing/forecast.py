"""
Forecasting revenue, volume, cost and margin -- and proving which method to use.

Six methods, all simple, and a rolling-origin backtest that picks between them
on out-of-sample error. That order matters. Choosing a method first and
reporting its in-sample fit is how forecasting goes wrong in a portfolio: any
method can be made to fit history, and the number a business acts on is the one
for a month that has not happened.

The methods are deliberately unglamorous:

* **naive** -- last month repeated. The bar every other method has to clear.
* **seasonal naive** -- the same month last year. On seasonal demand this is
  hard to beat and frequently is not beaten.
* **moving average** -- the last k months, which trades responsiveness for
  noise rejection.
* **drift** -- last value plus the average change per period.
* **seasonal naive with drift** -- last year's month, moved by the year's trend.
* **Holt-Winters** -- level, trend and season, additive, with the smoothing
  constants chosen by grid search on the training window.

Written out rather than imported because the whole set is about a hundred lines
and a statsmodels dependency is the largest thing that would then be in a
Streamlit app's requirements file.

Intervals come from the **backtest residuals**, not from a distributional
assumption. The spread of the errors this method actually made at this horizon
on this series is a claim you can check; a normal interval around a fitted
model is a claim about a model.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from typing import Any

from costing.formulas import safe_float

PERIOD = 12
MIN_TRAIN = 18          # a season and a half; less cannot see a season at all
DEFAULT_HORIZON = 6


def _clean(values: Sequence[Any]) -> list[float]:
    return [safe_float(v, 0.0) for v in values]


# --------------------------------------------------------------------------
# Methods. Each takes the history and a horizon and returns `horizon` points.
# --------------------------------------------------------------------------

def naive(history: Sequence[float], horizon: int, period: int = PERIOD) -> list[float]:
    return [history[-1]] * horizon if history else [0.0] * horizon


def seasonal_naive(history: Sequence[float], horizon: int, period: int = PERIOD) -> list[float]:
    if len(history) < period:
        return naive(history, horizon)
    return [history[-period + (i % period)] for i in range(horizon)]


def moving_average(history: Sequence[float], horizon: int, period: int = PERIOD,
                   window: int = 3) -> list[float]:
    if not history:
        return [0.0] * horizon
    window = min(window, len(history))
    value = sum(history[-window:]) / window
    return [value] * horizon


def drift(history: Sequence[float], horizon: int, period: int = PERIOD) -> list[float]:
    if len(history) < 2:
        return naive(history, horizon)
    slope = (history[-1] - history[0]) / (len(history) - 1)
    return [history[-1] + slope * (i + 1) for i in range(horizon)]


def seasonal_naive_drift(history: Sequence[float], horizon: int,
                         period: int = PERIOD) -> list[float]:
    """Last year's month, moved by how much the year as a whole has moved."""
    if len(history) < period * 2:
        return seasonal_naive(history, horizon, period)
    recent = sum(history[-period:]) / period
    prior = sum(history[-2 * period:-period]) / period
    growth = (recent / prior) if prior > 0 else 1.0
    base = seasonal_naive(history, horizon, period)
    return [value * growth for value in base]


def holt_winters(history: Sequence[float], horizon: int, period: int = PERIOD,
                 grid: Sequence[float] = (0.1, 0.3, 0.5, 0.7, 0.9)) -> list[float]:
    """
    Additive Holt-Winters with the smoothing constants grid-searched in sample.

    Grid-searched on one-step training error, which is the honest thing to do
    when the alternative is three magic numbers. It still gets judged out of
    sample against everything else -- fitting the constants better does not
    make the method better, and on seasonal food demand it routinely loses to
    seasonal naive.
    """
    if len(history) < period * 2:
        return seasonal_naive(history, horizon, period)

    best: tuple[float, list[float]] | None = None
    for alpha in grid:
        for beta in grid:
            for gamma in grid:
                fitted, forecasts = _holt_winters_once(history, horizon, period,
                                                       alpha, beta, gamma)
                error = sum(abs(a - f) for a, f in zip(history[period:], fitted,
                                                       strict=False))
                if best is None or error < best[0]:
                    best = (error, forecasts)
    return best[1] if best else seasonal_naive(history, horizon, period)


def _holt_winters_once(history: Sequence[float], horizon: int, period: int,
                       alpha: float, beta: float, gamma: float
                       ) -> tuple[list[float], list[float]]:
    level = sum(history[:period]) / period
    trend = (sum(history[period:2 * period]) - sum(history[:period])) / (period * period)
    season = [history[i] - level for i in range(period)]

    fitted = []
    for i in range(period, len(history)):
        value = history[i]
        prediction = level + trend + season[i % period]
        fitted.append(prediction)
        previous_level = level
        level = alpha * (value - season[i % period]) + (1 - alpha) * (level + trend)
        trend = beta * (level - previous_level) + (1 - beta) * trend
        season[i % period] = gamma * (value - level) + (1 - gamma) * season[i % period]

    forecasts = [
        level + trend * (i + 1) + season[(len(history) + i) % period]
        for i in range(horizon)
    ]
    return fitted, forecasts


METHODS: dict[str, Callable[..., list[float]]] = {
    "Naive": naive,
    "Seasonal naive": seasonal_naive,
    "Moving average (3)": lambda h, n, p=PERIOD: moving_average(h, n, p, window=3),
    "Drift": drift,
    "Seasonal naive with drift": seasonal_naive_drift,
    "Holt-Winters": holt_winters,
}


# --------------------------------------------------------------------------
# Accuracy
# --------------------------------------------------------------------------

def wape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """
    Weighted absolute percentage error: total error over total actual.

    Preferred to MAPE here because MAPE divides by each actual, so one quiet
    month with near-zero volume dominates the average and the metric stops
    describing the series. WAPE weights by size, which is what a business
    means when it asks how wrong the forecast was.
    """
    total = sum(abs(a) for a in actual)
    if total == 0:
        return float("nan")
    return sum(abs(a - p) for a, p in zip(actual, predicted, strict=False)) / total


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted, strict=False) if a != 0]
    if not pairs:
        return float("nan")
    return sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def bias(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """
    Signed error over total actual. A method can have low WAPE and still lean
    high every month, which compounds into inventory or into a plan nobody hits.
    """
    total = sum(abs(a) for a in actual)
    if total == 0:
        return float("nan")
    return sum(p - a for a, p in zip(actual, predicted, strict=False)) / total


# --------------------------------------------------------------------------
# Rolling-origin backtest
# --------------------------------------------------------------------------

def backtest(
    values: Sequence[Any],
    method: str,
    *,
    horizon: int = DEFAULT_HORIZON,
    min_train: int = MIN_TRAIN,
    period: int = PERIOD,
) -> dict[str, Any]:
    """
    Fit on a growing window, predict the next ``horizon``, roll forward, repeat.

    This is the only honest way to compare forecasting methods: every prediction
    scored here was made from data that stopped before the month it predicts.
    Refitting on everything and reporting the fit is how a method that memorises
    history wins a comparison it should lose.
    """
    series = _clean(values)
    function = METHODS.get(method)
    if function is None or len(series) < min_train + 1:
        return {"method": method, "folds": 0, "wape": float("nan"),
                "mape": float("nan"), "bias": float("nan"), "residuals": [],
                "usable": False,
                "reason": f"{len(series)} periods; need {min_train + 1} to backtest"}

    actuals: list[float] = []
    predictions: list[float] = []
    residuals_by_step: dict[int, list[float]] = {}
    folds = 0
    for cut in range(min_train, len(series)):
        steps = min(horizon, len(series) - cut)
        forecasts = function(series[:cut], steps, period)
        for step in range(steps):
            actual = series[cut + step]
            predicted = forecasts[step]
            actuals.append(actual)
            predictions.append(predicted)
            residuals_by_step.setdefault(step + 1, []).append(predicted - actual)
        folds += 1

    return {
        "method": method,
        "folds": folds,
        "observations": len(actuals),
        "wape": wape(actuals, predictions),
        "mape": mape(actuals, predictions),
        "bias": bias(actuals, predictions),
        "residuals_by_step": residuals_by_step,
        "usable": True,
        "reason": "ok",
    }


def compare_methods(
    values: Sequence[Any],
    *,
    horizon: int = DEFAULT_HORIZON,
    min_train: int = MIN_TRAIN,
    period: int = PERIOD,
) -> list[dict[str, Any]]:
    """Every method, backtested identically, ranked by out-of-sample WAPE."""
    results = [
        backtest(values, name, horizon=horizon, min_train=min_train, period=period)
        for name in METHODS
    ]
    usable = [r for r in results if r["usable"] and not math.isnan(r["wape"])]
    usable.sort(key=lambda r: r["wape"])
    for rank, result in enumerate(usable, start=1):
        result["rank"] = rank
        result["is_best"] = rank == 1
    for result in results:
        result.setdefault("rank", 0)
        result.setdefault("is_best", False)
    return sorted(results, key=lambda r: (r["rank"] == 0, r["rank"]))


def _interval(residuals: Sequence[float], level: float = 0.80) -> tuple[float, float]:
    """
    Empirical prediction interval from the errors the method actually made.

    Not a normal interval around a fitted model: the spread of realised
    out-of-sample errors at this horizon is checkable, and an assumption about
    the error distribution is not.
    """
    if len(residuals) < 4:
        return (float("nan"), float("nan"))
    ordered = sorted(residuals)
    tail = (1 - level) / 2

    def quantile(q: float) -> float:
        position = q * (len(ordered) - 1)
        low = int(math.floor(position))
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return (quantile(tail), quantile(1 - tail))


def forecast(
    values: Sequence[Any],
    *,
    horizon: int = DEFAULT_HORIZON,
    method: str | None = None,
    period: int = PERIOD,
    min_train: int = MIN_TRAIN,
    level: float = 0.80,
) -> dict[str, Any]:
    """
    Forecast forward, with the method chosen by backtest unless one is named.

    Each point carries a low and a high from the backtest residuals *at that
    horizon* -- the interval widens with distance because the errors did, not
    because a formula says it should.
    """
    series = _clean(values)
    comparison = compare_methods(series, horizon=horizon, min_train=min_train, period=period)
    best = next((r for r in comparison if r.get("is_best")), None)
    chosen = method or (best["method"] if best else "Seasonal naive")
    detail = next((r for r in comparison if r["method"] == chosen), None)

    function = METHODS.get(chosen, seasonal_naive)
    points = function(series, horizon, period)

    residuals = (detail or {}).get("residuals_by_step", {})
    out = []
    for step, value in enumerate(points, start=1):
        low, high = _interval(residuals.get(step, []), level)
        out.append(
            {
                "step": step,
                "forecast": value,
                "low": value - high if not math.isnan(high) else float("nan"),
                "high": value - low if not math.isnan(low) else float("nan"),
            }
        )
    return {
        "method": chosen,
        "chosen_by": "backtest" if method is None else "caller",
        "horizon": horizon,
        "points": out,
        "accuracy": detail or {},
        "comparison": comparison,
        "history_periods": len(series),
    }


def accuracy_against_actual(
    forecasts: Sequence[Any], actuals: Sequence[Any]
) -> dict[str, float]:
    """Score a forecast that has since met its months. Used by the app's
    forecast-versus-actual view, which is the only score that counts."""
    predicted = _clean(forecasts)
    actual = _clean(actuals)
    n = min(len(predicted), len(actual))
    if n == 0:
        return {"periods": 0, "wape": float("nan"), "bias": float("nan"),
                "mean_actual": 0.0, "mean_forecast": 0.0}
    predicted, actual = predicted[:n], actual[:n]
    return {
        "periods": n,
        "wape": wape(actual, predicted),
        "mape": mape(actual, predicted),
        "bias": bias(actual, predicted),
        "mean_actual": statistics.fmean(actual),
        "mean_forecast": statistics.fmean(predicted),
    }
