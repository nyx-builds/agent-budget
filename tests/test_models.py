"""Tests for Agent Budget models."""

import pytest
from datetime import date, timedelta

from agent_budget.models import (
    Budget, BudgetPeriod, BudgetRollover, Expense, RecurringExpense, RecurringFrequency,
    BudgetAlert, AlertLevel, AlertThreshold, BudgetComparison,
    SpendingForecast, CurrencyInfo, SUPPORTED_CURRENCIES, format_currency,
    SavingsGoal, SavingsGoalStatus, SavingsContribution,
    SpendingRule, SpendingRuleAction, ExpenseStatus,
)


class TestBudgetPeriod:
    def test_budget_daily_period_start(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.DAILY)
        today = date(2026, 6, 15)
        assert b.get_period_start(today) == today

    def test_budget_weekly_period_start(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.WEEKLY)
        d = date(2026, 6, 15)
        start = b.get_period_start(d)
        assert start == d

    def test_budget_weekly_period_start_midweek(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.WEEKLY)
        d = date(2026, 6, 17)
        start = b.get_period_start(d)
        assert start == date(2026, 6, 15)

    def test_budget_monthly_period_start(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY)
        d = date(2026, 6, 15)
        start = b.get_period_start(d)
        assert start == date(2026, 6, 1)

    def test_budget_quarterly_period_start(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.QUARTERLY)
        d = date(2026, 5, 15)
        start = b.get_period_start(d)
        assert start == date(2026, 4, 1)

    def test_budget_yearly_period_start(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.YEARLY)
        d = date(2026, 6, 15)
        start = b.get_period_start(d)
        assert start == date(2026, 1, 1)

    def test_budget_monthly_period_end(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY)
        d = date(2026, 6, 15)
        end = b.get_period_end(d)
        assert end == date(2026, 6, 30)

    def test_budget_yearly_period_end(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.YEARLY)
        d = date(2026, 6, 15)
        end = b.get_period_end(d)
        assert end == date(2026, 12, 31)


class TestBudget:
    def test_create_budget(self):
        b = Budget(name="API Costs", limit=500.0, period=BudgetPeriod.MONTHLY)
        assert b.name == "API Costs"
        assert b.limit == 500.0
        assert b.period == BudgetPeriod.MONTHLY
        assert b.active is True
        assert b.id.startswith("BUD-")

    def test_create_budget_with_category(self):
        b = Budget(name="API Budget", limit=500.0, period=BudgetPeriod.MONTHLY, category="api")
        assert b.category == "api"

    def test_budget_default_alerts(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY)
        assert len(b.alert_thresholds) == 4
        assert b.alert_thresholds[0].percent == 50
        assert b.alert_thresholds[3].percent == 100

    def test_budget_invalid_limit(self):
        with pytest.raises(Exception):
            Budget(name="Test", limit=0, period=BudgetPeriod.MONTHLY)

    def test_budget_invalid_name(self):
        with pytest.raises(Exception):
            Budget(name="", limit=100, period=BudgetPeriod.MONTHLY)

    def test_budget_rollover_fields(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY, rollover_enabled=True, rollover_cap=50.0)
        assert b.rollover_enabled is True
        assert b.rollover_cap == 50.0
        assert b.current_rollover == 0.0

    def test_budget_effective_limit(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY, current_rollover=25.0)
        assert b.effective_limit == 125.0

    def test_budget_effective_limit_no_rollover(self):
        b = Budget(name="Test", limit=100, period=BudgetPeriod.MONTHLY)
        assert b.effective_limit == 100.0


class TestExpense:
    def test_create_expense(self):
        e = Expense(amount=25.50, category="api", description="OpenAI call")
        assert e.amount == 25.50
        assert e.category == "api"
        assert e.description == "OpenAI call"
        assert e.id.startswith("EXP-")
        assert e.currency == "USD"

    def test_expense_tags_from_string(self):
        e = Expense(amount=10, category="api", tags="gpt4,production")
        assert e.tags == ["gpt4", "production"]

    def test_expense_tags_from_list(self):
        e = Expense(amount=10, category="api", tags=["gpt4", "production"])
        assert e.tags == ["gpt4", "production"]

    def test_expense_invalid_amount(self):
        with pytest.raises(Exception):
            Expense(amount=-10, category="api")

    def test_expense_date_default(self):
        e = Expense(amount=10, category="api")
        assert e.expense_date == date.today()

    def test_expense_vendor_and_receipt(self):
        e = Expense(amount=50, category="api", vendor="OpenAI", receipt_url="https://receipt.example.com/123", reimbursable=True)
        assert e.vendor == "OpenAI"
        assert e.receipt_url == "https://receipt.example.com/123"
        assert e.reimbursable is True
        assert e.approved_by is None

    def test_expense_approved_by(self):
        e = Expense(amount=50, category="api", approved_by="admin")
        assert e.approved_by == "admin"


