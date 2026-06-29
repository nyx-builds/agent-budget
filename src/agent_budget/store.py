"""JSON file storage for Agent Budget."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .models import (
    Budget, Expense, RecurringExpense, BudgetAlert,
    SavingsGoal, SavingsContribution, SpendingRule, BudgetRollover,
)


class BudgetStore:
    """JSON file-based storage for budgets, expenses, and recurring templates."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or os.environ.get("AGENT_BUDGET_DIR", Path.home() / ".agent-budget"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._budgets_file = self.data_dir / "budgets.json"
        self._expenses_file = self.data_dir / "expenses.json"
        self._recurring_file = self.data_dir / "recurring.json"
        self._alerts_file = self.data_dir / "alerts.json"
        self._savings_file = self.data_dir / "savings.json"
        self._rules_file = self.data_dir / "rules.json"
        self._rollovers_file = self.data_dir / "rollovers.json"
        self._templates_file = self.data_dir / "templates.json"

    # --- JSON helpers ---

    @staticmethod
    def _json_default(obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    def _read_json(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_json(self, path: Path, data: list[dict]) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=self._json_default)

    # --- Budgets ---

    def list_budgets(self, active_only: bool = False) -> list[Budget]:
        data = self._read_json(self._budgets_file)
        budgets = [Budget(**d) for d in data]
        if active_only:
            budgets = [b for b in budgets if b.active]
        return budgets

    def get_budget(self, budget_id: str) -> Optional[Budget]:
        for b in self.list_budgets():
            if b.id == budget_id:
                return b
        return None

    def save_budget(self, budget: Budget) -> Budget:
        budgets = self.list_budgets()
        # Update or add
        found = False
        for i, b in enumerate(budgets):
            if b.id == budget.id:
                budgets[i] = budget
                found = True
                break
        if not found:
            budgets.append(budget)
        self._write_json(self._budgets_file, [b.model_dump() for b in budgets])
        return budget

    def delete_budget(self, budget_id: str) -> bool:
        budgets = self.list_budgets()
        new_budgets = [b for b in budgets if b.id != budget_id]
        if len(new_budgets) == len(budgets):
            return False
        self._write_json(self._budgets_file, [b.model_dump() for b in new_budgets])
        return True

    # --- Expenses ---

    def list_expenses(
        self,
        category: Optional[str] = None,
        budget_id: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        status: Optional[str] = None,
        vendor: Optional[str] = None,
        reimbursable: Optional[bool] = None,
    ) -> list[Expense]:
        data = self._read_json(self._expenses_file)
        expenses = [Expense(**d) for d in data]
        if category:
            expenses = [e for e in expenses if e.category.lower() == category.lower()]
        if budget_id:
            expenses = [e for e in expenses if e.budget_id == budget_id]
        if start_date:
            expenses = [e for e in expenses if e.expense_date >= start_date]
        if end_date:
            expenses = [e for e in expenses if e.expense_date <= end_date]
        if tags:
            expenses = [e for e in expenses if any(t in e.tags for t in tags)]
        if status:
            expenses = [e for e in expenses if e.status.value == status]
        if vendor:
            expenses = [e for e in expenses if e.vendor and e.vendor.lower() == vendor.lower()]
        if reimbursable is not None:
            expenses = [e for e in expenses if e.reimbursable == reimbursable]
        return sorted(expenses, key=lambda e: e.expense_date, reverse=True)

    def get_expense(self, expense_id: str) -> Optional[Expense]:
        for e in self.list_expenses():
            if e.id == expense_id:
                return e
        return None

    def save_expense(self, expense: Expense) -> Expense:
        expenses = self.list_expenses()
        found = False
        for i, e in enumerate(expenses):
            if e.id == expense.id:
                expenses[i] = expense
                found = True
                break
        if not found:
            expenses.append(expense)
        self._write_json(self._expenses_file, [e.model_dump() for e in expenses])
        return expense

    def delete_expense(self, expense_id: str) -> bool:
        expenses = self.list_expenses()
        new_expenses = [e for e in expenses if e.id != expense_id]
        if len(new_expenses) == len(expenses):
            return False
        self._write_json(self._expenses_file, [e.model_dump() for e in new_expenses])
        return True

    # --- Recurring Expenses ---

    def list_recurring_expenses(self, active_only: bool = False) -> list[RecurringExpense]:
        data = self._read_json(self._recurring_file)
        recurring = [RecurringExpense(**d) for d in data]
        if active_only:
            recurring = [r for r in recurring if r.active]
        return recurring

    def get_recurring_expense(self, recurring_id: str) -> Optional[RecurringExpense]:
        for r in self.list_recurring_expenses():
            if r.id == recurring_id:
                return r
        return None

    def save_recurring_expense(self, recurring: RecurringExpense) -> RecurringExpense:
        recurrings = self.list_recurring_expenses()
        found = False
        for i, r in enumerate(recurrings):
            if r.id == recurring.id:
                recurrings[i] = recurring
                found = True
                break
        if not found:
            recurrings.append(recurring)
        self._write_json(self._recurring_file, [r.model_dump() for r in recurrings])
        return recurring

    def delete_recurring_expense(self, recurring_id: str) -> bool:
        recurrings = self.list_recurring_expenses()
        new_recurrings = [r for r in recurrings if r.id != recurring_id]
        if len(new_recurrings) == len(recurrings):
            return False
        self._write_json(self._recurring_file, [r.model_dump() for r in new_recurrings])
        return True

    # --- Alerts ---

    def list_alerts(self, budget_id: Optional[str] = None, unread_only: bool = False) -> list[BudgetAlert]:
        data = self._read_json(self._alerts_file)
        alerts = [BudgetAlert(**d) for d in data]
        if budget_id:
            alerts = [a for a in alerts if a.budget_id == budget_id]
        return sorted(alerts, key=lambda a: a.created_at, reverse=True)

    def save_alert(self, alert: BudgetAlert) -> BudgetAlert:
        alerts = self.list_alerts()
        alerts.append(alert)
        self._write_json(self._alerts_file, [a.model_dump() for a in alerts])
        return alert

    def clear_alerts(self, budget_id: Optional[str] = None) -> int:
        if budget_id:
            alerts = self.list_alerts()
            new_alerts = [a for a in alerts if a.budget_id != budget_id]
            self._write_json(self._alerts_file, [a.model_dump() for a in new_alerts])
            return len(alerts) - len(new_alerts)
        else:
            count = len(self.list_alerts())
            self._write_json(self._alerts_file, [])
            return count

    # --- Savings Goals ---

    def list_savings_goals(self, status: Optional[str] = None) -> list[SavingsGoal]:
        data = self._read_json(self._savings_file)
        goals = [SavingsGoal(**d) for d in data]
        if status:
            goals = [g for g in goals if g.status.value == status]
        return goals

    def get_savings_goal(self, goal_id: str) -> Optional[SavingsGoal]:
        for g in self.list_savings_goals():
            if g.id == goal_id:
                return g
        return None

    def save_savings_goal(self, goal: SavingsGoal) -> SavingsGoal:
        goals = self.list_savings_goals()
        found = False
        for i, g in enumerate(goals):
            if g.id == goal.id:
                goals[i] = goal
                found = True
                break
        if not found:
            goals.append(goal)
        self._write_json(self._savings_file, [g.model_dump() for g in goals])
        return goal

    def delete_savings_goal(self, goal_id: str) -> bool:
        goals = self.list_savings_goals()
        new_goals = [g for g in goals if g.id != goal_id]
        if len(new_goals) == len(goals):
            return False
        self._write_json(self._savings_file, [g.model_dump() for g in new_goals])
        return True

    # --- Spending Rules ---

    def list_spending_rules(self, enabled_only: bool = False) -> list[SpendingRule]:
        data = self._read_json(self._rules_file)
        rules = [SpendingRule(**d) for d in data]
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        return rules

    def get_spending_rule(self, rule_id: str) -> Optional[SpendingRule]:
        for r in self.list_spending_rules():
            if r.id == rule_id:
                return r
        return None

    def save_spending_rule(self, rule: SpendingRule) -> SpendingRule:
        rules = self.list_spending_rules()
        found = False
        for i, r in enumerate(rules):
            if r.id == rule.id:
                rules[i] = rule
                found = True
                break
        if not found:
            rules.append(rule)
        self._write_json(self._rules_file, [r.model_dump() for r in rules])
        return rule

    def delete_spending_rule(self, rule_id: str) -> bool:
        rules = self.list_spending_rules()
        new_rules = [r for r in rules if r.id != rule_id]
        if len(new_rules) == len(rules):
            return False
        self._write_json(self._rules_file, [r.model_dump() for r in new_rules])
        return True

    # --- Rollovers ---

    def list_rollovers(self, budget_id: Optional[str] = None) -> list[BudgetRollover]:
        data = self._read_json(self._rollovers_file)
        rollovers = [BudgetRollover(**d) for d in data]
        if budget_id:
            rollovers = [r for r in rollovers if r.budget_id == budget_id]
        return rollovers

    def save_rollover(self, rollover: BudgetRollover) -> BudgetRollover:
        rollovers = self.list_rollovers()
        rollovers.append(rollover)
        self._write_json(self._rollovers_file, [r.model_dump() for r in rollovers])
        return rollover

    def get_latest_rollover(self, budget_id: str) -> Optional[BudgetRollover]:
        """Get the most recent rollover for a budget."""
        rollovers = self.list_rollovers(budget_id=budget_id)
        if not rollovers:
            return None
        return sorted(rollovers, key=lambda r: r.to_period_start, reverse=True)[0]

    # --- Budget Templates ---

    def list_budget_templates(self) -> list:
        """List custom budget templates (built-ins are in models.py)."""
        from .models import BudgetTemplate
        data = self._read_json(self._templates_file)
        return [BudgetTemplate(**d) for d in data]

    def get_budget_template(self, template_id: str) -> Optional:
        """Get a custom budget template by ID."""
        for t in self.list_budget_templates():
            if t.id == template_id:
                return t
        return None

    def save_budget_template(self, template) -> object:
        """Save a budget template."""
        from .models import BudgetTemplate
        templates = self.list_budget_templates()
        found = False
        for i, t in enumerate(templates):
            if t.id == template.id:
                templates[i] = template
                found = True
                break
        if not found:
            templates.append(template)
        self._write_json(self._templates_file, [t.model_dump() for t in templates])
        return template

    def delete_budget_template(self, template_id: str) -> bool:
        """Delete a custom budget template."""
        templates = self.list_budget_templates()
        new_templates = [t for t in templates if t.id != template_id]
        if len(new_templates) == len(templates):
            return False
        self._write_json(self._templates_file, [t.model_dump() for t in new_templates])
        return True
