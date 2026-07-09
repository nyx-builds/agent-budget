"""JSON file storage for Agent Budget."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .models import (
    Budget, Expense, RecurringExpense, BudgetAlert,
    SavingsGoal, SavingsContribution, SpendingRule, BudgetRollover,
    Income, RecurringIncome, IncomeStatus,
    CostGuardrail, KillSwitch, CostAlertEvent,
    LoopDetectionConfig,
    SpendReservation, ReservationStatus,
    SpendAnomalyRule, AnomalyEvent,
)
from .llm_costs import LLMUsageRecord, ModelPrice, ModelProvider


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
        self._income_file = self.data_dir / "income.json"
        self._recurring_income_file = self.data_dir / "recurring_income.json"
        self._llm_usage_file = self.data_dir / "llm_usage.json"
        self._llm_prices_file = self.data_dir / "llm_prices.json"
        self._guardrails_file = self.data_dir / "guardrails.json"
        self._killswitch_file = self.data_dir / "killswitch.json"
        self._cost_alerts_file = self.data_dir / "cost_alerts.json"
        self._loop_configs_file = self.data_dir / "loop_configs.json"
        self._webhooks_file = self.data_dir / "webhooks.json"
        self._webhook_deliveries_file = self.data_dir / "webhook_deliveries.json"
        self._reservations_file = self.data_dir / "reservations.json"
        self._anomaly_rules_file = self.data_dir / "anomaly_rules.json"
        self._anomaly_events_file = self.data_dir / "anomaly_events.json"
        # v0.9.0: Process-wide lock so concurrent guardrail check+reserve
        # operations are atomic within a single Python process.  This is the
        # mechanism that makes the reserve/settle protocol hold under fan-out.
        self._lock = threading.RLock()

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

    # --- Income ---

    def list_income(
        self,
        source: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        status: Optional[str] = None,
    ) -> list[Income]:
        data = self._read_json(self._income_file)
        incomes = [Income(**d) for d in data]
        if source:
            incomes = [i for i in incomes if i.source.lower() == source.lower()]
        if start_date:
            incomes = [i for i in incomes if i.income_date >= start_date]
        if end_date:
            incomes = [i for i in incomes if i.income_date <= end_date]
        if tags:
            incomes = [i for i in incomes if any(t in i.tags for t in tags)]
        if status:
            incomes = [i for i in incomes if i.status.value == status]
        return sorted(incomes, key=lambda i: i.income_date, reverse=True)

    def get_income(self, income_id: str) -> Optional[Income]:
        for i in self.list_income():
            if i.id == income_id:
                return i
        return None

    def save_income(self, income: Income) -> Income:
        incomes = self.list_income()
        found = False
        for idx, i in enumerate(incomes):
            if i.id == income.id:
                incomes[idx] = income
                found = True
                break
        if not found:
            incomes.append(income)
        self._write_json(self._income_file, [i.model_dump() for i in incomes])
        return income

    def delete_income(self, income_id: str) -> bool:
        incomes = self.list_income()
        new_incomes = [i for i in incomes if i.id != income_id]
        if len(new_incomes) == len(incomes):
            return False
        self._write_json(self._income_file, [i.model_dump() for i in new_incomes])
        return True

    # --- Recurring Income ---

    def list_recurring_income(self, active_only: bool = False) -> list[RecurringIncome]:
        data = self._read_json(self._recurring_income_file)
        recurring = [RecurringIncome(**d) for d in data]
        if active_only:
            recurring = [r for r in recurring if r.active]
        return recurring

    def get_recurring_income(self, recurring_id: str) -> Optional[RecurringIncome]:
        for r in self.list_recurring_income():
            if r.id == recurring_id:
                return r
        return None

    def save_recurring_income(self, recurring: RecurringIncome) -> RecurringIncome:
        recurrings = self.list_recurring_income()
        found = False
        for idx, r in enumerate(recurrings):
            if r.id == recurring.id:
                recurrings[idx] = recurring
                found = True
                break
        if not found:
            recurrings.append(recurring)
        self._write_json(self._recurring_income_file, [r.model_dump() for r in recurrings])
        return recurring

    def delete_recurring_income(self, recurring_id: str) -> bool:
        recurrings = self.list_recurring_income()
        new_recurrings = [r for r in recurrings if r.id != recurring_id]
        if len(new_recurrings) == len(recurrings):
            return False
        self._write_json(self._recurring_income_file, [r.model_dump() for r in new_recurrings])
        return True

    # --- LLM Usage Records ---

    def list_llm_usage(
        self,
        model_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: Optional[int] = None,
    ) -> list[LLMUsageRecord]:
        """List LLM usage records with optional filters."""
        data = self._read_json(self._llm_usage_file)
        records = [LLMUsageRecord(**d) for d in data]
        if model_id:
            records = [r for r in records if r.model_id == model_id]
        if agent_id:
            records = [r for r in records if r.agent_id == agent_id]
        if from_date:
            records = [r for r in records if r.recorded_at.date() >= from_date]
        if to_date:
            records = [r for r in records if r.recorded_at.date() <= to_date]
        records.sort(key=lambda r: r.recorded_at, reverse=True)
        if limit:
            records = records[:limit]
        return records

    def get_llm_usage(self, record_id: str) -> Optional[LLMUsageRecord]:
        for r in self.list_llm_usage():
            if r.id == record_id:
                return r
        return None

    def save_llm_usage(self, record: LLMUsageRecord) -> LLMUsageRecord:
        records = self.list_llm_usage()
        found = False
        for idx, r in enumerate(records):
            if r.id == record.id:
                records[idx] = record
                found = True
                break
        if not found:
            records.append(record)
        self._write_json(self._llm_usage_file, [r.model_dump() for r in records])
        return record

    def delete_llm_usage(self, record_id: str) -> bool:
        records = self.list_llm_usage()
        new_records = [r for r in records if r.id != record_id]
        if len(new_records) == len(records):
            return False
        self._write_json(self._llm_usage_file, [r.model_dump() for r in new_records])
        return True

    # --- Custom Model Prices ---

    def list_custom_prices(self) -> list[ModelPrice]:
        data = self._read_json(self._llm_prices_file)
        return [ModelPrice(**d) for d in data]

    def save_custom_price(self, price: ModelPrice) -> ModelPrice:
        prices = self.list_custom_prices()
        found = False
        for idx, p in enumerate(prices):
            if p.model_id == price.model_id:
                prices[idx] = price
                found = True
                break
        if not found:
            prices.append(price)
        self._write_json(self._llm_prices_file, [p.model_dump() for p in prices])
        return price

    def delete_custom_price(self, model_id: str) -> bool:
        prices = self.list_custom_prices()
        new_prices = [p for p in prices if p.model_id != model_id]
        if len(new_prices) == len(prices):
            return False
        self._write_json(self._llm_prices_file, [p.model_dump() for p in new_prices])
        return True

    # --- Cost Guardrails (v0.5.0) ---

    def list_guardrails(self, enabled_only: bool = False) -> list[CostGuardrail]:
        data = self._read_json(self._guardrails_file)
        guardrails = [CostGuardrail(**d) for d in data]
        if enabled_only:
            guardrails = [g for g in guardrails if g.enabled]
        return sorted(guardrails, key=lambda g: g.priority, reverse=True)

    def get_guardrail(self, guardrail_id: str) -> Optional[CostGuardrail]:
        for g in self.list_guardrails():
            if g.id == guardrail_id:
                return g
        return None

    def save_guardrail(self, guardrail: CostGuardrail) -> CostGuardrail:
        guardrails = self.list_guardrails()
        found = False
        for idx, g in enumerate(guardrails):
            if g.id == guardrail.id:
                guardrails[idx] = guardrail
                found = True
                break
        if not found:
            guardrails.append(guardrail)
        self._write_json(self._guardrails_file, [g.model_dump() for g in guardrails])
        return guardrail

    def delete_guardrail(self, guardrail_id: str) -> bool:
        guardrails = self.list_guardrails()
        new_guardrails = [g for g in guardrails if g.id != guardrail_id]
        if len(new_guardrails) == len(guardrails):
            return False
        self._write_json(self._guardrails_file, [g.model_dump() for g in new_guardrails])
        return True

    # --- Kill Switch ---

    def get_kill_switch(self) -> KillSwitch:
        """Get current kill switch state. Returns inactive if not set."""
        if not self._killswitch_file.exists():
            return KillSwitch()
        try:
            with open(self._killswitch_file) as f:
                data = json.load(f)
            return KillSwitch(**data)
        except (json.JSONDecodeError, IOError, TypeError):
            return KillSwitch()

    def save_kill_switch(self, ks: KillSwitch) -> KillSwitch:
        """Persist kill switch state."""
        with open(self._killswitch_file, "w") as f:
            json.dump(ks.model_dump(), f, indent=2, default=self._json_default)
        return ks

    # --- Cost Alert Events ---

    def list_cost_alerts(
        self,
        guardrail_id: Optional[str] = None,
        unacknowledged_only: bool = False,
        limit: Optional[int] = None,
    ) -> list[CostAlertEvent]:
        data = self._read_json(self._cost_alerts_file)
        alerts = [CostAlertEvent(**d) for d in data]
        if guardrail_id:
            alerts = [a for a in alerts if a.guardrail_id == guardrail_id]
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        alerts.sort(key=lambda a: a.triggered_at, reverse=True)
        if limit:
            alerts = alerts[:limit]
        return alerts

    def save_cost_alert(self, alert: CostAlertEvent) -> CostAlertEvent:
        alerts = self.list_cost_alerts()
        found = False
        for idx, a in enumerate(alerts):
            if a.id == alert.id:
                alerts[idx] = alert
                found = True
                break
        if not found:
            alerts.append(alert)
        self._write_json(self._cost_alerts_file, [a.model_dump() for a in alerts])
        return alert

    def acknowledge_cost_alert(self, alert_id: str) -> Optional[CostAlertEvent]:
        alerts = self.list_cost_alerts()
        for idx, a in enumerate(alerts):
            if a.id == alert_id:
                a.acknowledged = True
                alerts[idx] = a
                self._write_json(self._cost_alerts_file, [a.model_dump() for a in alerts])
                return a
        return None

    def clear_cost_alerts(self, guardrail_id: Optional[str] = None) -> int:
        alerts = self.list_cost_alerts()
        if guardrail_id:
            new_alerts = [a for a in alerts if a.guardrail_id != guardrail_id]
        else:
            new_alerts = []
        cleared = len(alerts) - len(new_alerts)
        self._write_json(self._cost_alerts_file, [a.model_dump() for a in new_alerts])
        return cleared

    # --- Loop Detection Configs (v0.6.0) ---

    def list_loop_configs(self, enabled_only: bool = False) -> list[LoopDetectionConfig]:
        data = self._read_json(self._loop_configs_file)
        configs = [LoopDetectionConfig(**d) for d in data]
        if enabled_only:
            configs = [c for c in configs if c.enabled]
        return configs

    def get_loop_config(self, config_id: str) -> Optional[LoopDetectionConfig]:
        for c in self.list_loop_configs():
            if c.id == config_id:
                return c
        return None

    def save_loop_config(self, config: LoopDetectionConfig) -> LoopDetectionConfig:
        configs = self.list_loop_configs()
        found = False
        for idx, c in enumerate(configs):
            if c.id == config.id:
                configs[idx] = config
                found = True
                break
        if not found:
            configs.append(config)
        self._write_json(self._loop_configs_file, [c.model_dump() for c in configs])
        return config

    def delete_loop_config(self, config_id: str) -> bool:
        configs = self.list_loop_configs()
        new_configs = [c for c in configs if c.id != config_id]
        if len(new_configs) == len(configs):
            return False
        self._write_json(self._loop_configs_file, [c.model_dump() for c in new_configs])
        return True

    # --- v0.7.0 Webhook Storage ---

    def list_webhooks(self, enabled_only: bool = False) -> list:
        from .models import WebhookConfig
        data = self._read_json(self._webhooks_file)
        hooks = [WebhookConfig(**d) for d in data]
        if enabled_only:
            hooks = [h for h in hooks if h.enabled]
        return hooks

    def get_webhook(self, webhook_id: str):
        for h in self.list_webhooks():
            if h.id == webhook_id:
                return h
        return None

    def save_webhook(self, webhook) -> object:
        from .models import WebhookConfig
        hooks = self.list_webhooks()
        found = False
        for idx, h in enumerate(hooks):
            if h.id == webhook.id:
                hooks[idx] = webhook
                found = True
                break
        if not found:
            hooks.append(webhook)
        self._write_json(self._webhooks_file, [h.model_dump() for h in hooks])
        return webhook

    def delete_webhook(self, webhook_id: str) -> bool:
        hooks = self.list_webhooks()
        new_hooks = [h for h in hooks if h.id != webhook_id]
        if len(new_hooks) == len(hooks):
            return False
        self._write_json(self._webhooks_file, [h.model_dump() for h in new_hooks])
        return True

    def list_webhook_deliveries(self, webhook_id: str | None = None, limit: int = 100) -> list:
        from .models import WebhookDelivery
        data = self._read_json(self._webhook_deliveries_file)
        deliveries = [WebhookDelivery(**d) for d in data]
        if webhook_id:
            deliveries = [d for d in deliveries if d.webhook_id == webhook_id]
        # Sort by delivered_at descending, take limit
        deliveries.sort(key=lambda d: d.delivered_at, reverse=True)
        return deliveries[:limit]

    def save_webhook_delivery(self, delivery) -> object:
        from .models import WebhookDelivery
        deliveries = self.list_webhook_deliveries(limit=10000)
        deliveries.append(delivery)
        # Keep only last 1000 deliveries to avoid unbounded growth
        if len(deliveries) > 1000:
            deliveries = deliveries[-1000:]
        self._write_json(self._webhook_deliveries_file, [d.model_dump() for d in deliveries])
        return delivery

    # --- v0.9.0 Reservations (reserve/settle protocol) ---

    def save_reservation(self, reservation: SpendReservation) -> SpendReservation:
        """Create or update a reservation (thread-safe)."""
        with self._lock:
            data = self._read_json(self._reservations_file)
            records = [SpendReservation(**d) for d in data]
            found = False
            for idx, r in enumerate(records):
                if r.id == reservation.id:
                    records[idx] = reservation
                    found = True
                    break
            if not found:
                records.append(reservation)
            self._write_json(self._reservations_file, [r.model_dump() for r in records])
            return reservation

    def get_reservation(self, reservation_id: str) -> Optional[SpendReservation]:
        """Fetch a single reservation by ID."""
        with self._lock:
            for r in self.list_reservations():
                if r.id == reservation_id:
                    return r
        return None

    def list_reservations(
        self,
        status: Optional[ReservationStatus] = None,
        agent_id: Optional[str] = None,
        active_only: bool = False,
        now: Optional[datetime] = None,
    ) -> list[SpendReservation]:
        """List reservations with optional filters.

        When ``active_only`` is True, only reservations that still count
        against the budget (status=ACTIVE and not expired) are returned.
        """
        from datetime import datetime as _dt, timezone as _tz
        now = now or _dt.now(_tz.utc)
        with self._lock:
            data = self._read_json(self._reservations_file)
            records = [SpendReservation(**d) for d in data]
        if active_only:
            records = [r for r in records if r.is_active(now)]
        if status is not None:
            records = [r for r in records if r.status == status]
        if agent_id:
            records = [r for r in records if r.agent_id and r.agent_id.lower() == agent_id.lower()]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def delete_reservation(self, reservation_id: str) -> bool:
        """Remove a reservation entirely (hard delete)."""
        with self._lock:
            records = self.list_reservations()
            new_records = [r for r in records if r.id != reservation_id]
            if len(new_records) == len(records):
                return False
            self._write_json(self._reservations_file, [r.model_dump() for r in new_records])
            return True

    def expire_stale_reservations(self, now: Optional[datetime] = None) -> int:
        """Mark ACTIVE reservations past their TTL as EXPIRED.

        Returns the count of newly-expired reservations.  This should be
        called periodically (e.g. at the top of each guardrail check) so
        that crashed/abandoned calls eventually release their budget.
        """
        from datetime import datetime as _dt, timezone as _tz
        now = now or _dt.now(_tz.utc)
        expired_count = 0
        with self._lock:
            records = self.list_reservations()
            changed = False
            for idx, r in enumerate(records):
                if r.status == ReservationStatus.ACTIVE and now > r.expires_at:
                    records[idx] = r.model_copy(update={
                        "status": ReservationStatus.EXPIRED,
                    })
                    changed = True
                    expired_count += 1
            if changed:
                self._write_json(self._reservations_file, [r.model_dump() for r in records])
        return expired_count

    # --- v0.10.0: Spend Anomaly Detection ---

    def list_anomaly_rules(self, enabled_only: bool = False) -> list[SpendAnomalyRule]:
        """List all anomaly detection rules."""
        with self._lock:
            data = self._read_json(self._anomaly_rules_file)
            rules = [SpendAnomalyRule(**d) for d in data]
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        rules.sort(key=lambda r: r.created_at)
        return rules

    def get_anomaly_rule(self, rule_id: str) -> Optional[SpendAnomalyRule]:
        """Fetch a single anomaly rule by ID."""
        for r in self.list_anomaly_rules():
            if r.id == rule_id:
                return r
        return None

    def save_anomaly_rule(self, rule: SpendAnomalyRule) -> SpendAnomalyRule:
        """Create or update an anomaly rule."""
        with self._lock:
            rules = self.list_anomaly_rules()
            found = False
            for idx, r in enumerate(rules):
                if r.id == rule.id:
                    rules[idx] = rule
                    found = True
                    break
            if not found:
                rules.append(rule)
            self._write_json(self._anomaly_rules_file, [r.model_dump() for r in rules])
            return rule

    def delete_anomaly_rule(self, rule_id: str) -> bool:
        """Delete an anomaly rule by ID."""
        with self._lock:
            rules = self.list_anomaly_rules()
            new_rules = [r for r in rules if r.id != rule_id]
            if len(new_rules) == len(rules):
                return False
            self._write_json(self._anomaly_rules_file, [r.model_dump() for r in new_rules])
            return True

    def list_anomaly_events(
        self,
        rule_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        resolved: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> list[AnomalyEvent]:
        """List anomaly events with optional filters."""
        with self._lock:
            data = self._read_json(self._anomaly_events_file)
            events = [AnomalyEvent(**d) for d in data]
        if rule_id:
            events = [e for e in events if e.rule_id == rule_id]
        if acknowledged is not None:
            events = [e for e in events if e.acknowledged == acknowledged]
        if resolved is not None:
            events = [e for e in events if e.resolved == resolved]
        events.sort(key=lambda e: e.detected_at, reverse=True)
        if limit:
            events = events[:limit]
        return events

    def get_anomaly_event(self, event_id: str) -> Optional[AnomalyEvent]:
        """Fetch a single anomaly event by ID."""
        for e in self.list_anomaly_events():
            if e.id == event_id:
                return e
        return None

    def save_anomaly_event(self, event: AnomalyEvent) -> AnomalyEvent:
        """Create or update an anomaly event."""
        with self._lock:
            events = self.list_anomaly_events()
            found = False
            for idx, e in enumerate(events):
                if e.id == event.id:
                    events[idx] = event
                    found = True
                    break
            if not found:
                events.append(event)
            self._write_json(self._anomaly_events_file, [e.model_dump() for e in events])
            return event

    def delete_anomaly_event(self, event_id: str) -> bool:
        """Delete an anomaly event by ID."""
        with self._lock:
            events = self.list_anomaly_events()
            new_events = [e for e in events if e.id != event_id]
            if len(new_events) == len(events):
                return False
            self._write_json(self._anomaly_events_file, [e.model_dump() for e in new_events])
            return True

    def clear_anomaly_events(self, rule_id: Optional[str] = None) -> int:
        """Clear anomaly events, optionally filtered by rule. Returns count deleted."""
        with self._lock:
            events = self.list_anomaly_events()
            if rule_id:
                to_delete = [e for e in events if e.rule_id == rule_id]
                to_keep = [e for e in events if e.rule_id != rule_id]
            else:
                to_delete = events
                to_keep = []
            count = len(to_delete)
            self._write_json(self._anomaly_events_file, [e.model_dump() for e in to_keep])
            return count