class TestRecurringExpense:
    def test_create_recurring(self):
        r = RecurringExpense(
            name="AWS Hosting",
            amount=99.0,
            category="infra",
            frequency=RecurringFrequency.MONTHLY,
        )
        assert r.name == "AWS Hosting"
        assert r.amount == 99.0
        assert r.frequency == RecurringFrequency.MONTHLY
        assert r.active is True

    def test_advance_next_due_monthly(self):
        r = RecurringExpense(
            name="Test",
            amount=50,
            category="api",
            frequency=RecurringFrequency.MONTHLY,
            next_due=date(2026, 1, 15),
        )
        assert r.advance_next_due() == date(2026, 2, 15)

    def test_advance_next_due_weekly(self):
        r = RecurringExpense(
            name="Test",
            amount=50,
            category="api",
            frequency=RecurringFrequency.WEEKLY,
            next_due=date(2026, 6, 15),
        )
        assert r.advance_next_due() == date(2026, 6, 22)

    def test_advance_next_due_yearly(self):
        r = RecurringExpense(
            name="Test",
            amount=50,
            category="api",
            frequency=RecurringFrequency.YEARLY,
            next_due=date(2026, 1, 1),
        )
        assert r.advance_next_due() == date(2027, 1, 1)

    def test_advance_next_due_quarterly(self):
        r = RecurringExpense(
            name="Test",
            amount=50,
            category="api",
            frequency=RecurringFrequency.QUARTERLY,
            next_due=date(2026, 1, 1),
        )
        assert r.advance_next_due() == date(2026, 4, 1)

    def test_advance_next_due_yearly_wrap(self):
        r = RecurringExpense(
            name="Test",
            amount=50,
            category="api",
            frequency=RecurringFrequency.MONTHLY,
            next_due=date(2026, 12, 1),
        )
        assert r.advance_next_due() == date(2027, 1, 1)


class TestAlertThreshold:
    def test_valid_threshold(self):
        t = AlertThreshold(percent=75, level=AlertLevel.WARNING)
        assert t.percent == 75
        assert t.level == AlertLevel.WARNING

    def test_invalid_percent(self):
        with pytest.raises(Exception):
            AlertThreshold(percent=150, level=AlertLevel.WARNING)


class TestBudgetAlert:
    def test_create_alert(self):
        a = BudgetAlert(
            budget_id="BUD-TEST",
            budget_name="Test Budget",
            level=AlertLevel.WARNING,
            percent_spent=75.0,
            amount_spent=375.0,
            budget_limit=500.0,
            remaining=125.0,
            period=BudgetPeriod.MONTHLY,
            message="Budget 'Test Budget' is at 75.0%",
        )
        assert a.budget_id == "BUD-TEST"
        assert a.level == AlertLevel.WARNING


class TestBudgetComparison:
    def test_create_comparison(self):
        c = BudgetComparison(
            budget_id="BUD-TEST",
            budget_name="Test",
            category="api",
            budget_limit=500.0,
            actual_spent=375.0,
            remaining=125.0,
            percent_used=75.0,
            period=BudgetPeriod.MONTHLY,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            status="on_track",
        )
        assert c.percent_used == 75.0
        assert c.status == "on_track"

    def test_comparison_with_rollover(self):
        c = BudgetComparison(
            budget_id="BUD-TEST",
            budget_name="Test",
            category="api",
            budget_limit=500.0,
            actual_spent=375.0,
            remaining=150.0,
            percent_used=69.2,
            period=BudgetPeriod.MONTHLY,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            status="under",
            rollover_amount=25.0,
            effective_limit=525.0,
        )
        assert c.rollover_amount == 25.0
        assert c.effective_limit == 525.0


class TestSpendingForecast:
    def test_create_forecast(self):
        f = SpendingForecast(
            category="api",
            period="July 2026",
            projected_spending=450.0,
            budget_limit=500.0,
            confidence=0.8,
            based_on_periods=6,
        )
        assert f.projected_spending == 450.0
        assert f.confidence == 0.8


class TestSavingsGoal:
    def test_create_savings_goal(self):
        g = SavingsGoal(name="Emergency Fund", target_amount=10000.0)
        assert g.name == "Emergency Fund"
        assert g.target_amount == 10000.0
        assert g.current_amount == 0.0
        assert g.status == SavingsGoalStatus.ACTIVE
        assert g.id.startswith("SAV-")

    def test_savings_goal_progress(self):
        g = SavingsGoal(name="Test", target_amount=1000.0, current_amount=750.0)
        assert g.progress_percent == 75.0
        assert g.remaining == 250.0
        assert g.is_complete is False

    def test_savings_goal_complete(self):
        g = SavingsGoal(name="Test", target_amount=1000.0, current_amount=1000.0)
        assert g.is_complete is True
        assert g.remaining == 0.0
        assert g.progress_percent == 100.0

    def test_savings_goal_over_funded(self):
        g = SavingsGoal(name="Test", target_amount=1000.0, current_amount=1200.0)
        assert g.is_complete is True
        assert g.progress_percent == 100.0  # capped at 100
        assert g.remaining == 0.0

    def test_savings_goal_monthly_contribution_needed(self):
        g = SavingsGoal(
            name="Test", target_amount=1200.0, current_amount=600.0,
            target_date=date.today() + timedelta(days=180),
        )
        needed = g.monthly_contribution_needed
        assert needed is not None
        assert needed > 0

    def test_savings_goal_monthly_contribution_no_target_date(self):
        g = SavingsGoal(name="Test", target_amount=1000.0)
        assert g.monthly_contribution_needed is None

    def test_savings_goal_with_contributions(self):
        g = SavingsGoal(name="Test", target_amount=1000.0)
        c = SavingsContribution(amount=100.0, note="First deposit")
        g.contributions.append(c)
        g.current_amount = 100.0
        assert len(g.contributions) == 1
        assert g.progress_percent == 10.0


