"""Tests for v0.4.0 features: income tracking, recurring income, cash flow, burn rate, dashboard."""

import pytest
import os
import tempfile
import json
from datetime import date, timedelta

from agent_budget.models import (
    Income, RecurringIncome, IncomeStatus, RecurringFrequency,
    CashFlowSummary, BurnRate, FinancialDashboard,
    TrendDirection,
)
from agent_budget.store import BudgetStore
from agent_budget.service import BudgetService


@pytest.fixture
def temp_store():
    """Create a store with a temporary directory."""
    tmpdir = tempfile.mkdtemp()
    store = BudgetStore(data_dir=tmpdir)
    yield store
    # cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def svc(temp_store):
    return BudgetService(store=temp_store)


# ===== Income Model Tests =====

class TestIncomeModel:
    def test_income_defaults(self):
        inc = Income(amount=100.0, source="client-A")
        assert inc.amount == 100.0
        assert inc.source == "client-A"
        assert inc.currency == "USD"
        assert inc.status == IncomeStatus.RECEIVED
        assert inc.tags == []
        assert inc.description == ""
        assert inc.invoice_ref is None
        assert inc.recurring_id is None
        assert inc.id.startswith("INC-")

    def test_income_tags_string_parse(self):
        inc = Income(amount=50.0, source="api-sales", tags="a, b ,c")
        assert inc.tags == ["a", "b", "c"]

    def test_income_negative_amount_rejected(self):
        with pytest.raises(Exception):
            Income(amount=-10.0, source="test")

    def test_income_zero_amount_rejected(self):
        with pytest.raises(Exception):
            Income(amount=0, source="test")

    def test_income_status_enum(self):
        assert IncomeStatus.PENDING == "pending"
        assert IncomeStatus.RECEIVED == "received"
        assert IncomeStatus.CANCELLED == "cancelled"


class TestRecurringIncomeModel:
    def test_recurring_income_defaults(self):
        ri = RecurringIncome(
            name="Monthly retainer",
            amount=5000.0,
            source="client-A",
            frequency=RecurringFrequency.MONTHLY,
        )
        assert ri.name == "Monthly retainer"
        assert ri.amount == 5000.0
        assert ri.source == "client-A"
        assert ri.frequency == RecurringFrequency.MONTHLY
        assert ri.active is True
        assert ri.id.startswith("RIC-")

    def test_advance_next_due_monthly(self):
        ri = RecurringIncome(
            name="Test",
            amount=1000.0,
            source="test",
            frequency=RecurringFrequency.MONTHLY,
            next_due=date(2026, 1, 15),
        )
        assert ri.advance_next_due() == date(2026, 2, 15)

    def test_advance_next_due_monthly_year_rollover(self):
        ri = RecurringIncome(
            name="Test",
            amount=1000.0,
            source="test",
            frequency=RecurringFrequency.MONTHLY,
            next_due=date(2026, 12, 15),
        )
        assert ri.advance_next_due() == date(2027, 1, 15)

    def test_advance_next_due_weekly(self):
        ri = RecurringIncome(
            name="Test",
            amount=100.0,
            source="test",
            frequency=RecurringFrequency.WEEKLY,
            next_due=date(2026, 1, 5),
        )
        assert ri.advance_next_due() == date(2026, 1, 12)

    def test_advance_next_due_quarterly(self):
        ri = RecurringIncome(
            name="Test",
            amount=500.0,
            source="test",
            frequency=RecurringFrequency.QUARTERLY,
            next_due=date(2026, 1, 1),
        )
        assert ri.advance_next_due() == date(2026, 4, 1)

    def test_advance_next_due_yearly(self):
        ri = RecurringIncome(
            name="Test",
            amount=12000.0,
            source="test",
            frequency=RecurringFrequency.YEARLY,
            next_due=date(2026, 6, 15),
        )
        assert ri.advance_next_due() == date(2027, 6, 15)

    def test_recurring_income_tags_parse(self):
        ri = RecurringIncome(
            name="Test",
            amount=100.0,
            source="test",
            frequency=RecurringFrequency.MONTHLY,
            tags="x, y",
        )
        assert ri.tags == ["x", "y"]


# ===== Store Tests =====

