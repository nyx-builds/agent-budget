"""Statistical forecasting & scenario engine for agent-budget.

Replaces the naive 6-period moving average with real time-series methods:

- **Moving average** — flat mean of the last N periods (the v0.4 baseline).
- **Holt's linear trend** — level + trend with damping, projects growth or
  decline into future periods.
- **Holt-Winters (additive seasonality)** — level + trend + seasonal
  coefficients; captures repeating patterns.
- **Auto** — walk-forward backtests every candidate method on the observed
  history and selects the one with the lowest MAPE.

All algorithms are pure functions over a ``list[float]`` history, so they
are trivially unit-testable. Prediction intervals are derived from
backtested residuals and widen with the square root of the horizon,
mirroring standard practice for local trend models.

The scenario engine layers "what-if" adjustments on top of a baseline
forecast: scale spending up/down, add one-off spikes, override with
absolute per-period amounts, and see projected budget breaches before
they happen.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, field_validator

from .models import BudgetPeriod


# ══════════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════════


class ForecastMethod(str, Enum):
    """Available forecasting methods."""

    MOVING_AVERAGE = "moving_average"
    LINEAR_TREND = "linear_trend"
    HOLT_WINTERS = "holt_winters"
    AUTO = "auto"


class ForecastPoint(BaseModel):
    """A single predicted future value with a prediction interval."""

    period_index: int = Field(ge=1, description="1 = next period")
    period_label: str = Field(description="Human-readable period label")
    predicted: float = Field(ge=0, description="Point forecast")
    lower_bound: float = Field(ge=0, description="Lower bound of interval")
    upper_bound: float = Field(ge=0, description="Upper bound of interval")


class BudgetForecast(BaseModel):
    """Full forecast result for one budget."""

    budget_id: str
    budget_name: str = ""
    method: ForecastMethod = ForecastMethod.AUTO
    method_selected: Optional[ForecastMethod] = Field(
        default=None,
        description="Method actually chosen when method=AUTO",
    )
    points: list[ForecastPoint] = Field(default_factory=list)
    history_points: int = 0
    history_used: list[float] = Field(default_factory=list)
    total_predicted: float = 0.0
    mean_absolute_error: Optional[float] = Field(
        default=None, description="Backtested MAE of the selected method"
    )
    mape: Optional[float] = Field(
        default=None, description="Backtested MAPE (%) of the selected method, if computable"
    )
    confidence: float = Field(ge=0, le=1, default=0.5, description="Heuristic confidence 0..1")
    interval_confidence: float = Field(
        ge=0.5, le=0.999, default=0.8, description="Nominal coverage of prediction intervals"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BacktestResult(BaseModel):
    """Walk-forward validation accuracy for one method."""

    method: ForecastMethod
    tested_points: int = 0
    mape: Optional[float] = Field(
        default=None, description="Mean absolute % error (None if all actuals ~0)"
    )
    rmse: Optional[float] = None
    mae: Optional[float] = None


class ScenarioAdjustment(BaseModel):
    """One adjustment applied by a scenario.

    ``percent_change`` scales the baseline (e.g. -20 = cut 20%).
    ``absolute_per_period`` overrides the baseline amount per period.
    ``one_off_amount`` adds a single spike in period ``one_off_period``
    (1 = next period).
    """

    target_type: str = Field(default="all", pattern="^(all|category|budget)$")
    target_id: Optional[str] = Field(default=None, description="Category or budget id/name")
    percent_change: Optional[float] = Field(
        default=None, description="Scale applied to baseline (e.g. -20 = -20%)"
    )
    absolute_per_period: Optional[float] = Field(
        default=None, description="Override baseline with this amount per period"
    )
    one_off_amount: Optional[float] = Field(default=None, description="Single spike amount")
    one_off_period: int = Field(default=1, ge=1, description="Period index of the spike (1=next)")
    description: str = ""


class Scenario(BaseModel):
    """A named what-if scenario: adjustments over a forecast horizon."""

    id: str = Field(default_factory=lambda: f"SCN-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1)
    description: str = ""
    adjustments: list[ScenarioAdjustment] = Field(default_factory=list)
    horizon_periods: int = Field(default=6, ge=1, le=36)
    method: ForecastMethod = ForecastMethod.AUTO
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("adjustments", mode="before")
    @classmethod
    def _coerce(cls, v):
        return v or []


class ProjectedBreach(BaseModel):
    """A forecasted budget overrun in a specific future period."""

    budget_id: str
    budget_name: str = ""
    period_index: int
    period_label: str
    projected_spend: float
    limit: float
    overrun: float
    overrun_percent: float


class ScenarioResult(BaseModel):
    """Outcome of running a scenario against baseline forecasts."""

    scenario_id: str
    scenario_name: str
    baseline_total: float
    scenario_total: float
    delta: float
    delta_percent: float
    per_period: list[dict[str, Any]] = Field(default_factory=list)
    projected_breaches: list[ProjectedBreach] = Field(default_factory=list)
    ran_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunwayAnalysis(BaseModel):
    """How long current funds last at the observed burn rate."""

    currency: str = "USD"
    balance: float = Field(description="Income received - expenses paid (all time)")
    monthly_burn: float = Field(description="Average net outflow per month (positive = burning)")
    burn_trend_per_month: float = Field(
        description="Change in monthly burn per month (from linear trend)"
    )
    runway_months: Optional[float] = Field(
        default=None, description="Months until balance hits zero (None = never at current rate)"
    )
    exhaustion_date: Optional[date] = Field(default=None, description="Projected zero-balance date")
    profitable: bool = Field(description="True if net cash flow is positive")
    monthly_income: float = 0.0
    monthly_expenses: float = 0.0
    projection: list[dict[str, Any]] = Field(default_factory=list)
    method_note: str = ""


# ══════════════════════════════════════════════════════════════════════
# Forecast algorithms (pure functions)
# ══════════════════════════════════════════════════════════════════════


def moving_average_forecast(
    history: list[float],
    horizon: int,
    window: Optional[int] = None,
) -> list[float]:
    """Flat forecast equal to the mean of the last ``window`` observations."""
    if not history:
        return [0.0] * horizon
    w = min(window or len(history), len(history))
    level = sum(history[-w:]) / w
    return [max(0.0, level)] * horizon


def holt_linear_forecast(
    history: list[float],
    horizon: int,
    alpha: float = 0.5,
    beta: float = 0.25,
    damping: float = 0.98,
) -> list[float]:
    """Holt's linear trend method with damping.

    Damping < 1 shrinks the trend contribution over the horizon, which
    prevents absurd extrapolations on short or noisy series.
    """
    if not history:
        return [0.0] * horizon
    if len(history) == 1:
        return [max(0.0, history[0])] * horizon

    level = history[0]
    trend = history[1] - history[0]
    for obs in history[1:]:
        prev_level = level
        level = alpha * obs + (1 - alpha) * (level + damping * trend)
        trend = beta * (level - prev_level) + (1 - beta) * damping * trend

    out: list[float] = []
    cum_phi = 0.0
    for h in range(1, horizon + 1):
        cum_phi += damping ** h
        out.append(max(0.0, level + cum_phi * trend))
    return out


def holt_winters_forecast(
    history: list[float],
    horizon: int,
    season_length: int = 4,
    alpha: float = 0.4,
    beta: float = 0.1,
    gamma: float = 0.3,
) -> list[float]:
    """Additive Holt-Winters with a fixed seasonal period.

    Requires at least two full seasons of history; falls back to Holt's
    linear method otherwise.
    """
    if len(history) < 2 * season_length:
        return holt_linear_forecast(history, horizon)

    # Seasonal indices: average deviation from overall mean per season slot.
    overall = sum(history) / len(history)
    counts = [0] * season_length
    seasonals = [0.0] * season_length
    for i, obs in enumerate(history):
        seasonals[i % season_length] += obs
        counts[i % season_length] += 1
    seasonals = [s / max(1, c) - overall for s, c in zip(seasonals, counts)]

    level = overall
    trend = (sum(history[-season_length:]) - sum(history[:season_length])) / (season_length ** 2)

    for i, obs in enumerate(history):
        s = seasonals[i % season_length]
        prev_level = level
        level = alpha * (obs - s) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonals[i % season_length] = gamma * (obs - level) + (1 - gamma) * s

    out: list[float] = []
    n = len(history)
    for h in range(1, horizon + 1):
        s = seasonals[(n + h - 1) % season_length]
        out.append(max(0.0, level + h * trend + s))
    return out


_METHOD_FN: dict[ForecastMethod, Callable[[list[float], int], list[float]]] = {
    ForecastMethod.MOVING_AVERAGE: lambda hist, h: moving_average_forecast(hist, h),
    ForecastMethod.LINEAR_TREND: lambda hist, h: holt_linear_forecast(hist, h),
    ForecastMethod.HOLT_WINTERS: lambda hist, h: holt_winters_forecast(hist, h),
}


def _method_forecast(method: ForecastMethod, history: list[float], horizon: int) -> list[float]:
    fn = _METHOD_FN.get(method)
    if fn is None:
        raise ValueError(f"Unknown method: {method}")
    return fn(history, horizon)


# ══════════════════════════════════════════════════════════════════════
# Backtesting
# ══════════════════════════════════════════════════════════════════════


def backtest(
    history: list[float],
    method: ForecastMethod,
    holdout: int = 3,
) -> BacktestResult:
    """Walk-forward validation: fit on history[:-holdout], predict holdout."""
    if method == ForecastMethod.AUTO:
        raise ValueError("backtest() needs a concrete method, not AUTO")
    if holdout < 1 or len(history) <= holdout:
        return BacktestResult(method=method, tested_points=0)

    train = history[:-holdout]
    actual = history[-holdout:]
    preds = _method_forecast(method, train, holdout)

    errs = [a - p for a, p in zip(actual, preds)]
    abs_errs = [abs(e) for e in errs]
    mae = sum(abs_errs) / len(abs_errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    pct = [abs(e) / a * 100 for e, a in zip(abs_errs, actual) if a > 1e-9]
    mape = (sum(pct) / len(pct)) if pct else None
    return BacktestResult(
        method=method,
        tested_points=len(actual),
        mape=round(mape, 2) if mape is not None else None,
        rmse=round(rmse, 4),
        mae=round(mae, 4),
    )


def select_best_method(history: list[float], holdout: int = 3) -> tuple[ForecastMethod, BacktestResult]:
    """Backtest every concrete method, return (best, its result).

    Ranking: lowest MAPE; MAPE=None (degenerate actuals) ranks by MAE.
    Falls back to moving_average when history is too short to backtest.
    """
    if len(history) <= holdout + 1:
        return ForecastMethod.MOVING_AVERAGE, BacktestResult(
            method=ForecastMethod.MOVING_AVERAGE, tested_points=0
        )

    results = [
        (m, backtest(history, m, holdout))
        for m in (ForecastMethod.MOVING_AVERAGE, ForecastMethod.LINEAR_TREND, ForecastMethod.HOLT_WINTERS)
    ]

    def rank(r: BacktestResult) -> tuple[float, float]:
        if r.tested_points == 0:
            return (float("inf"), float("inf"))
        mape = r.mape if r.mape is not None else float("inf")
        mae = r.mae if r.mae is not None else float("inf")
        return (mape, mae)

    best_method, best_result = min(results, key=lambda mr: rank(mr[1]))
    return best_method, best_result


# ══════════════════════════════════════════════════════════════════════
# Prediction intervals
# ══════════════════════════════════════════════════════════════════════

_Z_SCORES = {0.50: 0.674, 0.60: 0.842, 0.70: 1.036, 0.80: 1.282, 0.90: 1.645, 0.95: 1.960, 0.99: 2.326}


def z_score(confidence: float) -> float:
    """Normal quantile for common confidence levels (linear interpolation)."""
    if confidence in _Z_SCORES:
        return _Z_SCORES[confidence]
    keys = sorted(_Z_SCORES)
    if confidence <= keys[0]:
        return _Z_SCORES[keys[0]]
    if confidence >= keys[-1]:
        return _Z_SCORES[keys[-1]]
    for a, b in zip(keys, keys[1:]):
        if a <= confidence <= b:
            t = (confidence - a) / (b - a)
            return _Z_SCORES[a] + t * (_Z_SCORES[b] - _Z_SCORES[a])
    return 1.282


def residual_sigma(history: list[float], method: ForecastMethod, holdout: int = 3) -> float:
    """Stddev of backtest residuals; 0 when backtesting is impossible."""
    if len(history) <= holdout + 1:
        return 0.0
    train = history[:-holdout]
    actual = history[-holdout:]
    preds = _method_forecast(method, train, holdout)
    errs = [a - p for a, p in zip(actual, preds)]
    if not errs:
        return 0.0
    mean = sum(errs) / len(errs)
    return math.sqrt(sum((e - mean) ** 2 for e in errs) / max(1, len(errs) - 1))


# ══════════════════════════════════════════════════════════════════════
# Period helpers
# ══════════════════════════════════════════════════════════════════════


def advance_period(d: date, period: BudgetPeriod, steps: int = 1) -> date:
    """Advance a date by ``steps`` budget periods."""
    for _ in range(steps):
        if period == BudgetPeriod.DAILY:
            d = d + timedelta(days=1)
        elif period == BudgetPeriod.WEEKLY:
            d = d + timedelta(weeks=1)
        elif period == BudgetPeriod.MONTHLY:
            m = d.month - 1 + 1
            d = date(d.year + m // 12, m % 12 + 1, 1)
        elif period == BudgetPeriod.QUARTERLY:
            m = d.month - 1 + 3
            d = date(d.year + m // 12, m % 12 + 1, 1)
        elif period == BudgetPeriod.YEARLY:
            d = date(d.year + 1, 1, 1)
        else:
            d = d + timedelta(days=30)
    return d


def retreat_period(d: date, period: BudgetPeriod, steps: int = 1) -> date:
    """Step a period-start date back by ``steps`` periods."""
    for _ in range(steps):
        if period == BudgetPeriod.DAILY:
            d = d - timedelta(days=1)
        elif period == BudgetPeriod.WEEKLY:
            d = d - timedelta(weeks=1)
        elif period == BudgetPeriod.MONTHLY:
            m = d.month - 2
            d = date(d.year + m // 12, m % 12 + 1, 1)
        elif period == BudgetPeriod.QUARTERLY:
            m = d.month - 4
            d = date(d.year + m // 12, m % 12 + 1, 1)
        elif period == BudgetPeriod.YEARLY:
            d = date(d.year - 1, 1, 1)
        else:
            d = d - timedelta(days=30)
    return d


def period_start_for(period: BudgetPeriod, ref: date) -> date:
    """Start date of the period containing ``ref``."""
    if period == BudgetPeriod.DAILY:
        return ref
    if period == BudgetPeriod.WEEKLY:
        return ref - timedelta(days=ref.weekday())
    if period == BudgetPeriod.MONTHLY:
        return ref.replace(day=1)
    if period == BudgetPeriod.QUARTERLY:
        qm = ((ref.month - 1) // 3) * 3 + 1
        return ref.replace(month=qm, day=1)
    if period == BudgetPeriod.YEARLY:
        return ref.replace(month=1, day=1)
    return ref


def period_label(d: date, period: BudgetPeriod) -> str:
    """Human-readable label for a period start date."""
    if period == BudgetPeriod.WEEKLY:
        return f"Week of {d.isoformat()}"
    if period in (BudgetPeriod.MONTHLY, BudgetPeriod.QUARTERLY):
        return d.strftime("%B %Y")
    if period == BudgetPeriod.YEARLY:
        return str(d.year)
    return d.isoformat()


def build_history(
    spend_fn: Callable[[date, date], float],
    period: BudgetPeriod,
    periods: int,
    ref_date: Optional[date] = None,
) -> list[float]:
    """Collect chronological per-period spend, oldest → newest.

    ``spend_fn(period_start, period_end) -> float`` is called for each of
    the trailing ``periods`` periods ending with the current one.
    """
    ref = ref_date or date.today()
    current_start = period_start_for(period, ref)
    starts = [retreat_period(current_start, period, i) for i in range(periods - 1, -1, -1)]

    hist: list[float] = []
    for s in starts:
        e = advance_period(s, period) - timedelta(days=1)
        try:
            hist.append(round(spend_fn(s, e), 4))
        except Exception:
            hist.append(0.0)
    return hist


# ══════════════════════════════════════════════════════════════════════
# Scenario math
# ══════════════════════════════════════════════════════════════════════


def apply_adjustments(
    baseline: list[float],
    adjustments: list[ScenarioAdjustment],
) -> list[float]:
    """Apply scenario adjustments to a baseline forecast series.

    ``percent_change`` and ``absolute_per_period`` apply to every period;
    ``one_off_amount`` adds a spike to a single period.
    """
    out = list(baseline)
    for adj in adjustments:
        for i in range(len(out)):
            if adj.absolute_per_period is not None:
                out[i] = adj.absolute_per_period
            if adj.percent_change is not None:
                out[i] = out[i] * (1 + adj.percent_change / 100)
            out[i] = max(0.0, out[i])  # spend can be scaled down but never negative
        if adj.one_off_amount is not None and 1 <= adj.one_off_period <= len(out):
            idx = adj.one_off_period - 1
            out[idx] = max(0.0, out[idx] + adj.one_off_amount)
    return [round(v, 4) for v in out]
