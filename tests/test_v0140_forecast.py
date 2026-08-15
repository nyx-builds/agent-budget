"""Tests for v0.14.0 Statistical Forecasting & Scenario Engine.

Covers:
- Pure forecast algorithms (moving average, Holt linear, Holt-Winters)
- Walk-forward backtesting & auto method selection
- Prediction intervals (z-scores, residual sigma, sqrt(horizon) widening)
- Period helpers (advance/retreat/labels/history collection)
- Scenario adjustment math
- Service layer: forecast, breaches, backtest, scenarios, runway
- Store persistence for scenarios
- MCP tools
- REST API endpoints
- CLI commands
"""

import json
import math
import shutil
import tempfile
from datetime import date

import pytest

from agent_budget.forecast import (
    ForecastMethod,
    ForecastPoint,
    BudgetForecast,
    BacktestResult,
    Scenario,
    ScenarioAdjustment,
    ScenarioResult,
    ProjectedBreach,
    RunwayAnalysis,
    moving_average_forecast,
    holt_linear_forecast,
    holt_winters_forecast,
    backtest,
    select_best_method,
    z_score,
    residual_sigma,
    advance_period,
    retreat_period,
    period_start_for,
    period_label,
    build_history,
    apply_adjustments,
)
from agent_budget.service import BudgetService
from agent_budget.store import BudgetStore
from agent_budget.models import Budget, BudgetPeriod, Expense, Income, IncomeStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    tmpdir = tempfile.mkdtemp()
    store = BudgetStore(data_dir=tmpdir)
    service = BudgetService(store=store)
    yield service
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def growing_service(svc):
    """Monthly 'llm' budget with 8 months of growing spend + income."""
    b = svc.create_budget(name="LLM API", limit=600, period=BudgetPeriod.MONTHLY, category="llm")
    today = date.today()
    for i in range(8, 0, -1):
        m = today.month - 1 - (i - 1)
        y = today.year + m // 12
        mm = m % 12 + 1
        svc.add_expense(amount=200 + (8 - i) * 50, category="llm", expense_date=date(y, mm, 5), budget_id=b.id)
    for i in range(4, 0, -1):
        m = today.month - 1 - (i - 1)
        y = today.year + m // 12
        mm = m % 12 + 1
        svc.add_income(amount=1000, source="client", income_date=date(y, mm, 3))
    svc._test_budget_id = b.id  # type: ignore[attr-defined]
    return svc


# ---------------------------------------------------------------------------
# Pure algorithms
# ---------------------------------------------------------------------------

class TestMovingAverage:
    def test_flat_series(self):
        assert moving_average_forecast([100, 100, 100], 3) == [100.0, 100.0, 100.0]

    def test_empty_history(self):
        assert moving_average_forecast([], 3) == [0.0, 0.0, 0.0]

    def test_window_truncation(self):
        # window=2 uses only the last two observations: (40+60)/2 = 50
        assert moving_average_forecast([10, 20, 40, 60], 2, window=2) == [50.0, 50.0]

    def test_never_negative(self):
        assert all(v >= 0 for v in moving_average_forecast([5, 3, 0, 0, 0], 4))


class TestHoltLinear:
    def test_growing_series_projects_growth(self):
        preds = holt_linear_forecast([100, 120, 140, 160, 180, 200], 4)
        assert preds[0] >= 180
        assert preds[3] > preds[0]

    def test_empty(self):
        assert holt_linear_forecast([], 2) == [0.0, 0.0]

    def test_single_observation(self):
        assert holt_linear_forecast([42], 3) == [42.0, 42.0, 42.0]

    def test_damping_prevents_explosion(self):
        preds = holt_linear_forecast([1, 2, 3, 100000], 12, damping=0.5)
        assert preds[-1] < 1e7  # damped, not exponential

    def test_never_negative(self):
        preds = holt_linear_forecast([100, 90, 5, 1, 0], 6)
        assert all(v >= 0 for v in preds)


