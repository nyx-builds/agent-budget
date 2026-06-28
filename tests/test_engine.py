"""Tests for agent-budget engine (core business logic)."""

import pytest
import tempfile
import os
from datetime import date, datetime, timedelta

from agent_budget.models import (
    BudgetPeriod, BudgetStatus, CostCategory, AlertSeverity, AlertAction,
)
from agent_budget.store import BudgetStore
from agent_budget.engine import BudgetEngine


@pytest.fixture
def store(tmp_path):
    return BudgetStore(data_dir=str(tmp_path / "budget-data"))


@pytest.fixture
def engine(store):
    return BudgetEngine(store=store)


class TestBudgetCRUD:
    def test_create_budget(self, engine):
        budget = engine.create_budget(name="API Costs", limit=500.0, period=BudgetPeriod.MONTHLY, category=CostCategory.API_CALLS)
        assert budget.name == "API Costs"
        assert budget.limit == 500.0
        assert budget.status == BudgetStatus.ACTIVE
        assert len(budget.alert_rules) == 4  # Default alert rules

    def test_create_budget_with_custom_alerts(self, engine):
        budget = engine.create_budget(
            name="Custom", limit=100.0,
            alert_rules=[{"threshold_pct": 90, "severity": "critical", "action": "halt"}],
        )
        # Custom alert_rules should override defaults (but dict not list of BudgetAlertRule, so it'll use defaults)
        # Actually, we pass a list — but the create_budget expects list[BudgetAlertRule]
        # Let's test with proper rules
        from agent_budget.models import BudgetAlertRule
        budget = engine.create_budget(
            name="Custom", limit=100.0,
            alert_rules=[BudgetAlertRule(threshold_pct=90, severity=AlertSeverity.CRITICAL, action=AlertAction.HALT)],
        )
        assert len(budget.alert_rules) == 1

    def test_get_budget(self, engine):
        created = engine.create_budget(name="Test", limit=100.0)
        fetched = engine.get_budget(created.id)
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_budget_not_found(self, engine):
        assert engine.get_budget("nonexistent") is None

    def test_update_budget(self, engine):
        budget = engine.create_budget(name="Old Name", limit=100.0)
        updated = engine.update_budget(budget.id, name="New Name", limit=200.0)
        assert updated.name == "New Name"
        assert updated.limit == 200.0

    def test_update_budget_not_found(self, engine):
        assert engine.update_budget("nonexistent", name="X") is None

    def test_delete_budget(self, engine):
        budget = engine.create_budget(name="Delete Me", limit=100.0)
        assert engine.delete_budget(budget.id) is True
        assert engine.get_budget(budget.id) is None

    def test_delete_budget_not_found(self, engine):
        assert engine.delete_budget("nonexistent") is False

    def test_list_budgets(self, engine):
        engine.create_budget(name="A", limit=100.0)
        engine.create_budget(name="B", limit=200.0)
        budgets = engine.list_budgets()
        assert len(budgets) == 2

    def test_list_budgets_filter_status(self, engine):
        b1 = engine.create_budget(name="Active", limit=100.0)
        b2 = engine.create_budget(name="Paused", limit=100.0)
        engine.pause_budget(b2.id)
        active = engine.list_budgets(status=BudgetStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_list_budgets_filter_category(self, engine):
        engine.create_budget(name="API", limit=100.0, category=CostCategory.API_CALLS)
        engine.create_budget(name="Compute", limit=100.0, category=CostCategory.COMPUTE)
        api_budgets = engine.list_budgets(category=CostCategory.API_CALLS)
        assert len(api_budgets) == 1
        assert api_budgets[0].name == "API"


class TestBudgetStatus:
    def test_pause_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        paused = engine.pause_budget(budget.id)
        assert paused.status == BudgetStatus.PAUSED

    def test_resume_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.pause_budget(budget.id)
        resumed = engine.resume_budget(budget.id)
        assert resumed.status == BudgetStatus.ACTIVE

    def test_resume_exceeded_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=10.0)
        engine.record_cost(budget.id, amount=15.0)
        # Budget should be exceeded
        fetched = engine.get_budget(budget.id)
        assert fetched.status == BudgetStatus.EXCEEDED
        # Resume should set to exceeded (not active)
        engine.pause_budget(budget.id)
        resumed = engine.resume_budget(budget.id)
        assert resumed.status == BudgetStatus.EXCEEDED

    def test_close_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        closed = engine.close_budget(budget.id)
        assert closed.status == BudgetStatus.CLOSED

    def test_reset_budget_period(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=50.0)
        reset = engine.reset_budget_period(budget.id)
        assert reset.spent == 0.0
        assert reset.status == BudgetStatus.ACTIVE


