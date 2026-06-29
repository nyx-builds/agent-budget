"""Tests for v0.3.0 features — CSV import, spending analytics, budget templates."""
import csv
import pytest
from datetime import date, timedelta
from pathlib import Path

from agent_budget.models import (
    BudgetPeriod, RecurringFrequency, AlertLevel, AlertThreshold,
    SpendingRuleAction, SavingsGoalStatus, TrendDirection,
    BudgetTemplate, BUILTIN_BUDGET_TEMPLATES,
)
from agent_budget.service import BudgetService
from agent_budget.store import BudgetStore


@pytest.fixture
def svc(tmp_path):
    return BudgetService(BudgetStore(data_dir=str(tmp_path)))


# --- CSV Import ---

class TestCSVImport:
    def _write_csv(self, path: Path, rows: list[dict], fieldnames: list[str] | None = None):
        """Helper to write a CSV file."""
        if not fieldnames:
            fieldnames = list(rows[0].keys()) if rows else []
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_basic_import(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "description": "OpenAI GPT-4"},
            {"date": "2026-06-02", "amount": "25.50", "category": "infra", "description": "AWS EC2"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 2
        assert result.skipped == 0
        assert result.total_amount == 75.50
        assert len(result.expense_ids) == 2

    def test_import_with_vendor(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "vendor": "OpenAI"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.vendor == "OpenAI"

    def test_import_with_default_category(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "description": "Some expense"},
        ], fieldnames=["date", "amount", "description"])
        result = svc.import_csv(str(csv_file), category="uncategorized")
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.category == "uncategorized"

    def test_import_skip_empty_amount(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "", "category": "api"},
            {"date": "2026-06-02", "amount": "50.00", "category": "api"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        assert result.skipped == 1

    def test_import_skip_negative_amount(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "-50.00", "category": "api"},
            {"date": "2026-06-02", "amount": "50.00", "category": "api"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1

    def test_import_currency_formatting(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "$1,250.00", "category": "api"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.amount == 1250.00

    def test_import_duplicate_detection(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "description": "Test"},
        ])
        # Import once
        result1 = svc.import_csv(str(csv_file))
        assert result1.imported == 1

        # Import same file again — should skip duplicates
        result2 = svc.import_csv(str(csv_file))
        assert result2.imported == 0
        assert result2.skipped == 1

    def test_import_no_dedup(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "description": "Test"},
        ])
        result1 = svc.import_csv(str(csv_file), skip_duplicates=False)
        result2 = svc.import_csv(str(csv_file), skip_duplicates=False)
        assert result1.imported == 1
        assert result2.imported == 1

    def test_import_date_formats(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "06/15/2026", "amount": "50.00", "category": "api"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.expense_date == date(2026, 6, 15)

    def test_import_missing_date_uses_today(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"amount": "50.00", "category": "api"},
        ], fieldnames=["amount", "category"])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.expense_date == date.today()

    def test_import_with_tags(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        # Tags with commas need quoting in CSV
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "amount", "category", "tags"])
            writer.writeheader()
            writer.writerow({"date": "2026-06-01", "amount": "50.00", "category": "api", "tags": "gpt4;production"})
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert "gpt4;production" in expense.tags or any("gpt4" in t or "production" in t for t in expense.tags)

    def test_import_with_budget_id(self, svc, tmp_path):
        budget = svc.create_budget(name="API", limit=500, period=BudgetPeriod.MONTHLY, category="api")
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api"},
        ])
        result = svc.import_csv(str(csv_file), budget_id=budget.id)
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.budget_id == budget.id

    def test_import_file_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.import_csv("/nonexistent/file.csv")

    def test_import_multiple_rows(self, svc, tmp_path):
        csv_file = tmp_path / "expenses.csv"
        rows = [
            {"date": f"2026-06-{i:02d}", "amount": f"{i * 10}.00", "category": "api", "description": f"Expense {i}"}
            for i in range(1, 11)
        ]
        self._write_csv(csv_file, rows)
        result = svc.import_csv(str(csv_file))
        assert result.imported == 10
        assert result.total_amount == sum(i * 10 for i in range(1, 11))

    def test_import_with_memo_column(self, svc, tmp_path):
        """Test that 'memo' maps to description."""
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "memo": "GPT-4 call"},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.description == "GPT-4 call"

    def test_import_with_payee_column(self, svc, tmp_path):
        """Test that 'payee' maps to vendor."""
        csv_file = tmp_path / "expenses.csv"
        self._write_csv(csv_file, [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "payee": "OpenAI Inc."},
        ])
        result = svc.import_csv(str(csv_file))
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.vendor == "OpenAI Inc."