class TestHoltWinters:
    def test_seasonal_pattern_captured(self):
        # Clear seasonality with period 4: high at index 1 mod 4
        history = [10, 20, 10, 10, 10, 20, 10, 10, 10, 20, 10, 10]
        preds = holt_winters_forecast(history, 4, season_length=4)
        assert preds[1] > preds[0]
        assert preds[1] > preds[2]

    def test_falls_back_when_history_too_short(self):
        out = holt_winters_forecast([5, 10], 3, season_length=4)
        assert out == holt_linear_forecast([5, 10], 3)

    def test_never_negative(self):
        preds = holt_winters_forecast([10, 20, 15, 25, 12, 22, 17, 27], 4)
        assert all(v >= 0 for v in preds)


class TestBacktest:
    def test_perfect_forecast_zero_error(self):
        r = backtest([100, 100, 100, 100, 100, 100], ForecastMethod.MOVING_AVERAGE)
        assert r.tested_points == 3
        assert r.mae == 0.0
        assert r.mape == 0.0

    def test_auto_rejected(self):
        with pytest.raises(ValueError):
            backtest([1, 2, 3], ForecastMethod.AUTO)

    def test_degenerate_actuals_mape_none(self):
        # holdout actuals all zero → MAPE undefined → None
        r = backtest([0, 0, 0, 0, 0, 0], ForecastMethod.MOVING_AVERAGE)
        assert r.mape is None
        assert r.mae == 0.0

    def test_too_short_history(self):
        r = backtest([1, 2], ForecastMethod.LINEAR_TREND, holdout=3)
        assert r.tested_points == 0

    def test_error_metrics_consistent(self):
        r = backtest([10, 12, 14, 16, 18, 20], ForecastMethod.MOVING_AVERAGE)
        assert r.rmse >= r.mae  # RMSE ≥ MAE always
        if r.mape is not None:
            assert r.mape > 0


class TestSelectBestMethod:
    def test_flat_series_prefers_moving_average(self):
        method, result = select_best_method([100, 100, 100, 100, 100, 100, 100, 100])
        assert method == ForecastMethod.MOVING_AVERAGE
        assert result.mape == 0.0

    def test_trending_series_prefers_trend_method(self):
        method, _ = select_best_method([10, 12, 14, 16, 18, 20, 22, 24])
        assert method == ForecastMethod.LINEAR_TREND

    def test_seasonal_series_prefers_holt_winters(self):
        history = [10, 25, 10, 25, 10, 25, 10, 25, 10, 25, 10, 25]
        method, result = select_best_method(history)
        assert method == ForecastMethod.HOLT_WINTERS
        assert result.mape is not None and result.mape < 5

    def test_short_history_falls_back(self):
        method, result = select_best_method([5, 10])
        assert method == ForecastMethod.MOVING_AVERAGE
        assert result.tested_points == 0


class TestIntervals:
    def test_z_score_known_values(self):
        assert z_score(0.50) == pytest.approx(0.674, abs=1e-3)
        assert z_score(0.95) == pytest.approx(1.960, abs=1e-3)
        assert z_score(0.99) == pytest.approx(2.326, abs=1e-3)

    def test_z_score_monotone(self):
        assert z_score(0.6) < z_score(0.7) < z_score(0.8) < z_score(0.9)

    def test_z_score_clamped(self):
        assert z_score(0.01) == z_score(0.50)
        assert z_score(0.9999) == z_score(0.99)

    def test_residual_sigma_perfect_fit(self):
        assert residual_sigma([100, 100, 100, 100, 100, 100], ForecastMethod.MOVING_AVERAGE) == 0.0

    def test_residual_sigma_short_history(self):
        assert residual_sigma([1, 2], ForecastMethod.LINEAR_TREND) == 0.0