class TestCostTracking:
    def test_record_cost(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        entry, alerts = engine.record_cost(budget.id, amount=25.0, source="openai")
        assert entry.amount == 25.0
        assert entry.budget_id == budget.id

    def test_record_cost_updates_spent(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=30.0)
        engine.record_cost(budget.id, amount=20.0)
        fetched = engine.get_budget(budget.id)
        assert fetched.spent == 50.0

    def test_record_cost_budget_not_found(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.record_cost("nonexistent", amount=10.0)

    def test_record_cost_closed_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.close_budget(budget.id)
        with pytest.raises(ValueError, match="closed"):
            engine.record_cost(budget.id, amount=10.0)

    def test_record_cost_exceeds_budget(self, engine):
        budget = engine.create_budget(name="Test", limit=10.0)
        engine.record_cost(budget.id, amount=15.0)
        fetched = engine.get_budget(budget.id)
        assert fetched.status == BudgetStatus.EXCEEDED
        assert fetched.is_exceeded

    def test_record_cost_triggers_alerts(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        # Default alerts: 50%, 80%, 95%, 100%
        entry, alerts = engine.record_cost(budget.id, amount=55.0)
        assert len(alerts) >= 1  # At least the 50% alert

    def test_get_cost_entries(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=10.0, source="openai")
        engine.record_cost(budget.id, amount=20.0, source="anthropic")
        entries = engine.get_cost_entries(budget.id)
        assert len(entries) == 2

    def test_get_cost_entries_filter_source(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=10.0, source="openai")
        engine.record_cost(budget.id, amount=20.0, source="anthropic")
        entries = engine.get_cost_entries(budget.id, source="openai")
        assert len(entries) == 1
        assert entries[0].source == "openai"

    def test_get_cost_entries_filter_category(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0, category=CostCategory.API_CALLS)
        engine.record_cost(budget.id, amount=10.0, category=CostCategory.API_CALLS)
        engine.record_cost(budget.id, amount=20.0, category=CostCategory.COMPUTE)
        entries = engine.get_cost_entries(budget.id, category=CostCategory.COMPUTE)
        assert len(entries) == 1

    def test_delete_cost_entry(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        entry, _ = engine.record_cost(budget.id, amount=30.0)
        # Budget spent should be 30
        fetched = engine.get_budget(budget.id)
        assert fetched.spent == 30.0
        # Delete the entry
        assert engine.delete_cost_entry(budget.id, entry.id) is True
        # Budget spent should be 0
        fetched = engine.get_budget(budget.id)
        assert fetched.spent == 0.0

    def test_delete_cost_entry_not_found(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        assert engine.delete_cost_entry(budget.id, "nonexistent") is False

    def test_delete_cost_entry_reverts_exceeded(self, engine):
        budget = engine.create_budget(name="Test", limit=10.0)
        entry, _ = engine.record_cost(budget.id, amount=15.0)
        fetched = engine.get_budget(budget.id)
        assert fetched.status == BudgetStatus.EXCEEDED
        # Delete the entry
        engine.delete_cost_entry(budget.id, entry.id)
        fetched = engine.get_budget(budget.id)
        assert fetched.status == BudgetStatus.ACTIVE


class TestAnalytics:
    def test_get_budget_summary(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=25.0)
        summary = engine.get_budget_summary(budget.id)
        assert summary is not None
        assert summary.spent == 25.0
        assert summary.remaining == 75.0
        assert summary.utilization_pct == 25.0

    def test_get_all_summaries(self, engine):
        engine.create_budget(name="A", limit=100.0)
        engine.create_budget(name="B", limit=200.0)
        summaries = engine.get_all_summaries()
        assert len(summaries) == 2

    def test_project_spending(self, engine):
        budget = engine.create_budget(name="Test", limit=300.0, period=BudgetPeriod.MONTHLY)
        # Record some costs
        engine.record_cost(budget.id, amount=50.0)
        proj = engine.project_spending(budget.id)
        assert proj is not None
        assert proj.spent_so_far == 50.0
        assert proj.daily_burn_rate > 0

    def test_project_spending_one_time_no_end(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0, period=BudgetPeriod.ONE_TIME)
        proj = engine.project_spending(budget.id)
        assert proj is None  # Can't project for one_time with no end date

    def test_costs_by_category(self, engine):
        budget = engine.create_budget(name="Test", limit=1000.0)
        engine.record_cost(budget.id, amount=50.0, category=CostCategory.API_CALLS)
        engine.record_cost(budget.id, amount=30.0, category=CostCategory.API_CALLS)
        engine.record_cost(budget.id, amount=20.0, category=CostCategory.COMPUTE)
        by_cat = engine.get_costs_by_category(budget.id)
        assert by_cat["api_calls"] == 80.0
        assert by_cat["compute"] == 20.0

    def test_costs_by_source(self, engine):
        budget = engine.create_budget(name="Test", limit=1000.0)
        engine.record_cost(budget.id, amount=50.0, source="openai")
        engine.record_cost(budget.id, amount=30.0, source="openai")
        engine.record_cost(budget.id, amount=20.0, source="anthropic")
        by_src = engine.get_costs_by_source(budget.id)
        assert by_src["openai"] == 80.0
        assert by_src["anthropic"] == 20.0

    def test_daily_spending(self, engine):
        budget = engine.create_budget(name="Test", limit=1000.0)
        engine.record_cost(budget.id, amount=10.0)
        daily = engine.get_daily_spending(budget.id)
        today = date.today().isoformat()
        assert today in daily
        assert daily[today] == 10.0


class TestAlerts:
    def test_add_alert_rule(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        updated = engine.add_alert_rule(budget.id, threshold_pct=75.0, severity=AlertSeverity.WARNING)
        assert updated is not None
        assert len(updated.alert_rules) == 5  # 4 default + 1 new

    def test_add_alert_rule_not_found(self, engine):
        assert engine.add_alert_rule("nonexistent", threshold_pct=75.0) is None

    def test_remove_alert_rule(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        rule_id = budget.alert_rules[0].id
        updated = engine.remove_alert_rule(budget.id, rule_id)
        assert len(updated.alert_rules) == 3

    def test_check_budget_alerts(self, engine):
        budget = engine.create_budget(name="Test", limit=100.0)
        engine.record_cost(budget.id, amount=55.0)
        alerts = engine.check_budget_alerts(budget.id)
        assert len(alerts) >= 1

    def test_check_all_alerts(self, engine):
        b1 = engine.create_budget(name="A", limit=100.0)
        b2 = engine.create_budget(name="B", limit=100.0)
        engine.record_cost(b1.id, amount=55.0)
        engine.record_cost(b2.id, amount=85.0)
        all_alerts = engine.check_all_alerts()
        assert len(all_alerts) >= 2


class TestHierarchy:
    def test_create_child_budget(self, engine):
        parent = engine.create_budget(name="Parent", limit=1000.0)
        child = engine.create_budget(
            name="Child", limit=200.0,
            parent_budget_id=parent.id,
        )
        assert child.parent_budget_id == parent.id

    def test_get_sub_budgets(self, engine):
        parent = engine.create_budget(name="Parent", limit=1000.0)
        engine.create_budget(name="Child A", limit=200.0, parent_budget_id=parent.id)
        engine.create_budget(name="Child B", limit=300.0, parent_budget_id=parent.id)
        children = engine.get_sub_budgets(parent.id)
        assert len(children) == 2

    def test_get_rollup_summary(self, engine):
        parent = engine.create_budget(name="Parent", limit=1000.0)
        engine.create_budget(name="Child A", limit=200.0, parent_budget_id=parent.id)
        engine.create_budget(name="Child B", limit=300.0, parent_budget_id=parent.id)
        engine.record_cost(parent.id, amount=100.0)
        engine.record_cost(parent.id, amount=50.0)
        rollup = engine.get_rollup_summary(parent.id)
        assert rollup is not None
        assert rollup["total_limit"] == 1500.0  # 1000 + 200 + 300
        assert rollup["child_count"] == 2

    def test_rollup_not_found(self, engine):
        assert engine.get_rollup_summary("nonexistent") is None