# --- Spending Trends ---

class TestSpendingTrends:
    def test_trends_with_data(self, svc):
        today = date.today()
        # Current period expenses
        svc.add_expense(amount=300, category="api", expense_date=today)
        svc.add_expense(amount=200, category="infra", expense_date=today)
        # Previous period expenses
        prev_month = today - timedelta(days=35)
        svc.add_expense(amount=200, category="api", expense_date=prev_month)
        svc.add_expense(amount=150, category="infra", expense_date=prev_month)

        trends = svc.get_spending_trends()
        assert len(trends) >= 2
        api_trend = next(t for t in trends if t.category == "api")
        assert api_trend.direction == TrendDirection.UP
        assert api_trend.change_amount == 100.0

    def test_trends_single_category(self, svc):
        today = date.today()
        svc.add_expense(amount=300, category="api", expense_date=today)
        svc.add_expense(amount=200, category="api", expense_date=today - timedelta(days=35))

        trends = svc.get_spending_trends(category="api")
        assert len(trends) == 1
        assert trends[0].category == "api"

    def test_trends_flat(self, svc):
        today = date.today()
        svc.add_expense(amount=100, category="api", expense_date=today)
        svc.add_expense(amount=99, category="api", expense_date=today - timedelta(days=35))

        trends = svc.get_spending_trends()
        api_trend = next(t for t in trends if t.category == "api")
        assert api_trend.direction == TrendDirection.FLAT

    def test_trends_decrease(self, svc):
        today = date.today()
        svc.add_expense(amount=100, category="api", expense_date=today)
        svc.add_expense(amount=300, category="api", expense_date=today - timedelta(days=35))

        trends = svc.get_spending_trends()
        api_trend = next(t for t in trends if t.category == "api")
        assert api_trend.direction == TrendDirection.DOWN

    def test_trends_weekly(self, svc):
        today = date.today()
        svc.add_expense(amount=100, category="api", expense_date=today)
        svc.add_expense(amount=50, category="api", expense_date=today - timedelta(days=8))

        trends = svc.get_spending_trends(period_type="weekly")
        assert len(trends) >= 1

    def test_trends_new_category(self, svc):
        today = date.today()
        # Only current period
        svc.add_expense(amount=100, category="new_cat", expense_date=today)

        trends = svc.get_spending_trends()
        new_cat_trend = next((t for t in trends if t.category == "new_cat"), None)
        assert new_cat_trend is not None
        assert new_cat_trend.direction == TrendDirection.UP
        assert new_cat_trend.change_percent == 100.0

    def test_trends_empty(self, svc):
        trends = svc.get_spending_trends()
        assert trends == []


# --- Category Breakdown ---

class TestCategoryBreakdown:
    def test_breakdown_basic(self, svc):
        svc.add_expense(amount=100, category="api", vendor="OpenAI")
        svc.add_expense(amount=200, category="api", vendor="Anthropic")
        svc.add_expense(amount=50, category="infra", vendor="AWS")

        breakdowns = svc.get_category_breakdown()
        assert len(breakdowns) == 2
        api = next(b for b in breakdowns if b.category == "api")
        assert api.total == 300.0
        assert api.count == 2
        assert api.average == 150.0
        assert api.largest_expense == 200.0
        assert "OpenAI" in api.vendors
        assert "Anthropic" in api.vendors

    def test_breakdown_percentages(self, svc):
        svc.add_expense(amount=100, category="api")
        svc.add_expense(amount=300, category="infra")

        breakdowns = svc.get_category_breakdown()
        infra = next(b for b in breakdowns if b.category == "infra")
        assert infra.percentage == 75.0

    def test_breakdown_top_n(self, svc):
        for i in range(5):
            svc.add_expense(amount=100, category=f"cat_{i}")

        breakdowns = svc.get_category_breakdown(top_n=3)
        assert len(breakdowns) == 3

    def test_breakdown_empty(self, svc):
        breakdowns = svc.get_category_breakdown()
        assert breakdowns == []

    def test_breakdown_excludes_cancelled(self, svc):
        expense = svc.add_expense(amount=100, category="api")
        svc.update_expense(expense.id, status="cancelled")
        svc.add_expense(amount=50, category="infra")

        breakdowns = svc.get_category_breakdown()
        assert len(breakdowns) == 1
        assert breakdowns[0].category == "infra"