class TestPeriodHelpers:
    def test_advance_monthly(self):
        assert advance_period(date(2026, 1, 15), BudgetPeriod.MONTHLY) == date(2026, 2, 1)

    def test_advance_monthly_year_rollover(self):
        assert advance_period(date(2026, 12, 1), BudgetPeriod.MONTHLY) == date(2027, 1, 1)

    def test_advance_daily(self):
        assert advance_period(date(2026, 8, 31), BudgetPeriod.DAILY) == date(2026, 9, 1)

    def test_advance_weekly(self):
        assert advance_period(date(2026, 8, 10), BudgetPeriod.WEEKLY) == date(2026, 8, 17)

    def test_advance_quarterly(self):
        assert advance_period(date(2026, 11, 5), BudgetPeriod.QUARTERLY) == date(2027, 2, 1)

    def test_advance_yearly(self):
        assert advance_period(date(2026, 3, 1), BudgetPeriod.YEARLY) == date(2027, 1, 1)

    def test_retreat_monthly(self):
        assert retreat_period(date(2026, 3, 1), BudgetPeriod.MONTHLY) == date(2026, 2, 1)

    def test_retreat_monthly_year_rollover(self):
        assert retreat_period(date(2026, 2, 1), BudgetPeriod.MONTHLY, 2) == date(2025, 12, 1)

    def test_period_start_monthly(self):
        assert period_start_for(BudgetPeriod.MONTHLY, date(2026, 8, 15)) == date(2026, 8, 1)

    def test_period_start_weekly(self):
        # 2026-08-15 is a Saturday; week starts Monday 2026-08-10
        assert period_start_for(BudgetPeriod.WEEKLY, date(2026, 8, 15)) == date(2026, 8, 10)

    def test_period_start_quarterly(self):
        assert period_start_for(BudgetPeriod.QUARTERLY, date(2026, 8, 15)) == date(2026, 7, 1)

    def test_period_start_yearly(self):
        assert period_start_for(BudgetPeriod.YEARLY, date(2026, 8, 15)) == date(2026, 1, 1)

    def test_period_label_monthly(self):
        assert "August" in period_label(date(2026, 8, 1), BudgetPeriod.MONTHLY)

    def test_period_label_weekly(self):
        assert "Week of" in period_label(date(2026, 8, 10), BudgetPeriod.WEEKLY)

    def test_period_label_yearly(self):
        assert period_label(date(2026, 1, 1), BudgetPeriod.YEARLY) == "2026"

    def test_build_history_collects_correct_periods(self):
        spend_log = {
            date(2026, 6, 1): 100.0,
            date(2026, 7, 1): 200.0,
            date(2026, 8, 1): 300.0,
        }

        def spend_fn(start, end):
            return spend_log.get(start, 0.0)

        hist = build_history(spend_fn, BudgetPeriod.MONTHLY, 3, ref_date=date(2026, 8, 15))
        assert hist == [100.0, 200.0, 300.0]

    def test_build_history_swallows_errors(self):
        hist = build_history(lambda s, e: 1 / 0, BudgetPeriod.MONTHLY, 2, ref_date=date(2026, 8, 15))
        assert hist == [0.0, 0.0]

    def test_advance_then_retreat_identity(self):
        # advance/retreat normalize to period starts, so the identity holds
        # for aligned dates (which is how the engine uses them).
        d = date(2026, 8, 19)
        for period in BudgetPeriod:
            s = period_start_for(period, d)
            assert retreat_period(advance_period(s, period), period) == s