class TestIncomeStore:
    def test_save_and_get_income(self, temp_store):
        inc = Income(amount=500.0, source="client-A")
        saved = temp_store.save_income(inc)
        assert saved.id == inc.id
        retrieved = temp_store.get_income(inc.id)
        assert retrieved is not None
        assert retrieved.amount == 500.0

    def test_get_income_not_found(self, temp_store):
        assert temp_store.get_income("NONEXIST") is None

    def test_list_income_empty(self, temp_store):
        assert temp_store.list_income() == []

    def test_list_income_filter_by_source(self, temp_store):
        temp_store.save_income(Income(amount=100.0, source="client-A"))
        temp_store.save_income(Income(amount=200.0, source="client-B"))
        result = temp_store.list_income(source="client-A")
        assert len(result) == 1
        assert result[0].source == "client-A"

    def test_list_income_filter_by_date_range(self, temp_store):
        temp_store.save_income(Income(amount=100.0, source="A", income_date=date(2026, 1, 15)))
        temp_store.save_income(Income(amount=200.0, source="B", income_date=date(2026, 6, 15)))
        result = temp_store.list_income(start_date=date(2026, 6, 1))
        assert len(result) == 1
        assert result[0].source == "B"

    def test_list_income_filter_by_status(self, temp_store):
        temp_store.save_income(Income(amount=100.0, source="A", status=IncomeStatus.RECEIVED))
        temp_store.save_income(Income(amount=200.0, source="B", status=IncomeStatus.PENDING))
        result = temp_store.list_income(status="pending")
        assert len(result) == 1
        assert result[0].source == "B"

    def test_list_income_sorted_by_date_desc(self, temp_store):
        temp_store.save_income(Income(amount=100.0, source="A", income_date=date(2026, 1, 1)))
        temp_store.save_income(Income(amount=200.0, source="B", income_date=date(2026, 6, 1)))
        result = temp_store.list_income()
        assert result[0].source == "B"  # more recent first

    def test_delete_income(self, temp_store):
        inc = Income(amount=100.0, source="A")
        temp_store.save_income(inc)
        assert temp_store.delete_income(inc.id) is True
        assert temp_store.get_income(inc.id) is None

    def test_delete_income_not_found(self, temp_store):
        assert temp_store.delete_income("NONEXIST") is False

    def test_update_income_via_save(self, temp_store):
        inc = Income(amount=100.0, source="A")
        temp_store.save_income(inc)
        inc.amount = 200.0
        temp_store.save_income(inc)
        retrieved = temp_store.get_income(inc.id)
        assert retrieved.amount == 200.0
        assert len(temp_store.list_income()) == 1


class TestRecurringIncomeStore:
    def test_save_and_get_recurring_income(self, temp_store):
        ri = RecurringIncome(name="Test", amount=100.0, source="A", frequency=RecurringFrequency.MONTHLY)
        saved = temp_store.save_recurring_income(ri)
        retrieved = temp_store.get_recurring_income(ri.id)
        assert retrieved is not None
        assert retrieved.amount == 100.0

    def test_list_recurring_income_active_only(self, temp_store):
        ri1 = RecurringIncome(name="Active", amount=100.0, source="A", frequency=RecurringFrequency.MONTHLY, active=True)
        ri2 = RecurringIncome(name="Inactive", amount=200.0, source="B", frequency=RecurringFrequency.MONTHLY, active=False)
        temp_store.save_recurring_income(ri1)
        temp_store.save_recurring_income(ri2)
        active = temp_store.list_recurring_income(active_only=True)
        assert len(active) == 1
        assert active[0].name == "Active"

    def test_delete_recurring_income(self, temp_store):
        ri = RecurringIncome(name="Test", amount=100.0, source="A", frequency=RecurringFrequency.MONTHLY)
        temp_store.save_recurring_income(ri)
        assert temp_store.delete_recurring_income(ri.id) is True
        assert temp_store.get_recurring_income(ri.id) is None


# ===== Service Income Tests =====