class TestSavingsContribution:
    def test_create_contribution(self):
        c = SavingsContribution(amount=100.0, note="Test contribution")
        assert c.amount == 100.0
        assert c.note == "Test contribution"
        assert c.id.startswith("CON-")


class TestSpendingRule:
    def test_create_spending_rule(self):
        r = SpendingRule(
            name="API Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
        )
        assert r.name == "API Cap"
        assert r.action == SpendingRuleAction.BLOCK
        assert r.threshold_amount == 500.0
        assert r.enabled is True

    def test_spending_rule_check_expense_blocked(self):
        rule = SpendingRule(
            name="API Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
        )
        expense = Expense(amount=100, category="api")
        # budget_spent=450, so 450+100=550 > 500
        result = rule.check_expense(expense, budget_spent=450.0, budget_limit=1000.0)
        assert result is not None
        assert "exceed" in result.lower()

    def test_spending_rule_check_expense_allowed(self):
        rule = SpendingRule(
            name="API Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
        )
        expense = Expense(amount=50, category="api")
        result = rule.check_expense(expense, budget_spent=100.0, budget_limit=1000.0)
        assert result is None

    def test_spending_rule_check_expense_wrong_category(self):
        rule = SpendingRule(
            name="API Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
        )
        expense = Expense(amount=100, category="infra")
        result = rule.check_expense(expense, budget_spent=450.0, budget_limit=1000.0)
        assert result is None  # Different category, no violation

    def test_spending_rule_approval_threshold(self):
        rule = SpendingRule(
            name="API Approval",
            category="api",
            action=SpendingRuleAction.BLOCK,
            requires_approval_above=100.0,
        )
        expense = Expense(amount=150, category="api")
        result = rule.check_expense(expense, budget_spent=0.0, budget_limit=1000.0)
        assert result is not None
        assert "approval" in result.lower()

    def test_spending_rule_approval_with_approver(self):
        rule = SpendingRule(
            name="API Approval",
            category="api",
            action=SpendingRuleAction.BLOCK,
            requires_approval_above=100.0,
        )
        expense = Expense(amount=150, category="api", approved_by="admin")
        result = rule.check_expense(expense, budget_spent=0.0, budget_limit=1000.0)
        assert result is None  # Approved

    def test_spending_rule_warn_action(self):
        rule = SpendingRule(
            name="API Warning",
            category="api",
            action=SpendingRuleAction.WARN,
            requires_approval_above=100.0,
        )
        expense = Expense(amount=150, category="api")
        result = rule.check_expense(expense, budget_spent=0.0, budget_limit=1000.0)
        assert result is not None
        assert "WARNING" in result

    def test_spending_rule_percent_threshold(self):
        rule = SpendingRule(
            name="80% Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_percent=80.0,
        )
        expense = Expense(amount=100, category="api")
        # budget_spent=750/1000=75%, adding 100 makes 85% > 80%
        result = rule.check_expense(expense, budget_spent=750.0, budget_limit=1000.0)
        assert result is not None
        assert "85.0%" in result

    def test_spending_rule_disabled(self):
        rule = SpendingRule(
            name="API Cap",
            category="api",
            action=SpendingRuleAction.BLOCK,
            threshold_amount=500.0,
            enabled=False,
        )
        expense = Expense(amount=100, category="api")
        result = rule.check_expense(expense, budget_spent=450.0, budget_limit=1000.0)
        assert result is None  # Disabled, no violation


class TestBudgetRollover:
    def test_create_rollover(self):
        r = BudgetRollover(
            budget_id="BUD-TEST",
            from_period_start=date(2026, 5, 1),
            from_period_end=date(2026, 5, 31),
            to_period_start=date(2026, 6, 1),
            to_period_end=date(2026, 6, 30),
            unspent_amount=50.0,
            previous_limit=500.0,
        )
        assert r.budget_id == "BUD-TEST"
        assert r.unspent_amount == 50.0


class TestFormatCurrency:
    def test_usd(self):
        assert format_currency(1234.56) == "$1,234.56"

    def test_eur(self):
        assert format_currency(1234.56, "EUR") == "€1,234.56"

    def test_jpy(self):
        assert format_currency(1234, "JPY") == "¥1,234"

    def test_unknown_currency(self):
        result = format_currency(100, "XYZ")
        assert "100.00" in result


class TestSupportedCurrencies:
    def test_all_currencies_present(self):
        expected = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "BRL", "KRW", "MXN", "SGD", "SEK", "NZD"]
        for code in expected:
            assert code in SUPPORTED_CURRENCIES