class TestApplyAdjustments:
    def test_percent_change(self):
        out = apply_adjustments([100, 100], [ScenarioAdjustment(percent_change=-20)])
        assert out == [80.0, 80.0]

    def test_absolute_override(self):
        out = apply_adjustments([100, 200, 300], [ScenarioAdjustment(absolute_per_period=50)])
        assert out == [50.0, 50.0, 50.0]

    def test_absolute_then_percent_compose(self):
        out = apply_adjustments([100, 100], [
            ScenarioAdjustment(absolute_per_period=200, percent_change=-50),
        ])
        assert out == [100.0, 100.0]

    def test_one_off_spike(self):
        out = apply_adjustments([100, 100, 100], [
            ScenarioAdjustment(one_off_amount=500, one_off_period=2),
        ])
        assert out[1] == 600.0
        assert out[0] == 100.0

    def test_one_off_out_of_range_ignored(self):
        out = apply_adjustments([100, 100], [ScenarioAdjustment(one_off_amount=500, one_off_period=9)])
        assert out == [100.0, 100.0]

    def test_no_adjustments_passthrough(self):
        assert apply_adjustments([123.456, 78.9], []) == [123.456, 78.9]

    def test_never_negative(self):
        out = apply_adjustments([10, 10], [ScenarioAdjustment(percent_change=-200)])
        assert all(v >= 0 for v in out)


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------

class TestServiceForecast:
    def test_forecast_basic(self, growing_service):
        svc = growing_service
        fc = svc.get_budget_forecast(svc._test_budget_id, horizon_periods=4)
        assert len(fc.points) == 4
        assert fc.history_points == 12
        assert fc.total_predicted > 0
        assert fc.method == ForecastMethod.AUTO
        assert fc.method_selected is not None

    def test_forecast_intervals_ordered_and_widening(self, growing_service):
        svc = growing_service
        fc = svc.get_budget_forecast(svc._test_budget_id, horizon_periods=6)
        for p in fc.points:
            assert 0 <= p.lower_bound <= p.predicted <= p.upper_bound
        # Intervals widen with horizon on a noisy series
        widths = [p.upper_bound - p.lower_bound for p in fc.points]
        assert widths[-1] >= widths[0]

    def test_forecast_growing_series_exceeds_limit(self, growing_service):
        svc = growing_service
        fc = svc.get_budget_forecast(svc._test_budget_id, horizon_periods=4)
        assert fc.points[-1].predicted > 600  # trend detected

    def test_forecast_explicit_method(self, growing_service):
        svc = growing_service
        fc = svc.get_budget_forecast(svc._test_budget_id, horizon_periods=3, method=ForecastMethod.MOVING_AVERAGE)
        assert fc.method == ForecastMethod.MOVING_AVERAGE
        assert fc.method_selected is None
        # MA of a growing series is flat
        assert fc.points[0].predicted == fc.points[1].predicted == fc.points[2].predicted

    def test_forecast_unknown_budget(self, svc):
        with pytest.raises(ValueError):
            svc.get_budget_forecast("BUD-NOPE")

    def test_forecast_invalid_args(self, growing_service):
        svc = growing_service
        with pytest.raises(ValueError):
            svc.get_budget_forecast(svc._test_budget_id, horizon_periods=0)
        with pytest.raises(ValueError):
            svc.get_budget_forecast(svc._test_budget_id, history_periods=1)

    def test_forecast_all_budgets(self, growing_service):
        svc = growing_service
        svc.create_budget(name="Second", limit=100, period=BudgetPeriod.MONTHLY)
        forecasts = svc.forecast_all_budgets(horizon_periods=2)
        assert len(forecasts) == 2
        assert all(f.history_points == 12 for f in forecasts)

    def test_forecast_ignores_current_partial_period(self, svc):
        """A huge expense in the current partial period must not skew history."""
        b = svc.create_budget(name="Partial", limit=1000, period=BudgetPeriod.MONTHLY)
        today = date.today()
        svc.add_expense(amount=99999, category="misc", expense_date=today, budget_id=b.id)
        fc = svc.get_budget_forecast(b.id, horizon_periods=1)
        assert fc.history_used[-1] == 0.0  # current period excluded

    def test_forecast_multi_currency_budget(self, svc):
        b = svc.create_budget(name="EUR budget", limit=500, period=BudgetPeriod.MONTHLY, currency="EUR")
        today = date.today()
        # Place expenses in the 4 COMPLETE months before the current one
        # (i=0 → last complete month = July when today is in August).
        # The current partial month is excluded from forecast history by design.
        for i in range(4):
            m = today.month - 2 - i
            y = today.year + m // 12
            mm = m % 12 + 1
            svc.add_expense(amount=100, category="eu", expense_date=date(y, mm, 5), budget_id=b.id, currency="EUR")
        fc = svc.get_budget_forecast(b.id, horizon_periods=2, history_periods=4)
        assert fc.points[0].predicted == pytest.approx(100, abs=1)

    def test_backtest_methods_returns_all_three(self, growing_service):
        svc = growing_service
        results = svc.backtest_methods(svc._test_budget_id)
        assert [r.method for r in results] == [
            ForecastMethod.MOVING_AVERAGE, ForecastMethod.LINEAR_TREND, ForecastMethod.HOLT_WINTERS,
        ]
        assert all(r.tested_points == 3 for r in results)