class TestIncomeService:
    def test_add_income(self, svc):
        inc = svc.add_income(amount=1000.0, source="client-A", description="Consulting")
        assert inc.amount == 1000.0
        assert inc.source == "client-A"
        assert inc.id.startswith("INC-")

    def test_add_income_zero_rejected(self, svc):
        with pytest.raises(ValueError, match="positive"):
            svc.add_income(amount=0, source="test")

    def test_add_income_negative_rejected(self, svc):
        with pytest.raises(ValueError, match="positive"):
            svc.add_income(amount=-10, source="test")

    def test_update_income(self, svc):
        inc = svc.add_income(amount=1000.0, source="A")
        updated = svc.update_income(income_id=inc.id, amount=1500.0, description="Updated")
        assert updated.amount == 1500.0

    def test_update_income_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.update_income(income_id="NONEXIST", amount=100)

    def test_update_income_zero_rejected(self, svc):
        inc = svc.add_income(amount=1000.0, source="A")
        with pytest.raises(ValueError, match="positive"):
            svc.update_income(income_id=inc.id, amount=0)

    def test_delete_income(self, svc):
        inc = svc.add_income(amount=500.0, source="A")
        assert svc.delete_income(inc.id) is True
        assert svc.get_income(inc.id) is None

    def test_list_income(self, svc):
        svc.add_income(amount=100.0, source="A", income_date=date(2026, 1, 1))
        svc.add_income(amount=200.0, source="B", income_date=date(2026, 6, 1))
        all_income = svc.list_income()
        assert len(all_income) == 2

    def test_get_total_income(self, svc):
        svc.add_income(amount=1000.0, source="A")
        svc.add_income(amount=500.0, source="B")
        svc.add_income(amount=200.0, source="C", status=IncomeStatus.CANCELLED)
        total = svc.get_total_income()
        assert total == 1500.0  # cancelled excluded

    def test_get_income_summary(self, svc):
        svc.add_income(amount=1000.0, source="A")
        svc.add_income(amount=500.0, source="A")
        svc.add_income(amount=300.0, source="B")
        summary = svc.get_income_summary()
        assert summary["A"] == 1500.0
        assert summary["B"] == 300.0


# ===== Service Recurring Income Tests =====

class TestRecurringIncomeService:
    def test_add_recurring_income(self, svc):
        ri = svc.add_recurring_income(
            name="Monthly retainer",
            amount=5000.0,
            source="client-A",
            frequency=RecurringFrequency.MONTHLY,
        )
        assert ri.amount == 5000.0
        assert ri.frequency == RecurringFrequency.MONTHLY

    def test_add_recurring_income_zero_rejected(self, svc):
        with pytest.raises(ValueError):
            svc.add_recurring_income(
                name="Test", amount=0, source="A", frequency=RecurringFrequency.MONTHLY,
            )

    def test_process_recurring_income(self, svc):
        past_date = date.today() - timedelta(days=35)
        svc.add_recurring_income(
            name="Monthly",
            amount=1000.0,
            source="client-A",
            frequency=RecurringFrequency.MONTHLY,
            start_date=past_date,
        )
        generated = svc.process_recurring_income()
        assert len(generated) >= 1
        assert generated[0].source == "client-A"
        assert generated[0].recurring_id is not None

    def test_process_recurring_income_not_due(self, svc):
        future_date = date.today() + timedelta(days=30)
        svc.add_recurring_income(
            name="Future",
            amount=1000.0,
            source="client-A",
            frequency=RecurringFrequency.MONTHLY,
            start_date=future_date,
        )
        generated = svc.process_recurring_income()
        assert len(generated) == 0

    def test_pause_and_resume_recurring_income(self, svc):
        ri = svc.add_recurring_income(
            name="Test", amount=100.0, source="A", frequency=RecurringFrequency.MONTHLY,
        )
        paused = svc.pause_recurring_income(ri.id)
        assert paused.active is False
        resumed = svc.resume_recurring_income(ri.id)
        assert resumed.active is True

    def test_pause_recurring_income_not_found(self, svc):
        with pytest.raises(ValueError):
            svc.pause_recurring_income("NONEXIST")

    def test_delete_recurring_income(self, svc):
        ri = svc.add_recurring_income(
            name="Test", amount=100.0, source="A", frequency=RecurringFrequency.MONTHLY,
        )
        assert svc.delete_recurring_income(ri.id) is True

    def test_process_recurring_income_respects_end_date(self, svc):
        past_start = date.today() - timedelta(days=65)
        past_end = date.today() - timedelta(days=30)
        svc.add_recurring_income(
            name="Expired",
            amount=500.0,
            source="client-X",
            frequency=RecurringFrequency.MONTHLY,
            start_date=past_start,
            end_date=past_end,
        )
        generated = svc.process_recurring_income()
        # Should have generated income for the period when it was active
        assert all(g.income_date <= past_end for g in generated)


# ===== Cash Flow Tests =====