# --- Period Comparison ---

class TestPeriodComparison:
    def test_compare_basic(self, svc):
        svc.add_expense(amount=200, category="api", expense_date=date(2026, 5, 15))
        svc.add_expense(amount=100, category="infra", expense_date=date(2026, 5, 20))
        svc.add_expense(amount=300, category="api", expense_date=date(2026, 6, 15))
        svc.add_expense(amount=150, category="infra", expense_date=date(2026, 6, 20))

        result = svc.compare_periods(
            period_a_start=date(2026, 5, 1),
            period_a_end=date(2026, 5, 31),
            period_b_start=date(2026, 6, 1),
            period_b_end=date(2026, 6, 30),
        )
        assert result.period_a_total == 300.0
        assert result.period_b_total == 450.0
        assert result.change_amount == 150.0
        assert result.direction == TrendDirection.UP

    def test_compare_category_trends(self, svc):
        svc.add_expense(amount=100, category="api", expense_date=date(2026, 5, 15))
        svc.add_expense(amount=200, category="api", expense_date=date(2026, 6, 15))

        result = svc.compare_periods(
            period_a_start=date(2026, 5, 1),
            period_a_end=date(2026, 5, 31),
            period_b_start=date(2026, 6, 1),
            period_b_end=date(2026, 6, 30),
        )
        assert len(result.category_trends) >= 1
        api_trend = next(t for t in result.category_trends if t.category == "api")
        assert api_trend.direction == TrendDirection.UP

    def test_compare_no_change(self, svc):
        svc.add_expense(amount=100, category="api", expense_date=date(2026, 5, 15))
        svc.add_expense(amount=100, category="api", expense_date=date(2026, 6, 15))

        result = svc.compare_periods(
            period_a_start=date(2026, 5, 1),
            period_a_end=date(2026, 5, 31),
            period_b_start=date(2026, 6, 1),
            period_b_end=date(2026, 6, 30),
        )
        assert result.direction == TrendDirection.FLAT

    def test_compare_empty_periods(self, svc):
        result = svc.compare_periods(
            period_a_start=date(2026, 1, 1),
            period_a_end=date(2026, 1, 31),
            period_b_start=date(2026, 2, 1),
            period_b_end=date(2026, 2, 28),
        )
        assert result.period_a_total == 0.0
        assert result.period_b_total == 0.0
        assert result.change_amount == 0.0


# --- Budget Templates ---

