"""Tests for agent-budget models."""

import pytest
from datetime import date, datetime

from agent_budget.models import (
    Budget, CostEntry, BudgetAlertRule, BudgetPeriod, BudgetStatus,
    CostCategory, AlertSeverity, AlertAction, BudgetSummary, SpendingProjection,
)


class TestBudgetAlertRule:
    def test_create_rule(self):
        rule = BudgetAlertRule(threshold_pct=80, severity=AlertSeverity.WARNING, action=AlertAction.NOTIFY)
        assert rule.threshold_pct == 80
        assert rule.severity == AlertSeverity.WARNING
        assert rule.action == AlertAction.NOTIFY
        assert rule.cooldown_minutes == 60

    def test_invalid_threshold(self):
        with pytest.raises(ValueError):
            BudgetAlertRule(threshold_pct=150)

    def test_negative_threshold(self):
        with pytest.raises(ValueError):
            BudgetAlertRule(threshold_pct=-10)


class TestBudget:
    def test_create_budget(self):
        budget = Budget(name="Test", limit=1000.0)
        assert budget.name == "Test"
        assert budget.limit == 1000.0
        assert budget.spent == 0.0
        assert budget.remaining == 1000.0
        assert budget.utilization_pct == 0.0
        assert budget.status == BudgetStatus.ACTIVE
        assert budget.period == BudgetPeriod.MONTHLY

    def test_remaining(self):
        budget = Budget(name="Test", limit=100.0, spent=30.0)
        assert budget.remaining == 70.0

    def test_remaining_cannot_go_negative(self):
        budget = Budget(name="Test", limit=100.0, spent=150.0)
        assert budget.remaining == 0.0

    def test_utilization_pct(self):
        budget = Budget(name="Test", limit=200.0, spent=50.0)
        assert budget.utilization_pct == 25.0

    def test_is_exceeded(self):
        budget = Budget(name="Test", limit=100.0, spent=100.0)
        assert not budget.is_exceeded
        budget.spent = 101.0
        assert budget.is_exceeded

    def test_period_end_monthly(self):
        budget = Budget(name="Test", limit=100.0, period=BudgetPeriod.MONTHLY, start_date=date(2026, 1, 15))
        end = budget.period_end
        assert end == date(2026, 2, 15)

    def test_period_end_daily(self):
        budget = Budget(name="Test", limit=100.0, period=BudgetPeriod.DAILY, start_date=date(2026, 3, 1))
        assert budget.period_end == date(2026, 3, 1)

    def test_period_end_weekly(self):
        budget = Budget(name="Test", limit=100.0, period=BudgetPeriod.WEEKLY, start_date=date(2026, 3, 1))
        assert budget.period_end == date(2026, 3, 7)

    def test_period_end_yearly(self):
        budget = Budget(name="Test", limit=100.0, period=BudgetPeriod.YEARLY, start_date=date(2026, 6, 1))
        assert budget.period_end == date(2027, 6, 1)

    def test_period_end_one_time(self):
        budget = Budget(name="Test", limit=100.0, period=BudgetPeriod.ONE_TIME)
        assert budget.period_end is None

    def test_check_alerts_no_rules(self):
        budget = Budget(name="Test", limit=100.0, spent=90.0, alert_rules=[])
        assert budget.check_alerts() == []

    def test_check_alerts_triggered(self):
        budget = Budget(
            name="Test", limit=100.0, spent=85.0,
            alert_rules=[
                BudgetAlertRule(threshold_pct=50, severity=AlertSeverity.INFO),
                BudgetAlertRule(threshold_pct=80, severity=AlertSeverity.WARNING),
                BudgetAlertRule(threshold_pct=100, severity=AlertSeverity.CRITICAL),
            ],
        )
        alerts = budget.check_alerts()
        assert len(alerts) == 2  # 50% and 80% triggered
        assert alerts[0]["threshold_pct"] == 50
        assert alerts[1]["threshold_pct"] == 80

    def test_check_alerts_all_triggered(self):
        budget = Budget(
            name="Test", limit=100.0, spent=100.0,
            alert_rules=[
                BudgetAlertRule(threshold_pct=50),
                BudgetAlertRule(threshold_pct=80),
                BudgetAlertRule(threshold_pct=100),
            ],
        )
        alerts = budget.check_alerts()
        assert len(alerts) == 3

    def test_reset_period(self):
        budget = Budget(name="Test", limit=100.0, spent=80.0)
        budget.reset_period()
        assert budget.spent == 0.0

    def test_default_id_generated(self):
        b1 = Budget(name="A", limit=100.0)
        b2 = Budget(name="B", limit=100.0)
        assert b1.id != b2.id
        assert len(b1.id) == 12


class TestCostEntry:
    def test_create_entry(self):
        entry = CostEntry(budget_id="abc", amount=25.50, category=CostCategory.API_CALLS, source="openai")
        assert entry.budget_id == "abc"
        assert entry.amount == 25.50
        assert entry.category == CostCategory.API_CALLS
        assert entry.source == "openai"

    def test_amount_must_be_positive(self):
        with pytest.raises(ValueError):
            CostEntry(budget_id="abc", amount=0)
        with pytest.raises(ValueError):
            CostEntry(budget_id="abc", amount=-10)

    def test_metadata(self):
        entry = CostEntry(budget_id="abc", amount=5.0, metadata={"model": "gpt-4", "tokens": 1500})
        assert entry.metadata["model"] == "gpt-4"
        assert entry.metadata["tokens"] == 1500


class TestBudgetSummary:
    def test_create_summary(self):
        summary = BudgetSummary(
            budget_id="abc",
            name="Test",
            category="misc",
            limit=1000.0,
            spent=250.0,
            remaining=750.0,
            utilization_pct=25.0,
            status="active",
            period="monthly",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 2, 1),
            currency="USD",
            active_alerts=1,
        )
        assert summary.budget_id == "abc"
        assert summary.utilization_pct == 25.0


class TestSpendingProjection:
    def test_create_projection(self):
        proj = SpendingProjection(
            budget_id="abc",
            budget_name="Test",
            total_budget=1000.0,
            spent_so_far=250.0,
            days_elapsed=10,
            total_days=30,
            daily_burn_rate=25.0,
            projected_total=750.0,
            projected_overage=0.0,
            projected_remaining=250.0,
            on_track=True,
        )
        assert proj.on_track is True
        assert proj.daily_burn_rate == 25.0