class TestCashFlow:
    def test_cash_flow_basic(self, svc):
        svc.add_income(amount=5000.0, source="salary", income_date=date.today())
        svc.add_expense(amount=2000.0, category="rent", expense_date=date.today())
        svc.add_expense(amount=500.0, category="food", expense_date=date.today())
        flow = svc.get_cash_flow()
        assert flow.total_income == 5000.0
        assert flow.total_expenses == 2500.0
        assert flow.net_cash_flow == 2500.0
        assert flow.is_profitable is True
        assert flow.savings_rate == 50.0

    def test_cash_flow_deficit(self, svc):
        svc.add_income(amount=1000.0, source="A", income_date=date.today())
        svc.add_expense(amount=2000.0, category="X", expense_date=date.today())
        flow = svc.get_cash_flow()
        assert flow.net_cash_flow == -1000.0
        assert flow.is_profitable is False

    def test_cash_flow_no_income(self, svc):
        svc.add_expense(amount=500.0, category="X", expense_date=date.today())
        flow = svc.get_cash_flow()
        assert flow.total_income == 0.0
        assert flow.total_expenses == 500.0
        assert flow.is_profitable is False

    def test_cash_flow_no_data(self, svc):
        flow = svc.get_cash_flow()
        assert flow.total_income == 0.0
        assert flow.total_expenses == 0.0
        assert flow.net_cash_flow == 0.0

    def test_cash_flow_with_date_range(self, svc):
        svc.add_income(amount=1000.0, source="A", income_date=date(2026, 1, 15))
        svc.add_income(amount=2000.0, source="B", income_date=date(2026, 6, 15))
        flow = svc.get_cash_flow(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        assert flow.total_income == 2000.0

    def test_cash_flow_excludes_cancelled(self, svc):
        svc.add_income(amount=1000.0, source="A", income_date=date.today())
        svc.add_income(amount=500.0, source="B", income_date=date.today(), status=IncomeStatus.CANCELLED)
        flow = svc.get_cash_flow()
        assert flow.total_income == 1000.0

    def test_cash_flow_largest_source_and_category(self, svc):
        svc.add_income(amount=500.0, source="small", income_date=date.today())
        svc.add_income(amount=3000.0, source="big", income_date=date.today())
        svc.add_expense(amount=200.0, category="minor", expense_date=date.today())
        svc.add_expense(amount=2000.0, category="major", expense_date=date.today())
        flow = svc.get_cash_flow()
        assert flow.largest_income_source == "big"
        assert flow.largest_expense_category == "major"


# ===== Burn Rate Tests =====

class TestBurnRate:
    def test_burn_rate_basic(self, svc):
        # Add some historical expenses and income
        for i in range(3):
            d = date.today() - timedelta(days=30 * (i + 1))
            svc.add_expense(amount=2000.0, category="ops", expense_date=d)
            svc.add_income(amount=1500.0, source="revenue", income_date=d)
        burn = svc.get_burn_rate(months=3)
        assert burn.avg_monthly_burn > 0
        assert burn.avg_monthly_income > 0
        assert burn.net_burn > 0  # spending more than earning
        assert burn.is_sustainable is False

    def test_burn_rate_sustainable(self, svc):
        for i in range(3):
            d = date.today() - timedelta(days=30 * (i + 1))
            svc.add_expense(amount=1000.0, category="ops", expense_date=d)
            svc.add_income(amount=3000.0, source="revenue", income_date=d)
        burn = svc.get_burn_rate(months=3)
        assert burn.net_burn < 0  # earning more than spending
        assert burn.is_sustainable is True
        assert burn.runway_months is None

    def test_burn_rate_with_savings(self, svc):
        # Create savings goal with money
        goal = svc.create_savings_goal(name="Reserve", target_amount=10000.0)
        svc.contribute_to_savings(goal.id, amount=5000.0)

        for i in range(3):
            d = date.today() - timedelta(days=30 * (i + 1))
            svc.add_expense(amount=2000.0, category="ops", expense_date=d)

        burn = svc.get_burn_rate(months=3)
        assert burn.total_savings == 5000.0
        assert burn.runway_months is not None
        assert burn.runway_months > 0

    def test_burn_rate_invalid_months(self, svc):
        with pytest.raises(ValueError):
            svc.get_burn_rate(months=0)

    def test_burn_rate_one_month(self, svc):
        svc.add_expense(amount=500.0, category="X", expense_date=date.today() - timedelta(days=15))
        burn = svc.get_burn_rate(months=1)
        assert burn.analysis_period_months == 1


# ===== Dashboard Tests =====

class TestDashboard:
    def test_dashboard_empty_state(self, svc):
        dashboard = svc.get_financial_dashboard()
        assert dashboard.health_score >= 0
        assert dashboard.health_score <= 100
        assert dashboard.active_budgets == 0
        assert dashboard.active_alerts == 0

    def test_dashboard_with_data(self, svc):
        # Create budget
        budget = svc.create_budget(name="API", limit=1000.0, period="monthly", category="api")
        # Add income
        svc.add_income(amount=5000.0, source="revenue")
        # Add expense
        svc.add_expense(amount=500.0, category="api", budget_id=budget.id)
        # Create savings goal
        goal = svc.create_savings_goal(name="Reserve", target_amount=10000.0)
        svc.contribute_to_savings(goal.id, amount=3000.0)

        dashboard = svc.get_financial_dashboard()
        assert dashboard.active_budgets == 1
        assert dashboard.total_savings > 0
        assert dashboard.monthly_cash_flow is not None
        assert dashboard.burn_rate is not None
        assert 0 <= dashboard.health_score <= 100
        assert dashboard.health_status in ["excellent", "good", "fair", "poor", "critical"]

    def test_dashboard_health_status_categories(self, svc):
        # Healthy scenario: high income, low expenses
        svc.add_income(amount=10000.0, source="revenue")
        svc.add_expense(amount=100.0, category="misc")
        dashboard = svc.get_financial_dashboard()
        assert dashboard.health_score > 40  # Should be at least fair

    def test_dashboard_over_limit_budget(self, svc):
        budget = svc.create_budget(name="Small", limit=100.0, period="monthly", category="test")
        svc.add_expense(amount=200.0, category="test", budget_id=budget.id)
        dashboard = svc.get_financial_dashboard()
        assert dashboard.budgets_over_limit == 1


# ===== Export Tests =====

class TestExportWithIncome:
    def test_export_json_includes_income(self, svc):
        svc.add_income(amount=1000.0, source="A")
        result = svc.export_data(format="json")
        data = json.loads(result)
        assert "incomes" in data
        assert len(data["incomes"]) == 1
        assert "recurring_incomes" in data

    def test_export_csv_includes_income(self, svc):
        svc.add_income(amount=1000.0, source="client-A")
        result = svc.export_data(format="csv")
        lines = result.strip().split("\n")
        income_lines = [l for l in lines if l.startswith("income,")]
        assert len(income_lines) == 1

    def test_export_markdown_includes_income(self, svc):
        svc.add_income(amount=1000.0, source="A", description="Consulting")
        result = svc.export_data(format="markdown")
        assert "## Income" in result


# ===== Integration Tests =====

class TestIntegration:
    def test_full_financial_workflow(self, svc):
        """Test a complete financial workflow: income, expenses, budget, savings, dashboard."""
        # Set up recurring income
        svc.add_recurring_income(
            name="API Revenue",
            amount=2000.0,
            source="api-sales",
            frequency=RecurringFrequency.MONTHLY,
            start_date=date.today() - timedelta(days=65),
        )
        # Process recurring income
        generated = svc.process_recurring_income()
        assert len(generated) >= 2

        # Add one-time income
        svc.add_income(amount=5000.0, source="consulting", description="Big project")

        # Create budget and add expenses
        budget = svc.create_budget(name="Infrastructure", limit=3000.0, period="monthly", category="infra")
        svc.add_expense(amount=1500.0, category="infra", budget_id=budget.id, description="Servers")
        svc.add_expense(amount=800.0, category="infra", budget_id=budget.id, description="CDN")

        # Create savings goal
        goal = svc.create_savings_goal(name="Emergency Fund", target_amount=20000.0)
        svc.contribute_to_savings(goal.id, amount=5000.0)

        # Check cash flow
        flow = svc.get_cash_flow()
        assert flow.is_profitable is True

        # Check dashboard
        dashboard = svc.get_financial_dashboard()
        assert dashboard.active_budgets == 1
        assert dashboard.monthly_cash_flow is not None
        assert dashboard.health_score > 0

    def test_recurring_income_then_cashflow(self, svc):
        """Process recurring income then verify it shows in cash flow."""
        # Start from the beginning of the current month so generated income
        # lands inside the current period that get_cash_flow() defaults to.
        month_start = date.today().replace(day=1)
        svc.add_recurring_income(
            name="Monthly",
            amount=3000.0,
            source="retainer",
            frequency=RecurringFrequency.MONTHLY,
            start_date=month_start,
        )
        svc.process_recurring_income()

        flow = svc.get_cash_flow()
        assert flow.total_income >= 3000.0