class TestBudgetTemplates:
    def test_list_builtin_templates(self, svc):
        templates = svc.list_budget_templates()
        assert len(templates) >= 6  # We have 6 built-in templates
        builtin = [t for t in templates if t.is_builtin]
        assert len(builtin) == 6

    def test_list_templates_by_category(self, svc):
        templates = svc.list_budget_templates(category="api")
        assert len(templates) >= 1
        for t in templates:
            assert t.category.lower() == "api" or t.category == "all"

    def test_get_template(self, svc):
        template = svc.get_budget_template("TPL-API001")
        assert template is not None
        assert template.name == "API Costs"
        assert template.category == "api"

    def test_get_template_not_found(self, svc):
        template = svc.get_budget_template("TPL-NONEXISTENT")
        assert template is None

    def test_create_custom_template(self, svc):
        template = svc.create_budget_template(
            name="My Custom Budget",
            category="custom",
            default_limit=250.0,
            period=BudgetPeriod.MONTHLY,
            description="A custom template for testing",
        )
        assert template.name == "My Custom Budget"
        assert template.category == "custom"
        assert template.default_limit == 250.0
        assert template.is_builtin is False

        # Should appear in listing
        templates = svc.list_budget_templates()
        custom = [t for t in templates if not t.is_builtin]
        assert len(custom) >= 1

    def test_instantiate_template(self, svc):
        budget = svc.instantiate_budget_template("TPL-API001")
        assert budget.name == "API Costs"
        assert budget.limit == 500.0
        assert budget.period == BudgetPeriod.MONTHLY
        assert budget.category == "api"

        # Should have alerts configured
        assert len(budget.alert_thresholds) >= 2  # Template has 3 suggested alerts

        # Should have spending rules created
        rules = svc.list_spending_rules()
        api_rules = [r for r in rules if r.budget_id == budget.id]
        assert len(api_rules) >= 1

    def test_instantiate_template_with_overrides(self, svc):
        budget = svc.instantiate_budget_template(
            "TPL-API001",
            name="My API Budget",
            limit=750.0,
            currency="EUR",
        )
        assert budget.name == "My API Budget"
        assert budget.limit == 750.0
        assert budget.currency == "EUR"

    def test_instantiate_template_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.instantiate_budget_template("TPL-NONEXISTENT")

    def test_instantiate_full_agent_stack_template(self, svc):
        budget = svc.instantiate_budget_template("TPL-AGENT")
        assert budget.name == "Full Agent Stack"
        assert budget.limit == 2000.0
        assert budget.category is None  # "all" maps to None

    def test_create_and_instantiate_custom_template(self, svc):
        # Create a custom template
        template = svc.create_budget_template(
            name="Monitoring",
            category="monitoring",
            default_limit=150.0,
            period=BudgetPeriod.MONTHLY,
            description="Monitoring and observability budget",
        )
        # Instantiate it
        budget = svc.instantiate_budget_template(template.id)
        assert budget.name == "Monitoring"
        assert budget.limit == 150.0
        assert budget.category == "monitoring"


# --- Integration: CSV Import + Analytics ---

class TestImportAndAnalytics:
    def test_import_then_trends(self, svc, tmp_path):
        """Test that imported expenses show up in trends."""
        csv_file = tmp_path / "expenses.csv"
        today = date.today()
        prev_month = today - timedelta(days=35)

        rows = [
            {"date": str(today), "amount": "300.00", "category": "api", "description": "Current month"},
            {"date": str(prev_month), "amount": "200.00", "category": "api", "description": "Previous month"},
        ]
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "amount", "category", "description"])
            writer.writeheader()
            writer.writerows(rows)

        result = svc.import_csv(str(csv_file))
        assert result.imported == 2

        trends = svc.get_spending_trends()
        api_trend = next((t for t in trends if t.category == "api"), None)
        assert api_trend is not None
        assert api_trend.current_period_spending >= 300.0

    def test_import_then_breakdown(self, svc, tmp_path):
        """Test that imported expenses show up in breakdown."""
        csv_file = tmp_path / "expenses.csv"
        rows = [
            {"date": "2026-06-01", "amount": "100.00", "category": "api", "vendor": "OpenAI"},
            {"date": "2026-06-02", "amount": "200.00", "category": "infra", "vendor": "AWS"},
            {"date": "2026-06-03", "amount": "50.00", "category": "api", "vendor": "Anthropic"},
        ]
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "amount", "category", "vendor"])
            writer.writeheader()
            writer.writerows(rows)

        result = svc.import_csv(str(csv_file))
        assert result.imported == 3

        breakdowns = svc.get_category_breakdown(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        api = next((b for b in breakdowns if b.category == "api"), None)
        assert api is not None
        assert api.total == 150.0
        assert api.count == 2
        assert len(api.vendors) == 2

    def test_import_then_template_instantiate(self, svc, tmp_path):
        """Test workflow: import CSV, create budget from template, assign budget."""
        # Create budget from template
        budget = svc.instantiate_budget_template("TPL-API001")

        # Import CSV with budget_id
        csv_file = tmp_path / "expenses.csv"
        rows = [
            {"date": "2026-06-01", "amount": "50.00", "category": "api", "description": "API call"},
        ]
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "amount", "category", "description"])
            writer.writeheader()
            writer.writerows(rows)

        result = svc.import_csv(str(csv_file), budget_id=budget.id)
        assert result.imported == 1
        expense = svc.get_expense(result.expense_ids[0])
        assert expense.budget_id == budget.id