class TestServiceBreaches:
    def test_growing_series_breaches(self, growing_service):
        svc = growing_service
        breaches = svc.projected_breaches(horizon_periods=4)
        assert len(breaches) >= 1
        assert all(b.projected_spend > b.limit for b in breaches)
        assert all(b.overrun > 0 for b in breaches)
        assert all(b.overrun_percent > 0 for b in breaches)

    def test_no_breaches_when_flat_under_limit(self, svc):
        b = svc.create_budget(name="Flat", limit=10000, period=BudgetPeriod.MONTHLY)
        today = date.today()
        for i in range(6, 0, -1):
            m = today.month - 1 - (i - 1)
            y = today.year + m // 12
            mm = m % 12 + 1
            svc.add_expense(amount=100, category="flat", expense_date=date(y, mm, 5), budget_id=b.id)
        assert svc.projected_breaches(horizon_periods=4) == []


class TestServiceScenarios:
    def test_percent_cut(self, growing_service):
        svc = growing_service
        scenario = Scenario(
            name="Cut 30%",
            adjustments=[ScenarioAdjustment(percent_change=-30)],
            horizon_periods=4,
        )
        result = svc.run_scenario(scenario)
        assert result.baseline_total > 0
        assert result.delta_percent == pytest.approx(-30.0, abs=0.5)
        assert len(result.per_period) == 4
        assert all(row["delta"] < 0 for row in result.per_period)

    def test_one_off_spike_creates_breach(self, growing_service):
        svc = growing_service
        scenario = Scenario(
            name="Migration spike",
            adjustments=[ScenarioAdjustment(one_off_amount=5000, one_off_period=1)],
            horizon_periods=3,
        )
        result = svc.run_scenario(scenario)
        assert result.projected_breaches  # spike pushes over the 600 limit
        assert result.delta > 0

    def test_category_targeting(self, growing_service):
        svc = growing_service
        svc.create_budget(name="Other", limit=100, period=BudgetPeriod.MONTHLY, category="other")
        scenario = Scenario(
            name="LLM only cut",
            adjustments=[ScenarioAdjustment(target_type="category", target_id="llm", percent_change=-100)],
            horizon_periods=2,
        )
        result = svc.run_scenario(scenario)
        # 'other' budget baseline is 0, cut to 0; llm cut to 0 → scenario total ~0
        assert result.scenario_total < 100

    def test_budget_name_targeting(self, growing_service):
        svc = growing_service
        scenario = Scenario(
            name="By name",
            adjustments=[ScenarioAdjustment(target_type="budget", target_id="LLM API", percent_change=-50)],
            horizon_periods=2,
        )
        result = svc.run_scenario(scenario)
        assert result.delta < 0

    def test_scenario_crud_roundtrip(self, svc):
        created = svc.create_scenario(
            name="Saved cut",
            adjustments=[{"percent_change": -20}],
            horizon_periods=3,
        )
        assert created.id.startswith("SCN-")
        assert len(svc.list_scenarios()) == 1
        fetched = svc.get_scenario(created.id)
        assert fetched is not None and fetched.name == "Saved cut"
        assert len(fetched.adjustments) == 1
        assert svc.delete_scenario(created.id) is True
        assert svc.list_scenarios() == []
        assert svc.delete_scenario(created.id) is False

    def test_run_saved_scenario(self, growing_service):
        svc = growing_service
        created = svc.create_scenario(name="Rerun me", adjustments=[{"percent_change": 10}], horizon_periods=2)
        result = svc.run_saved_scenario(created.id)
        assert result.scenario_name == "Rerun me"
        assert result.delta_percent == pytest.approx(10.0, abs=0.5)

    def test_run_saved_unknown(self, svc):
        with pytest.raises(ValueError):
            svc.run_saved_scenario("SCN-NOPE")

    def test_scenario_survives_reload(self, svc):
        created = svc.create_scenario(name="Persist", adjustments=[{"absolute_per_period": 42}], horizon_periods=5)
        store2 = BudgetStore(data_dir=svc.store.data_dir)
        assert [s.name for s in store2.list_scenarios()] == ["Persist"]


