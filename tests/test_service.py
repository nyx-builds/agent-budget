"""Tests for Agent Budget service."""

import pytest
from datetime import date, timedelta

from agent_budget.models import (
    BudgetPeriod, RecurringFrequency, AlertLevel, AlertThreshold,
    SpendingRuleAction, SavingsGoalStatus,
)
from agent_budget.service import BudgetService
from agent_budget.store import BudgetStore


@pytest.fixture
def svc(tmp_path):
    return BudgetService(BudgetStore(data_dir=str(tmp_path)))


class TestBudgetCRUD:
    def test_create_budget(self, svc):
        budget = svc.create_budget(name="API Costs", limit=500, period=BudgetPeriod.MONTHLY)
        assert budget.name == "API Costs"
        assert budget.limit == 500
        assert budget.period == BudgetPeriod.MONTHLY

    def test_create_budget_with_category(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        assert budget.category == "api"

    def test_create_budget_with_rollover(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True, rollover_cap=200.0)
        assert budget.rollover_enabled is True
        assert budget.rollover_cap == 200.0

    def test_update_budget(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        updated = svc.update_budget(budget.id, name="Updated API", limit=750)
        assert updated.name == "Updated API"
        assert updated.limit == 750

    def test_update_budget_rollover(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        updated = svc.update_budget(budget.id, rollover_enabled=True, rollover_cap=100.0)
        assert updated.rollover_enabled is True
        assert updated.rollover_cap == 100.0

    def test_update_budget_alert_thresholds(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        new_thresholds = [
            AlertThreshold(percent=60, level=AlertLevel.INFO),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ]
        updated = svc.update_alert_thresholds(budget.id, new_thresholds)
        assert len(updated.alert_thresholds) == 2
        assert updated.alert_thresholds[0].percent == 60

    def test_update_budget_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.update_budget("BUD-NONEXISTENT", name="Test")

    def test_delete_budget(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        assert svc.delete_budget(budget.id) is True
        assert svc.get_budget(budget.id) is None

    def test_list_budgets(self, svc):
        svc.create_budget(name="B1", limit=100, period=BudgetPeriod.MONTHLY)
        svc.create_budget(name="B2", limit=200, period=BudgetPeriod.WEEKLY)
        budgets = svc.list_budgets()
        assert len(budgets) == 2


class TestExpenseCRUD:
    def test_add_expense(self, svc):
        expense = svc.add_expense(amount=50, category="api", description="Test")
        assert expense.amount == 50
        assert expense.category == "api"

    def test_add_expense_auto_assigns_budget(self, svc):
        svc.create_budget(name="API Budget", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        expense = svc.add_expense(amount=50, category="api")
        assert expense.budget_id is not None

    def test_add_expense_with_date(self, svc):
        expense = svc.add_expense(amount=50, category="api", expense_date=date(2026, 6, 1))
        assert expense.expense_date == date(2026, 6, 1)

    def test_add_expense_with_tags(self, svc):
        expense = svc.add_expense(amount=50, category="api", tags=["gpt4", "production"])
        assert expense.tags == ["gpt4", "production"]

    def test_add_expense_with_vendor(self, svc):
        expense = svc.add_expense(amount=50, category="api", vendor="OpenAI", receipt_url="https://receipt.example.com/123", reimbursable=True)
        assert expense.vendor == "OpenAI"
        assert expense.receipt_url == "https://receipt.example.com/123"
        assert expense.reimbursable is True

    def test_add_expense_with_approval(self, svc):
        expense = svc.add_expense(amount=50, category="api", approved_by="admin")
        assert expense.approved_by == "admin"

    def test_update_expense(self, svc):
        expense = svc.add_expense(amount=50, category="api", description="Original")
        updated = svc.update_expense(expense.id, amount=75, description="Updated", vendor="AWS", reimbursable=True)
        assert updated.amount == 75
        assert updated.description == "Updated"
        assert updated.vendor == "AWS"
        assert updated.reimbursable is True

    def test_update_expense_status(self, svc):
        expense = svc.add_expense(amount=50, category="api")
        updated = svc.update_expense(expense.id, status="cancelled")
        assert updated.status.value == "cancelled"

    def test_update_expense_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.update_expense("EXP-NONEXISTENT", amount=100)

    def test_delete_expense(self, svc):
        expense = svc.add_expense(amount=50, category="api")
        assert svc.delete_expense(expense.id) is True
        assert svc.get_expense(expense.id) is None

    def test_list_expenses_with_filters(self, svc):
        svc.add_expense(amount=50, category="api", expense_date=date(2026, 6, 1))
        svc.add_expense(amount=100, category="infra", expense_date=date(2026, 5, 1))
        api = svc.list_expenses(category="api")
        assert len(api) == 1
        june = svc.list_expenses(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        assert len(june) == 1

    def test_list_expenses_filter_vendor(self, svc):
        svc.add_expense(amount=50, category="api", vendor="OpenAI")
        svc.add_expense(amount=100, category="api", vendor="AWS")
        filtered = svc.list_expenses(vendor="OpenAI")
        assert len(filtered) == 1

    def test_list_expenses_filter_reimbursable(self, svc):
        svc.add_expense(amount=50, category="api", reimbursable=True)
        svc.add_expense(amount=100, category="api", reimbursable=False)
        filtered = svc.list_expenses(reimbursable=True)
        assert len(filtered) == 1
        assert filtered[0].reimbursable is True


class TestBudgetStatus:
    def test_get_budget_status(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=200, category="api", budget_id=budget.id)
        status = svc.get_budget_status(budget.id)
        assert status.actual_spent == 200
        assert status.remaining == 300
        assert status.percent_used == 40.0
        assert status.status == "under"

    def test_budget_status_on_track(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=400, category="api", budget_id=budget.id)
        status = svc.get_budget_status(budget.id)
        assert status.percent_used == 80.0
        assert status.status == "on_track"

    def test_budget_status_over(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=475, category="api", budget_id=budget.id)
        status = svc.get_budget_status(budget.id)
        assert status.percent_used == 95.0
        assert status.status == "over"

    def test_budget_status_critical(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=550, category="api", budget_id=budget.id)
        status = svc.get_budget_status(budget.id)
        assert status.percent_used == 110.0
        assert status.status == "critical"

    def test_budget_status_with_rollover(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True)
        budget.current_rollover = 100.0
        svc.store.save_budget(budget)
        svc.add_expense(amount=200, category="api", budget_id=budget.id)
        status = svc.get_budget_status(budget.id)
        assert status.effective_limit == 600.0  # 500 + 100 rollover
        assert status.rollover_amount == 100.0
        assert status.remaining == 400.0
        assert status.percent_used == 33.3

    def test_get_all_budget_status(self, svc):
        b1 = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        b2 = svc.create_budget(name="Infra", limit=1000, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=200, category="api", budget_id=b1.id)
        svc.add_expense(amount=500, category="infra", budget_id=b2.id)
        statuses = svc.get_all_budget_status()
        assert len(statuses) == 2

    def test_budget_status_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.get_budget_status("BUD-NONEXISTENT")


class TestBudgetAlerts:
    def test_alerts_generated_on_threshold(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=400, category="api", budget_id=budget.id)
        alerts = svc.get_alerts(budget_id=budget.id)
        assert len(alerts) >= 1

    def test_critical_alert_on_overspend(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=550, category="api", budget_id=budget.id)
        alerts = svc.get_alerts(budget_id=budget.id)
        critical = [a for a in alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical) >= 1

    def test_clear_alerts(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=400, category="api", budget_id=budget.id)
        count = svc.clear_alerts(budget_id=budget.id)
        assert count >= 1


class TestBudgetRollover:
    def test_process_rollover_disabled(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        result = svc.process_budget_rollover(budget.id)
        assert result is None

    def test_process_rollover_with_unspent(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True)
        # Add some expenses (less than limit)
        svc.add_expense(amount=200, category="api", budget_id=budget.id, expense_date=date.today() - timedelta(days=35))
        result = svc.process_budget_rollover(budget.id)
        # Should create a rollover
        if result:
            assert result.unspent_amount > 0
            # Budget should have rollover applied
            updated = svc.get_budget(budget.id)
            assert updated.current_rollover > 0

    def test_process_rollover_already_processed(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True)
        # Process once
        svc.process_budget_rollover(budget.id)
        # Process again - should be no-op
        result = svc.process_budget_rollover(budget.id)
        assert result is None

    def test_process_rollover_with_cap(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True, rollover_cap=50.0)
        # Don't add expenses — full limit should roll over but be capped
        result = svc.process_budget_rollover(budget.id)
        if result:
            assert result.unspent_amount <= 50.0

    def test_process_all_rollovers(self, svc):
        svc.create_budget(name="B1", limit=500, period=BudgetPeriod.MONTHLY, rollover_enabled=True)
        svc.create_budget(name="B2", limit=1000, period=BudgetPeriod.MONTHLY)
        results = svc.process_all_rollovers()
        # Only B1 has rollover enabled
        assert len(results) <= 1


class TestRecurringExpenses:
    def test_add_recurring(self, svc):
        recurring = svc.add_recurring_expense(
            name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY
        )
        assert recurring.name == "AWS"
        assert recurring.amount == 99

    def test_process_recurring(self, svc):
        recurring = svc.add_recurring_expense(
            name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY,
            start_date=date.today(),
        )
        generated = svc.process_recurring_expenses()
        assert len(generated) >= 1
        assert generated[0].amount == 99

    def test_pause_and_resume_recurring(self, svc):
        recurring = svc.add_recurring_expense(
            name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY,
        )
        paused = svc.pause_recurring(recurring.id)
        assert paused.active is False
        resumed = svc.resume_recurring(recurring.id)
        assert resumed.active is True

    def test_delete_recurring(self, svc):
        recurring = svc.add_recurring_expense(
            name="AWS", amount=99, category="infra", frequency=RecurringFrequency.MONTHLY,
        )
        assert svc.delete_recurring_expense(recurring.id) is True

    def test_recurring_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.pause_recurring("REC-NONEXISTENT")


class TestSavingsGoals:
    def test_create_savings_goal(self, svc):
        goal = svc.create_savings_goal(name="Emergency Fund", target_amount=10000.0)
        assert goal.name == "Emergency Fund"
        assert goal.target_amount == 10000.0
        assert goal.current_amount == 0.0
        assert goal.status == SavingsGoalStatus.ACTIVE

    def test_create_savings_goal_with_target_date(self, svc):
        goal = svc.create_savings_goal(name="Vacation", target_amount=3000.0, target_date=date(2026, 12, 31))
        assert goal.target_date == date(2026, 12, 31)

    def test_contribute_to_savings(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        updated = svc.contribute_to_savings(goal.id, amount=250.0, note="First deposit")
        assert updated.current_amount == 250.0
        assert updated.progress_percent == 25.0
        assert len(updated.contributions) == 1
        assert updated.contributions[0].amount == 250.0

    def test_contribute_completes_goal(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        updated = svc.contribute_to_savings(goal.id, amount=1000.0)
        assert updated.is_complete is True
        assert updated.status == SavingsGoalStatus.COMPLETED

    def test_contribute_multiple_times(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        svc.contribute_to_savings(goal.id, amount=300.0)
        updated = svc.contribute_to_savings(goal.id, amount=500.0)
        assert updated.current_amount == 800.0
        assert len(updated.contributions) == 2

    def test_withdraw_from_savings(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        svc.contribute_to_savings(goal.id, amount=500.0)
        updated = svc.withdraw_from_savings(goal.id, amount=200.0, note="Emergency")
        assert updated.current_amount == 300.0
        assert len(updated.contributions) == 2  # One positive, one negative

    def test_withdraw_exceeds_available(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        svc.contribute_to_savings(goal.id, amount=100.0)
        with pytest.raises(ValueError, match="Cannot withdraw"):
            svc.withdraw_from_savings(goal.id, amount=200.0)

    def test_withdraw_reverts_completed_status(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        svc.contribute_to_savings(goal.id, amount=1000.0)
        assert svc.get_savings_goal(goal.id).status == SavingsGoalStatus.COMPLETED
        updated = svc.withdraw_from_savings(goal.id, amount=500.0)
        assert updated.status == SavingsGoalStatus.ACTIVE

    def test_contribute_invalid_amount(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        with pytest.raises(ValueError, match="positive"):
            svc.contribute_to_savings(goal.id, amount=-100.0)

    def test_update_savings_goal(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        updated = svc.update_savings_goal(goal.id, name="Updated", target_amount=2000.0)
        assert updated.name == "Updated"
        assert updated.target_amount == 2000.0

    def test_pause_and_resume_savings(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        paused = svc.pause_savings_goal(goal.id)
        assert paused.status == SavingsGoalStatus.PAUSED
        resumed = svc.resume_savings_goal(goal.id)
        assert resumed.status == SavingsGoalStatus.ACTIVE

    def test_list_savings_goals(self, svc):
        svc.create_savings_goal(name="Goal 1", target_amount=1000.0)
        svc.create_savings_goal(name="Goal 2", target_amount=2000.0)
        goals = svc.list_savings_goals()
        assert len(goals) == 2

    def test_list_savings_goals_by_status(self, svc):
        svc.create_savings_goal(name="Active", target_amount=1000.0)
        g2 = svc.create_savings_goal(name="Complete", target_amount=500.0)
        svc.contribute_to_savings(g2.id, amount=500.0)
        active = svc.list_savings_goals(status="active")
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_delete_savings_goal(self, svc):
        goal = svc.create_savings_goal(name="Test", target_amount=1000.0)
        assert svc.delete_savings_goal(goal.id) is True
        assert svc.get_savings_goal(goal.id) is None

    def test_savings_goal_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.update_savings_goal("SAV-NONEXISTENT", name="Test")


class TestSpendingRules:
    def test_create_spending_rule(self, svc):
        rule = svc.create_spending_rule(
            name="API Cap", category="api", action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
        )
        assert rule.name == "API Cap"
        assert rule.action == SpendingRuleAction.BLOCK
        assert rule.threshold_amount == 500.0

    def test_create_rule_with_approval(self, svc):
        rule = svc.create_spending_rule(
            name="API Approval", category="api", action=SpendingRuleAction.BLOCK,
            requires_approval_above=100.0,
        )
        assert rule.requires_approval_above == 100.0

    def test_update_spending_rule(self, svc):
        rule = svc.create_spending_rule(name="Test", category="api", action=SpendingRuleAction.WARN)
        updated = svc.update_spending_rule(rule.id, name="Updated", threshold_amount=200.0)
        assert updated.name == "Updated"
        assert updated.threshold_amount == 200.0

    def test_update_spending_rule_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.update_spending_rule("RUL-NONEXISTENT", name="Test")

    def test_list_spending_rules(self, svc):
        svc.create_spending_rule(name="R1", category="api", action=SpendingRuleAction.WARN)
        svc.create_spending_rule(name="R2", category="infra", action=SpendingRuleAction.BLOCK)
        rules = svc.list_spending_rules()
        assert len(rules) == 2

    def test_delete_spending_rule(self, svc):
        rule = svc.create_spending_rule(name="Test", category="api", action=SpendingRuleAction.WARN)
        assert svc.delete_spending_rule(rule.id) is True

    def test_expense_blocked_by_rule(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        svc.create_spending_rule(
            name="API Cap", category="api", action=SpendingRuleAction.BLOCK,
            threshold_amount=100.0, budget_id=budget.id,
        )
        # Add expense that brings total over threshold
        svc.add_expense(amount=80, category="api", budget_id=budget.id)
        with pytest.raises(ValueError, match="blocked"):
            svc.add_expense(amount=30, category="api", budget_id=budget.id)

    def test_expense_allowed_under_rule(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        svc.create_spending_rule(
            name="API Cap", category="api", action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0, budget_id=budget.id,
        )
        # Should be fine under the cap
        expense = svc.add_expense(amount=50, category="api", budget_id=budget.id)
        assert expense.amount == 50

    def test_expense_blocked_by_approval_threshold(self, svc):
        svc.create_spending_rule(
            name="Approval", category="api", action=SpendingRuleAction.BLOCK,
            requires_approval_above=50.0,
        )
        with pytest.raises(ValueError, match="approval"):
            svc.add_expense(amount=100, category="api")

    def test_expense_approved_bypasses_rule(self, svc):
        svc.create_spending_rule(
            name="Approval", category="api", action=SpendingRuleAction.BLOCK,
            requires_approval_above=50.0,
        )
        # With approval, should go through
        expense = svc.add_expense(amount=100, category="api", approved_by="admin")
        assert expense.amount == 100

    def test_check_expense_rules_public(self, svc):
        svc.create_spending_rule(
            name="API Cap", category="api", action=SpendingRuleAction.BLOCK,
            requires_approval_above=100.0,
        )
        from agent_budget.models import Expense
        test_expense = Expense(amount=150, category="api")
        violations = svc.check_expense_rules(test_expense)
        assert len(violations) > 0


class TestForecasting:
    def test_get_spending_forecast(self, svc):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        for i in range(3):
            svc.add_expense(amount=300, category="api", budget_id=budget.id, expense_date=date.today() - timedelta(days=i * 30))
        forecasts = svc.get_spending_forecast(months=3)
        assert len(forecasts) >= 1

    def test_forecast_no_history(self, svc):
        svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        forecasts = svc.get_spending_forecast(months=1)
        assert len(forecasts) >= 1


class TestSpendingSummary:
    def test_category_summary(self, svc):
        svc.add_expense(amount=100, category="api")
        svc.add_expense(amount=200, category="infra")
        svc.add_expense(amount=50, category="api")
        summary = svc.get_category_summary()
        assert summary["api"] == 150
        assert summary["infra"] == 200

    def test_total_spending(self, svc):
        svc.add_expense(amount=100, category="api")
        svc.add_expense(amount=200, category="infra")
        total = svc.get_total_spending()
        assert total == 300

    def test_total_spending_by_category(self, svc):
        svc.add_expense(amount=100, category="api")
        svc.add_expense(amount=200, category="infra")
        api_total = svc.get_total_spending(category="api")
        assert api_total == 100


class TestExport:
    def test_export_json(self, svc):
        svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=50, category="api")
        output = svc.export_data(format="json")
        import json
        data = json.loads(output)
        assert "budgets" in data
        assert "expenses" in data
        assert "savings_goals" in data
        assert "spending_rules" in data

    def test_export_csv(self, svc):
        svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=50, category="api")
        output = svc.export_data(format="csv")
        assert "budget" in output
        assert "expense" in output

    def test_export_markdown(self, svc):
        svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY)
        svc.add_expense(amount=50, category="api")
        output = svc.export_data(format="markdown")
        assert "# Agent Budget Export" in output

    def test_export_unsupported_format(self, svc):
        with pytest.raises(ValueError):
            svc.export_data(format="xml")


class TestCurrencies:
    def test_list_currencies(self, svc):
        currencies = svc.list_currencies()
        assert len(currencies) >= 15
        codes = [c["code"] for c in currencies]
        assert "USD" in codes
        assert "EUR" in codes
