"""Tests for Agent Budget store."""

import json
import tempfile
import pytest
from datetime import date
from pathlib import Path

from agent_budget.models import (
    Budget, BudgetPeriod, Expense, RecurringExpense, BudgetAlert, AlertLevel,
    SavingsGoal, SavingsGoalStatus, SavingsContribution, SpendingRule, SpendingRuleAction,
    BudgetRollover, RecurringFrequency,
)
from agent_budget.store import BudgetStore


@pytest.fixture
def store(tmp_path):
    return BudgetStore(data_dir=str(tmp_path))


class TestBudgetStore:
    def test_save_and_list_budgets(self, store):
        budget = Budget(name="Test", limit=500, period=BudgetPeriod.MONTHLY)
        store.save_budget(budget)
        budgets = store.list_budgets()
        assert len(budgets) == 1
        assert budgets[0].name == "Test"

    def test_get_budget(self, store):
        budget = Budget(name="Test", limit=500, period=BudgetPeriod.MONTHLY)
        store.save_budget(budget)
        found = store.get_budget(budget.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_budget_not_found(self, store):
        assert store.get_budget("BUD-NONEXISTENT") is None

    def test_update_budget(self, store):
        budget = Budget(name="Test", limit=500, period=BudgetPeriod.MONTHLY)
        store.save_budget(budget)
        budget.name = "Updated"
        store.save_budget(budget)
        found = store.get_budget(budget.id)
        assert found.name == "Updated"

    def test_delete_budget(self, store):
        budget = Budget(name="Test", limit=500, period=BudgetPeriod.MONTHLY)
        store.save_budget(budget)
        assert store.delete_budget(budget.id) is True
        assert store.get_budget(budget.id) is None

    def test_delete_budget_not_found(self, store):
        assert store.delete_budget("BUD-NONEXISTENT") is False

    def test_list_budgets_active_only(self, store):
        b1 = Budget(name="Active", limit=100, period=BudgetPeriod.MONTHLY, active=True)
        b2 = Budget(name="Inactive", limit=200, period=BudgetPeriod.MONTHLY, active=False)
        store.save_budget(b1)
        store.save_budget(b2)
        active = store.list_budgets(active_only=True)
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_budgets_persisted_to_file(self, store):
        budget = Budget(name="Test", limit=500, period=BudgetPeriod.MONTHLY)
        store.save_budget(budget)
        data_file = store._budgets_file
        assert data_file.exists()
        with open(data_file) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "Test"


class TestExpenseStore:
    def test_save_and_list_expenses(self, store):
        expense = Expense(amount=50, category="api", description="Test expense")
        store.save_expense(expense)
        expenses = store.list_expenses()
        assert len(expenses) == 1
        assert expenses[0].amount == 50

    def test_filter_by_category(self, store):
        e1 = Expense(amount=50, category="api")
        e2 = Expense(amount=100, category="infra")
        store.save_expense(e1)
        store.save_expense(e2)
        api_expenses = store.list_expenses(category="api")
        assert len(api_expenses) == 1
        assert api_expenses[0].category == "api"

    def test_filter_by_date_range(self, store):
        e1 = Expense(amount=50, category="api", expense_date=date(2026, 6, 1))
        e2 = Expense(amount=100, category="api", expense_date=date(2026, 5, 1))
        store.save_expense(e1)
        store.save_expense(e2)
        june = store.list_expenses(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        assert len(june) == 1

    def test_filter_by_budget_id(self, store):
        e1 = Expense(amount=50, category="api", budget_id="BUD-1")
        e2 = Expense(amount=100, category="api", budget_id="BUD-2")
        store.save_expense(e1)
        store.save_expense(e2)
        filtered = store.list_expenses(budget_id="BUD-1")
        assert len(filtered) == 1

    def test_filter_by_vendor(self, store):
        e1 = Expense(amount=50, category="api", vendor="OpenAI")
        e2 = Expense(amount=100, category="api", vendor="AWS")
        store.save_expense(e1)
        store.save_expense(e2)
        filtered = store.list_expenses(vendor="openai")
        assert len(filtered) == 1
        assert filtered[0].vendor == "OpenAI"

    def test_filter_by_reimbursable(self, store):
        e1 = Expense(amount=50, category="api", reimbursable=True)
        e2 = Expense(amount=100, category="api", reimbursable=False)
        store.save_expense(e1)
        store.save_expense(e2)
        filtered = store.list_expenses(reimbursable=True)
        assert len(filtered) == 1
        assert filtered[0].reimbursable is True

    def test_delete_expense(self, store):
        expense = Expense(amount=50, category="api")
        store.save_expense(expense)
        assert store.delete_expense(expense.id) is True
        assert store.get_expense(expense.id) is None

    def test_delete_expense_not_found(self, store):
        assert store.delete_expense("EXP-NONEXISTENT") is False


class TestRecurringStore:
    def test_save_and_list_recurring(self, store):
        recurring = RecurringExpense(
            name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY
        )
        store.save_recurring_expense(recurring)
        recurrings = store.list_recurring_expenses()
        assert len(recurrings) == 1

    def test_list_active_only(self, store):
        r1 = RecurringExpense(name="Active", amount=50, category="api", frequency=RecurringFrequency.MONTHLY, active=True)
        r2 = RecurringExpense(name="Inactive", amount=50, category="api", frequency=RecurringFrequency.MONTHLY, active=False)
        store.save_recurring_expense(r1)
        store.save_recurring_expense(r2)
        active = store.list_recurring_expenses(active_only=True)
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_delete_recurring(self, store):
        recurring = RecurringExpense(name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY)
        store.save_recurring_expense(recurring)
        assert store.delete_recurring_expense(recurring.id) is True


class TestAlertStore:
    def test_save_and_list_alerts(self, store):
        alert = BudgetAlert(
            budget_id="BUD-1",
            budget_name="Test",
            level=AlertLevel.WARNING,
            percent_spent=75,
            amount_spent=375,
            budget_limit=500,
            remaining=125,
            period=BudgetPeriod.MONTHLY,
        )
        store.save_alert(alert)
        alerts = store.list_alerts()
        assert len(alerts) == 1

    def test_filter_alerts_by_budget(self, store):
        a1 = BudgetAlert(
            budget_id="BUD-1", budget_name="Test1", level=AlertLevel.WARNING,
            percent_spent=75, amount_spent=375, budget_limit=500, remaining=125,
            period=BudgetPeriod.MONTHLY,
        )
        a2 = BudgetAlert(
            budget_id="BUD-2", budget_name="Test2", level=AlertLevel.CRITICAL,
            percent_spent=100, amount_spent=500, budget_limit=500, remaining=0,
            period=BudgetPeriod.MONTHLY,
        )
        store.save_alert(a1)
        store.save_alert(a2)
        filtered = store.list_alerts(budget_id="BUD-1")
        assert len(filtered) == 1

    def test_clear_alerts(self, store):
        alert = BudgetAlert(
            budget_id="BUD-1", budget_name="Test", level=AlertLevel.WARNING,
            percent_spent=75, amount_spent=375, budget_limit=500, remaining=125,
            period=BudgetPeriod.MONTHLY,
        )
        store.save_alert(alert)
        count = store.clear_alerts()
        assert count == 1
        assert len(store.list_alerts()) == 0

    def test_clear_alerts_by_budget(self, store):
        a1 = BudgetAlert(
            budget_id="BUD-1", budget_name="Test1", level=AlertLevel.WARNING,
            percent_spent=75, amount_spent=375, budget_limit=500, remaining=125,
            period=BudgetPeriod.MONTHLY,
        )
        a2 = BudgetAlert(
            budget_id="BUD-2", budget_name="Test2", level=AlertLevel.CRITICAL,
            percent_spent=100, amount_spent=500, budget_limit=500, remaining=0,
            period=BudgetPeriod.MONTHLY,
        )
        store.save_alert(a1)
        store.save_alert(a2)
        count = store.clear_alerts(budget_id="BUD-1")
        assert count == 1
        assert len(store.list_alerts()) == 1


class TestSavingsGoalStore:
    def test_save_and_list_savings_goals(self, store):
        goal = SavingsGoal(name="Emergency Fund", target_amount=10000.0)
        store.save_savings_goal(goal)
        goals = store.list_savings_goals()
        assert len(goals) == 1
        assert goals[0].name == "Emergency Fund"

    def test_get_savings_goal(self, store):
        goal = SavingsGoal(name="Test", target_amount=1000.0)
        store.save_savings_goal(goal)
        found = store.get_savings_goal(goal.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_savings_goal_not_found(self, store):
        assert store.get_savings_goal("SAV-NONEXISTENT") is None

    def test_update_savings_goal(self, store):
        goal = SavingsGoal(name="Test", target_amount=1000.0)
        store.save_savings_goal(goal)
        goal.current_amount = 500.0
        store.save_savings_goal(goal)
        found = store.get_savings_goal(goal.id)
        assert found.current_amount == 500.0

    def test_delete_savings_goal(self, store):
        goal = SavingsGoal(name="Test", target_amount=1000.0)
        store.save_savings_goal(goal)
        assert store.delete_savings_goal(goal.id) is True
        assert store.get_savings_goal(goal.id) is None

    def test_list_savings_goals_by_status(self, store):
        g1 = SavingsGoal(name="Active", target_amount=1000.0, status=SavingsGoalStatus.ACTIVE)
        g2 = SavingsGoal(name="Completed", target_amount=500.0, status=SavingsGoalStatus.COMPLETED)
        store.save_savings_goal(g1)
        store.save_savings_goal(g2)
        active = store.list_savings_goals(status="active")
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_savings_goal_with_contributions_persist(self, store):
        goal = SavingsGoal(name="Test", target_amount=1000.0, current_amount=100.0)
        goal.contributions.append(SavingsContribution(amount=100.0, note="First"))
        store.save_savings_goal(goal)
        found = store.get_savings_goal(goal.id)
        assert len(found.contributions) == 1
        assert found.contributions[0].amount == 100.0


class TestSpendingRuleStore:
    def test_save_and_list_rules(self, store):
        rule = SpendingRule(name="API Cap", category="api", action=SpendingRuleAction.BLOCK, threshold_amount=500.0)
        store.save_spending_rule(rule)
        rules = store.list_spending_rules()
        assert len(rules) == 1
        assert rules[0].name == "API Cap"

    def test_get_rule(self, store):
        rule = SpendingRule(name="Test", category="api", action=SpendingRuleAction.WARN)
        store.save_spending_rule(rule)
        found = store.get_spending_rule(rule.id)
        assert found is not None
        assert found.name == "Test"

    def test_get_rule_not_found(self, store):
        assert store.get_spending_rule("RUL-NONEXISTENT") is None

    def test_delete_rule(self, store):
        rule = SpendingRule(name="Test", category="api", action=SpendingRuleAction.WARN)
        store.save_spending_rule(rule)
        assert store.delete_spending_rule(rule.id) is True

    def test_list_rules_enabled_only(self, store):
        r1 = SpendingRule(name="Active", category="api", action=SpendingRuleAction.WARN, enabled=True)
        r2 = SpendingRule(name="Disabled", category="api", action=SpendingRuleAction.BLOCK, enabled=False)
        store.save_spending_rule(r1)
        store.save_spending_rule(r2)
        active = store.list_spending_rules(enabled_only=True)
        assert len(active) == 1
        assert active[0].name == "Active"


class TestRolloverStore:
    def test_save_and_list_rollovers(self, store):
        rollover = BudgetRollover(
            budget_id="BUD-1",
            from_period_start=date(2026, 5, 1),
            from_period_end=date(2026, 5, 31),
            to_period_start=date(2026, 6, 1),
            to_period_end=date(2026, 6, 30),
            unspent_amount=50.0,
            previous_limit=500.0,
        )
        store.save_rollover(rollover)
        rollovers = store.list_rollovers()
        assert len(rollovers) == 1

    def test_filter_rollovers_by_budget(self, store):
        r1 = BudgetRollover(
            budget_id="BUD-1", from_period_start=date(2026, 5, 1),
            from_period_end=date(2026, 5, 31), to_period_start=date(2026, 6, 1),
            to_period_end=date(2026, 6, 30), unspent_amount=50.0, previous_limit=500.0,
        )
        r2 = BudgetRollover(
            budget_id="BUD-2", from_period_start=date(2026, 5, 1),
            from_period_end=date(2026, 5, 31), to_period_start=date(2026, 6, 1),
            to_period_end=date(2026, 6, 30), unspent_amount=100.0, previous_limit=1000.0,
        )
        store.save_rollover(r1)
        store.save_rollover(r2)
        filtered = store.list_rollovers(budget_id="BUD-1")
        assert len(filtered) == 1

    def test_get_latest_rollover(self, store):
        r1 = BudgetRollover(
            budget_id="BUD-1", from_period_start=date(2026, 4, 1),
            from_period_end=date(2026, 4, 30), to_period_start=date(2026, 5, 1),
            to_period_end=date(2026, 5, 31), unspent_amount=30.0, previous_limit=500.0,
        )
        r2 = BudgetRollover(
            budget_id="BUD-1", from_period_start=date(2026, 5, 1),
            from_period_end=date(2026, 5, 31), to_period_start=date(2026, 6, 1),
            to_period_end=date(2026, 6, 30), unspent_amount=50.0, previous_limit=500.0,
        )
        store.save_rollover(r1)
        store.save_rollover(r2)
        latest = store.get_latest_rollover("BUD-1")
        assert latest is not None
        assert latest.unspent_amount == 50.0

    def test_get_latest_rollover_no_rollovers(self, store):
        assert store.get_latest_rollover("BUD-NONEXISTENT") is None