class TestServiceRunway:
    def test_profitable(self, growing_service):
        rw = growing_service.analyze_runway()
        assert rw.profitable is True
        assert rw.monthly_burn < 0
        assert rw.runway_months is None
        assert rw.exhaustion_date is None

    def test_burning_down(self, svc):
        today = date.today()
        # Big income long ago, steady expenses in the 6 complete months.
        m = today.month - 1 - 8
        y = today.year + m // 12
        svc.add_income(amount=10000, source="grant", income_date=date(y, m % 12 + 1, 1))
        for i in range(6):
            m = today.month - 2 - i
            y = today.year + m // 12
            svc.add_expense(amount=500, category="ops", expense_date=date(y, m % 12 + 1, 5))
        rw = svc.analyze_runway()
        assert rw.profitable is False
        # Balance = 10000 income − 3000 spent = 7000; burn = 500/month
        # (6 expenses × 500 over the 6-month window) → 14.0 months of runway.
        assert rw.runway_months == pytest.approx(14.0, abs=0.2)
        assert rw.exhaustion_date is not None

    def test_no_data(self, svc):
        rw = svc.analyze_runway()
        assert rw.balance == 0.0
        assert rw.monthly_burn == 0.0
        assert rw.runway_months is None

    def test_invalid_months(self, svc):
        with pytest.raises(ValueError):
            svc.analyze_runway(months=0)

    def test_cancelled_excluded(self, svc):
        today = date.today()
        svc.add_income(amount=5000, source="x", income_date=today)
        svc.add_expense(amount=1000, category="ops", expense_date=today, status_kwargs=None) if False else None
        e = svc.add_expense(amount=1000, category="ops", expense_date=today)
        from agent_budget.models import ExpenseStatus
        e.status = ExpenseStatus.CANCELLED
        svc.store.save_expense(e)
        rw = svc.analyze_runway()
        assert rw.balance == 5000.0

    def test_projection_months_bounded(self, growing_service):
        rw = growing_service.analyze_runway()
        assert 1 <= len(rw.projection) <= 12


class TestStoreScenarioPersistence:
    def test_save_list_get_delete(self, svc):
        s = Scenario(name="S1", adjustments=[ScenarioAdjustment(percent_change=-10)], horizon_periods=2)
        svc.store.save_scenario(s)
        assert [x.name for x in svc.store.list_scenarios()] == ["S1"]
        assert svc.store.get_scenario(s.id).id == s.id
        assert svc.store.delete_scenario(s.id) is True
        assert svc.store.get_scenario(s.id) is None


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

