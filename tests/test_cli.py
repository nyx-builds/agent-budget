"""Tests for Agent Budget CLI."""

import pytest
from click.testing import CliRunner
from datetime import date

from agent_budget.cli import main


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """Create a CLI runner with a temp data directory."""
    runner = CliRunner()
    monkeypatch.setenv("AGENT_BUDGET_DIR", str(tmp_path / "data"))
    return runner


class TestBudgetCLI:
    def test_budget_create(self, runner):
        result = runner.invoke(main, ["budget", "create", "API Costs", "--limit", "500", "--period", "monthly"])
        assert result.exit_code == 0
        assert "Budget created" in result.output
        assert "API Costs" in result.output

    def test_budget_create_with_category(self, runner):
        result = runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly", "--category", "api"])
        assert result.exit_code == 0
        assert "API" in result.output

    def test_budget_create_with_rollover(self, runner):
        result = runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly", "--rollover"])
        assert result.exit_code == 0
        assert "Rollover" in result.output

    def test_budget_list(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        result = runner.invoke(main, ["budget", "list"])
        assert result.exit_code == 0
        assert "API" in result.output

    def test_budget_list_empty(self, runner):
        result = runner.invoke(main, ["budget", "list"])
        assert result.exit_code == 0
        assert "No budgets found" in result.output

    def test_budget_status(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        result = runner.invoke(main, ["budget", "status"])
        assert result.exit_code == 0
        assert "API" in result.output

    def test_budget_update(self, runner):
        result = runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        lines = result.output.split("\n")
        budget_id = None
        for line in lines:
            if "BUD-" in line:
                start = line.index("BUD-")
                budget_id = line[start:start + 12]
                break
        assert budget_id is not None
        result = runner.invoke(main, ["budget", "update", budget_id, "--name", "Updated API"])
        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_budget_delete(self, runner):
        result = runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        lines = result.output.split("\n")
        budget_id = None
        for line in lines:
            if "BUD-" in line:
                start = line.index("BUD-")
                budget_id = line[start:start + 12]
                break
        result = runner.invoke(main, ["budget", "delete", budget_id], input="y")
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_budget_rollover(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly", "--rollover"])
        result = runner.invoke(main, ["budget", "rollover"])
        assert result.exit_code == 0


class TestExpenseCLI:
    def test_expense_add(self, runner):
        result = runner.invoke(main, ["expense", "add", "50", "--category", "api", "--description", "Test expense"])
        assert result.exit_code == 0
        assert "Expense logged" in result.output

    def test_expense_add_with_date(self, runner):
        result = runner.invoke(main, ["expense", "add", "50", "--category", "api", "--date", "2026-06-01"])
        assert result.exit_code == 0

    def test_expense_add_with_tags(self, runner):
        result = runner.invoke(main, ["expense", "add", "50", "--category", "api", "--tags", "gpt4,production"])
        assert result.exit_code == 0

    def test_expense_add_with_vendor(self, runner):
        result = runner.invoke(main, ["expense", "add", "50", "--category", "api", "--vendor", "OpenAI", "--reimbursable"])
        assert result.exit_code == 0
        assert "Vendor" in result.output or "Reimbursable" in result.output

    def test_expense_update(self, runner):
        result = runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        lines = result.output.split("\n")
        expense_id = None
        for line in lines:
            if "EXP-" in line:
                start = line.index("EXP-")
                expense_id = line[start:start + 12]
                break
        assert expense_id is not None
        result = runner.invoke(main, ["expense", "update", expense_id, "--amount", "75", "--vendor", "AWS"])
        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_expense_list(self, runner):
        runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        result = runner.invoke(main, ["expense", "list"])
        assert result.exit_code == 0

    def test_expense_list_this_month(self, runner):
        runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        result = runner.invoke(main, ["expense", "list", "--this-month"])
        assert result.exit_code == 0

    def test_expense_list_empty(self, runner):
        result = runner.invoke(main, ["expense", "list"])
        assert result.exit_code == 0
        assert "No expenses found" in result.output

    def test_expense_list_reimbursable(self, runner):
        runner.invoke(main, ["expense", "add", "50", "--category", "api", "--reimbursable"])
        runner.invoke(main, ["expense", "add", "100", "--category", "api"])
        result = runner.invoke(main, ["expense", "list", "--reimbursable"])
        assert result.exit_code == 0


class TestSavingsCLI:
    def test_savings_create(self, runner):
        result = runner.invoke(main, ["savings", "create", "Emergency Fund", "--target", "10000"])
        assert result.exit_code == 0
        assert "Savings goal created" in result.output

    def test_savings_create_with_target_date(self, runner):
        result = runner.invoke(main, ["savings", "create", "Vacation", "--target", "3000", "--target-date", "2026-12-31"])
        assert result.exit_code == 0

    def test_savings_list(self, runner):
        runner.invoke(main, ["savings", "create", "Emergency Fund", "--target", "10000"])
        result = runner.invoke(main, ["savings", "list"])
        assert result.exit_code == 0
        assert "Savings Goals" in result.output

    def test_savings_list_empty(self, runner):
        result = runner.invoke(main, ["savings", "list"])
        assert result.exit_code == 0
        assert "No savings goals found" in result.output

    def test_savings_contribute(self, runner):
        result = runner.invoke(main, ["savings", "create", "Test", "--target", "1000"])
        lines = result.output.split("\n")
        goal_id = None
        for line in lines:
            if "SAV-" in line:
                start = line.index("SAV-")
                goal_id = line[start:start + 12]
                break
        assert goal_id is not None
        result = runner.invoke(main, ["savings", "contribute", goal_id, "--amount", "250"])
        assert result.exit_code == 0
        assert "Contribution" in result.output or "25%" in result.output

    def test_savings_withdraw(self, runner):
        result = runner.invoke(main, ["savings", "create", "Test", "--target", "1000"])
        lines = result.output.split("\n")
        goal_id = None
        for line in lines:
            if "SAV-" in line:
                start = line.index("SAV-")
                goal_id = line[start:start + 12]
                break
        runner.invoke(main, ["savings", "contribute", goal_id, "--amount", "500"])
        result = runner.invoke(main, ["savings", "withdraw", goal_id, "--amount", "200"])
        assert result.exit_code == 0

    def test_savings_pause_resume(self, runner):
        result = runner.invoke(main, ["savings", "create", "Test", "--target", "1000"])
        lines = result.output.split("\n")
        goal_id = None
        for line in lines:
            if "SAV-" in line:
                start = line.index("SAV-")
                goal_id = line[start:start + 12]
                break
        result = runner.invoke(main, ["savings", "pause", goal_id])
        assert result.exit_code == 0
        result = runner.invoke(main, ["savings", "resume", goal_id])
        assert result.exit_code == 0

    def test_savings_delete(self, runner):
        result = runner.invoke(main, ["savings", "create", "Test", "--target", "1000"])
        lines = result.output.split("\n")
        goal_id = None
        for line in lines:
            if "SAV-" in line:
                start = line.index("SAV-")
                goal_id = line[start:start + 12]
                break
        result = runner.invoke(main, ["savings", "delete", goal_id], input="y")
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()


class TestSpendingRuleCLI:
    def test_rule_add(self, runner):
        result = runner.invoke(main, ["rule", "add", "API Cap", "--category", "api", "--action", "block", "--threshold-amount", "500"])
        assert result.exit_code == 0
        assert "Spending rule created" in result.output

    def test_rule_add_with_approval(self, runner):
        result = runner.invoke(main, ["rule", "add", "API Approval", "--category", "api", "--action", "block", "--approval-above", "100"])
        assert result.exit_code == 0

    def test_rule_list(self, runner):
        runner.invoke(main, ["rule", "add", "API Cap", "--category", "api", "--action", "block", "--threshold-amount", "500"])
        result = runner.invoke(main, ["rule", "list"])
        assert result.exit_code == 0
        assert "API Cap" in result.output

    def test_rule_list_empty(self, runner):
        result = runner.invoke(main, ["rule", "list"])
        assert result.exit_code == 0
        assert "No spending rules found" in result.output

    def test_rule_check(self, runner):
        runner.invoke(main, ["rule", "add", "API Cap", "--category", "api", "--action", "block", "--approval-above", "100"])
        result = runner.invoke(main, ["rule", "check", "--amount", "50", "--category", "api"])
        assert result.exit_code == 0

    def test_rule_delete(self, runner):
        result = runner.invoke(main, ["rule", "add", "Test", "--category", "api", "--action", "warn"])
        lines = result.output.split("\n")
        rule_id = None
        for line in lines:
            if "RUL-" in line:
                start = line.index("RUL-")
                rule_id = line[start:start + 12]
                break
        result = runner.invoke(main, ["rule", "delete", rule_id], input="y")
        assert result.exit_code == 0


class TestRecurringCLI:
    def test_recurring_add(self, runner):
        result = runner.invoke(main, ["recurring", "add", "AWS", "--amount", "99", "--category", "infra", "--frequency", "monthly"])
        assert result.exit_code == 0
        assert "Recurring expense created" in result.output

    def test_recurring_list(self, runner):
        runner.invoke(main, ["recurring", "add", "AWS", "--amount", "99", "--category", "infra", "--frequency", "monthly"])
        result = runner.invoke(main, ["recurring", "list"])
        assert result.exit_code == 0
        assert "AWS" in result.output

    def test_recurring_process(self, runner):
        runner.invoke(main, ["recurring", "add", "AWS", "--amount", "99", "--category", "infra", "--frequency", "monthly"])
        result = runner.invoke(main, ["recurring", "process"])
        assert result.exit_code == 0


class TestAnalysisCLI:
    def test_compare(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        result = runner.invoke(main, ["compare"])
        assert result.exit_code == 0

    def test_forecast(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        result = runner.invoke(main, ["forecast"])
        assert result.exit_code == 0

    def test_summary(self, runner):
        runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        runner.invoke(main, ["expense", "add", "100", "--category", "infra"])
        result = runner.invoke(main, ["summary"])
        assert result.exit_code == 0
        assert "Grand Total" in result.output

    def test_alerts(self, runner):
        result = runner.invoke(main, ["alerts"])
        assert result.exit_code == 0


class TestExportCLI:
    def test_export_json(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        result = runner.invoke(main, ["export", "--format", "json"])
        assert result.exit_code == 0

    def test_export_csv(self, runner):
        runner.invoke(main, ["expense", "add", "50", "--category", "api"])
        result = runner.invoke(main, ["export", "--format", "csv"])
        assert result.exit_code == 0

    def test_export_markdown(self, runner):
        runner.invoke(main, ["budget", "create", "API", "--limit", "500", "--period", "monthly"])
        result = runner.invoke(main, ["export", "--format", "markdown"])
        assert result.exit_code == 0


class TestCurrenciesCLI:
    def test_currencies(self, runner):
        result = runner.invoke(main, ["currencies"])
        assert result.exit_code == 0
        assert "USD" in result.output
        assert "EUR" in result.output