class TestForecastMCP:
    @pytest.fixture(autouse=True)
    def reset_mcp_service(self, growing_service):
        import agent_budget.mcp_server as m
        m._service = growing_service
        yield
        m._service = None

    def test_forecast_budget_tool(self):
        from agent_budget.mcp_server import forecast_budget
        import agent_budget.mcp_server as m
        svc = m._service
        result = forecast_budget(budget_id=svc._test_budget_id, horizon_periods=3)
        data = json.loads(result)
        assert data["budget_name"] == "LLM API"
        assert len(data["points"]) == 3
        assert data["method_selected"] in ("moving_average", "linear_trend", "holt_winters")

    def test_forecast_all_tool(self):
        from agent_budget.mcp_server import forecast_all_budgets
        result = forecast_all_budgets(horizon_periods=2)
        data = json.loads(result)
        assert isinstance(data, list) and len(data) >= 1

    def test_projected_breaches_tool(self):
        from agent_budget.mcp_server import get_projected_breaches
        result = get_projected_breaches(horizon_periods=4)
        data = json.loads(result)
        assert data["breach_count"] >= 1
        assert data["breaches"][0]["budget_name"] == "LLM API"

    def test_backtest_tool(self):
        from agent_budget.mcp_server import backtest_forecast_methods
        import agent_budget.mcp_server as m
        result = backtest_forecast_methods(budget_id=m._service._test_budget_id)
        data = json.loads(result)
        assert len(data) == 3
        assert {"moving_average", "linear_trend", "holt_winters"} <= {d["method"] for d in data}

    def test_run_what_if_scenario_tool(self):
        from agent_budget.mcp_server import run_what_if_scenario
        result = run_what_if_scenario(
            name="MCP cut",
            adjustments=[{"percent_change": -50}],
            horizon_periods=2,
        )
        data = json.loads(result)
        assert data["delta_percent"] == pytest.approx(-50.0, abs=0.5)
        assert data["saved"] is False

    def test_scenario_save_and_rerun_via_tools(self):
        from agent_budget.mcp_server import (
            run_what_if_scenario, list_scenarios, run_saved_scenario, delete_scenario,
        )
        result = run_what_if_scenario(
            name="Keep me", adjustments=[{"percent_change": -10}], horizon_periods=2, save=True,
        )
        data = json.loads(result)
        assert data["saved"] is True
        sid = data["scenario_id"]

        listed = json.loads(list_scenarios())
        assert any(s["id"] == sid for s in listed)

        rerun = json.loads(run_saved_scenario(scenario_id=sid))
        assert rerun["scenario_name"] == "Keep me"

        deleted = json.loads(delete_scenario(scenario_id=sid))
        assert deleted["deleted"] is True

    def test_analyze_cash_runway_tool(self):
        from agent_budget.mcp_server import analyze_cash_runway
        data = json.loads(analyze_cash_runway(months=4))
        assert data["profitable"] is True
        assert "projection" in data


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

class TestForecastAPI:
    @pytest.fixture
    def client(self, growing_service):
        from fastapi.testclient import TestClient
        import agent_budget.api_server as api_mod
        api_mod._service = growing_service
        app = api_mod.create_app()
        yield TestClient(app)
        api_mod._service = None

    def test_statistical_forecast_single(self, client, growing_service):
        resp = client.get(f"/analytics/statistical-forecast?budget_id={growing_service._test_budget_id}&horizon_periods=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["budget_name"] == "LLM API"
        assert len(data["points"]) == 3

    def test_statistical_forecast_all(self, client):
        resp = client.get("/analytics/statistical-forecast?horizon_periods=2")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_statistical_forecast_bad_method(self, client):
        resp = client.get("/analytics/statistical-forecast?method=neural_net")
        assert resp.status_code == 422

    def test_statistical_forecast_unknown_budget(self, client):
        resp = client.get("/analytics/statistical-forecast?budget_id=BUD-NOPE")
        assert resp.status_code == 404

    def test_projected_breaches(self, client):
        resp = client.get("/analytics/projected-breaches?horizon_periods=4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["breach_count"] >= 1

    def test_forecast_backtest(self, client, growing_service):
        resp = client.get(f"/analytics/forecast-backtest?budget_id={growing_service._test_budget_id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_runway(self, client):
        resp = client.get("/analytics/runway")
        assert resp.status_code == 200
        data = resp.json()
        assert data["profitable"] is True

    def test_scenario_crud_and_run(self, client):
        created = client.post("/scenarios", json={
            "name": "API cut",
            "adjustments": [{"percent_change": -25}],
            "horizon_periods": 3,
        })
        assert created.status_code == 200
        sid = created.json()["id"]
        assert sid.startswith("SCN-")

        listed = client.get("/scenarios")
        assert any(s["id"] == sid for s in listed.json())

        fetched = client.get(f"/scenarios/{sid}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "API cut"

        ran = client.post(f"/scenarios/{sid}/run")
        assert ran.status_code == 200
        assert ran.json()["delta_percent"] == pytest.approx(-25.0, abs=0.5)

        deleted = client.delete(f"/scenarios/{sid}")
        assert deleted.status_code == 200
        assert client.get(f"/scenarios/{sid}").status_code == 404

    def test_adhoc_scenario_run(self, client):
        resp = client.post("/scenarios/run-adhoc", json={
            "name": "One time",
            "adjustments": [{"one_off_amount": 5000, "one_off_period": 1}],
            "horizon_periods": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["delta"] > 0
        assert data["scenario_id"].startswith("SCN-")
        # Nothing persisted
        assert client.get("/scenarios").json() == []

    def test_scenario_unknown_404(self, client):
        assert client.get("/scenarios/SCN-NOPE").status_code == 404
        assert client.post("/scenarios/SCN-NOPE/run").status_code == 404
        assert client.delete("/scenarios/SCN-NOPE").status_code == 404


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestForecastCLI:
    @pytest.fixture
    def runner(self, growing_service, monkeypatch):
        from click.testing import CliRunner
        import agent_budget.cli as cli_mod
        monkeypatch.setattr(cli_mod, "get_service", lambda: growing_service)
        return CliRunner()

    def test_predict_forecast(self, runner, growing_service):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "forecast", growing_service._test_budget_id, "--horizon", "3"])
        assert result.exit_code == 0
        assert "Statistical Forecast" in result.output

    def test_predict_forecast_unknown_budget(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "forecast", "BUD-NOPE"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_predict_breaches(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "breaches", "--horizon", "4"])
        assert result.exit_code == 0
        assert "breach" in result.output.lower()

    def test_predict_backtest(self, runner, growing_service):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "backtest", growing_service._test_budget_id])
        assert result.exit_code == 0
        assert "moving_average" in result.output

    def test_predict_runway(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "runway"])
        assert result.exit_code == 0
        assert "Runway" in result.output

    def test_predict_scenario(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "scenario", "--name", "Cut", "--percent", "-30", "--horizon", "3"])
        assert result.exit_code == 0
        assert "What-If Scenario" in result.output

    def test_predict_scenario_requires_adjustment(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "scenario", "--name", "Empty"])
        assert result.exit_code == 1

    def test_predict_scenario_save_and_list(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "scenario", "--name", "Saved", "--percent", "-10", "--save"])
        assert result.exit_code == 0
        assert "Saved as SCN-" in result.output

        listed = runner.invoke(main, ["predict", "scenarios"])
        assert listed.exit_code == 0
        assert "Saved" in listed.output

    def test_predict_scenarios_empty(self, runner):
        from agent_budget.cli import main
        result = runner.invoke(main, ["predict", "scenarios"])
        assert result.exit_code == 0
        assert "No saved scenarios" in result.output

    def test_predict_run_saved_and_delete(self, runner):
        from agent_budget.cli import main
        saved = runner.invoke(main, ["predict", "scenario", "--name", "RT", "--percent", "-5", "--save"])
        sid = saved.output.split("Saved as ")[1].strip()

        ran = runner.invoke(main, ["predict", "run-saved", sid])
        assert ran.exit_code == 0
        assert "RT" in ran.output

        deleted = runner.invoke(main, ["predict", "delete-scenario", sid])
        assert deleted.exit_code == 0
        assert "Deleted" in deleted.output
