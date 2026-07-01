"""Business logic for Agent Budget — budget checks, forecasts, alerts, comparisons."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .models import (
    Budget, BudgetPeriod, BudgetRollover, Expense, RecurringExpense, RecurringFrequency,
    BudgetAlert, BudgetComparison, SpendingForecast, AlertLevel, AlertThreshold,
    SavingsGoal, SavingsGoalStatus, SavingsContribution,
    SpendingRule, SpendingRuleAction,
    SUPPORTED_CURRENCIES,
    SpendingTrend, TrendDirection, CategoryBreakdown, PeriodComparison,
    BudgetTemplate, CSVImportResult, BUILTIN_BUDGET_TEMPLATES,
    Income, RecurringIncome, IncomeStatus,
    CashFlowSummary, BurnRate, FinancialDashboard,
)
from .store import BudgetStore


class BudgetService:
    """Core business logic for budget management."""

    def __init__(self, store: Optional[BudgetStore] = None):
        self.store = store or BudgetStore()

    # --- Budget CRUD ---

    def create_budget(
        self,
        name: str,
        limit: float,
        period: BudgetPeriod,
        category: Optional[str] = None,
        currency: str = "USD",
        rollover_enabled: bool = False,
        rollover_cap: Optional[float] = None,
    ) -> Budget:
        budget = Budget(
            name=name, limit=limit, period=period, category=category,
            currency=currency, rollover_enabled=rollover_enabled,
            rollover_cap=rollover_cap,
        )
        return self.store.save_budget(budget)

    def update_budget(
        self,
        budget_id: str,
        name: Optional[str] = None,
        limit: Optional[float] = None,
        period: Optional[BudgetPeriod] = None,
        category: Optional[str] = None,
        active: Optional[bool] = None,
        rollover_enabled: Optional[bool] = None,
        rollover_cap: Optional[float] = None,
        alert_thresholds: Optional[list[AlertThreshold]] = None,
    ) -> Budget:
        budget = self.store.get_budget(budget_id)
        if not budget:
            raise ValueError(f"Budget {budget_id} not found")
        if name is not None:
            budget.name = name
        if limit is not None:
            budget.limit = limit
        if period is not None:
            budget.period = period
        if category is not None:
            budget.category = category
        if active is not None:
            budget.active = active
        if rollover_enabled is not None:
            budget.rollover_enabled = rollover_enabled
        if rollover_cap is not None:
            budget.rollover_cap = rollover_cap
        if alert_thresholds is not None:
            budget.alert_thresholds = alert_thresholds
        budget.updated_at = datetime.now(timezone.utc)
        return self.store.save_budget(budget)

    def delete_budget(self, budget_id: str) -> bool:
        return self.store.delete_budget(budget_id)

    def list_budgets(self, active_only: bool = False) -> list[Budget]:
        return self.store.list_budgets(active_only=active_only)

    def get_budget(self, budget_id: str) -> Optional[Budget]:
        return self.store.get_budget(budget_id)

    # --- Expense CRUD ---

    def add_expense(
        self,
        amount: float,
        category: str,
        description: str = "",
        expense_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        currency: str = "USD",
        budget_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        vendor: Optional[str] = None,
        receipt_url: Optional[str] = None,
        reimbursable: bool = False,
        approved_by: Optional[str] = None,
    ) -> Expense:
        # Auto-assign budget if category matches
        if not budget_id and category:
            budget = self._find_budget_for_category(category)
            if budget:
                budget_id = budget.id

        expense = Expense(
            amount=amount,
            category=category,
            description=description,
            expense_date=expense_date or date.today(),
            tags=tags or [],
            currency=currency,
            budget_id=budget_id,
            metadata=metadata or {},
            vendor=vendor,
            receipt_url=receipt_url,
            reimbursable=reimbursable,
            approved_by=approved_by,
        )

        # Check spending rules
        rule_violations = self._check_spending_rules(expense)
        blocked = [v for v in rule_violations if v.startswith("Total") or "exceeds approval" in v]
        if blocked:
            raise ValueError(f"Expense blocked by spending rule: {blocked[0]}")

        expense = self.store.save_expense(expense)

        # Check alerts after adding
        if budget_id:
            self._check_budget_alerts(budget_id)

        return expense

    def update_expense(
        self,
        expense_id: str,
        amount: Optional[float] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        status: Optional[str] = None,
        vendor: Optional[str] = None,
        receipt_url: Optional[str] = None,
        reimbursable: Optional[bool] = None,
        approved_by: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Expense:
        """Update an existing expense."""
        expense = self.store.get_expense(expense_id)
        if not expense:
            raise ValueError(f"Expense {expense_id} not found")
        if amount is not None:
            expense.amount = amount
        if category is not None:
            expense.category = category
        if description is not None:
            expense.description = description
        if tags is not None:
            expense.tags = tags
        if status is not None:
            from .models import ExpenseStatus
            expense.status = ExpenseStatus(status)
        if vendor is not None:
            expense.vendor = vendor
        if receipt_url is not None:
            expense.receipt_url = receipt_url
        if reimbursable is not None:
            expense.reimbursable = reimbursable
        if approved_by is not None:
            expense.approved_by = approved_by
        if metadata is not None:
            expense.metadata = metadata
        return self.store.save_expense(expense)

    def delete_expense(self, expense_id: str) -> bool:
        expense = self.store.get_expense(expense_id)
        result = self.store.delete_expense(expense_id)
        if result and expense and expense.budget_id:
            self._check_budget_alerts(expense.budget_id)
        return result

    def list_expenses(self, **kwargs) -> list[Expense]:
        return self.store.list_expenses(**kwargs)

    def get_expense(self, expense_id: str) -> Optional[Expense]:
        return self.store.get_expense(expense_id)

    # --- Recurring Expenses ---

    def add_recurring_expense(
        self,
        name: str,
        amount: float,
        category: str,
        frequency: RecurringFrequency,
        description: str = "",
        currency: str = "USD",
        tags: Optional[list[str]] = None,
        budget_id: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> RecurringExpense:
        if not budget_id and category:
            budget = self._find_budget_for_category(category)
            if budget:
                budget_id = budget.id

        recurring = RecurringExpense(
            name=name,
            amount=amount,
            category=category,
            frequency=frequency,
            description=description,
            currency=currency,
            tags=tags or [],
            budget_id=budget_id,
            start_date=start_date or date.today(),
            end_date=end_date,
            next_due=start_date or date.today(),
        )
        return self.store.save_recurring_expense(recurring)

    def process_recurring_expenses(self, ref_date: Optional[date] = None) -> list[Expense]:
        """Generate expenses for all due recurring templates."""
        ref_date = ref_date or date.today()
        generated = []
        for recurring in self.store.list_recurring_expenses(active_only=True):
            while recurring.next_due <= ref_date:
                if recurring.end_date and recurring.next_due > recurring.end_date:
                    recurring.active = False
                    self.store.save_recurring_expense(recurring)
                    break
                expense = self.add_expense(
                    amount=recurring.amount,
                    category=recurring.category,
                    description=f"{recurring.name} (recurring)",
                    expense_date=recurring.next_due,
                    tags=recurring.tags,
                    currency=recurring.currency,
                    budget_id=recurring.budget_id,
                    metadata={"recurring_id": recurring.id, "source": "recurring"},
                )
                generated.append(expense)
                recurring.next_due = recurring.advance_next_due()
                self.store.save_recurring_expense(recurring)
        return generated

    def list_recurring_expenses(self, active_only: bool = False) -> list[RecurringExpense]:
        return self.store.list_recurring_expenses(active_only=active_only)

    def get_recurring_expense(self, recurring_id: str) -> Optional[RecurringExpense]:
        return self.store.get_recurring_expense(recurring_id)

    def delete_recurring_expense(self, recurring_id: str) -> bool:
        return self.store.delete_recurring_expense(recurring_id)

    def pause_recurring(self, recurring_id: str) -> RecurringExpense:
        recurring = self.store.get_recurring_expense(recurring_id)
        if not recurring:
            raise ValueError(f"Recurring expense {recurring_id} not found")
        recurring.active = False
        return self.store.save_recurring_expense(recurring)

    def resume_recurring(self, recurring_id: str) -> RecurringExpense:
        recurring = self.store.get_recurring_expense(recurring_id)
        if not recurring:
            raise ValueError(f"Recurring expense {recurring_id} not found")
        recurring.active = True
        return self.store.save_recurring_expense(recurring)

    # --- Budget Rollover ---

    def process_budget_rollover(self, budget_id: str, ref_date: Optional[date] = None) -> Optional[BudgetRollover]:
        """Process budget rollover: carry unspent budget to the next period.

        Only processes if the budget has rollover_enabled and hasn't already
        been rolled over for this period transition.
        """
        budget = self.store.get_budget(budget_id)
        if not budget:
            raise ValueError(f"Budget {budget_id} not found")
        if not budget.rollover_enabled:
            return None

        ref_date = ref_date or date.today()
        period_start = budget.get_period_start(ref_date)
        prev_period_start = self._get_previous_period_start(budget, ref_date)

        # Check if we already have a rollover for this transition
        existing = self.store.list_rollovers(budget_id=budget_id)
        for r in existing:
            if r.to_period_start == period_start:
                return None  # Already rolled over

        # Calculate unspent from previous period
        prev_end = period_start - timedelta(days=1)
        prev_spent = self._get_spending_for_period(budget_id, prev_period_start, prev_end)
        unspent = budget.limit - prev_spent

        if unspent <= 0:
            # No rollover if overspent
            budget.current_rollover = 0.0
            self.store.save_budget(budget)
            return None

        # Apply cap
        if budget.rollover_cap is not None:
            unspent = min(unspent, budget.rollover_cap)

        # Update budget with rollover
        budget.current_rollover = unspent
        self.store.save_budget(budget)

        rollover = BudgetRollover(
            budget_id=budget.id,
            from_period_start=prev_period_start,
            from_period_end=prev_end,
            to_period_start=period_start,
            to_period_end=budget.get_period_end(ref_date),
            unspent_amount=unspent,
            previous_limit=budget.limit,
        )
        return self.store.save_rollover(rollover)

    def process_all_rollovers(self, ref_date: Optional[date] = None) -> list[BudgetRollover]:
        """Process rollovers for all active budgets with rollover enabled."""
        ref_date = ref_date or date.today()
        rollovers = []
        for budget in self.store.list_budgets(active_only=True):
            if budget.rollover_enabled:
                result = self.process_budget_rollover(budget.id, ref_date)
                if result:
                    rollovers.append(result)
        return rollovers

    def _get_previous_period_start(self, budget: Budget, ref_date: date) -> date:
        """Calculate the start of the previous budget period."""
        current_start = budget.get_period_start(ref_date)
        if budget.period == BudgetPeriod.DAILY:
            return current_start - timedelta(days=1)
        elif budget.period == BudgetPeriod.WEEKLY:
            return current_start - timedelta(weeks=1)
        elif budget.period == BudgetPeriod.MONTHLY:
            if current_start.month == 1:
                return current_start.replace(year=current_start.year - 1, month=12)
            else:
                return current_start.replace(month=current_start.month - 1)
        elif budget.period == BudgetPeriod.QUARTERLY:
            if current_start.month <= 3:
                return current_start.replace(year=current_start.year - 1, month=10)
            else:
                return current_start.replace(month=current_start.month - 3)
        elif budget.period == BudgetPeriod.YEARLY:
            return current_start.replace(year=current_start.year - 1)
        return current_start - timedelta(days=30)

    def _get_spending_for_period(self, budget_id: str, start: date, end: date) -> float:
        """Get total spending for a budget in a specific date range."""
        expenses = self.store.list_expenses(budget_id=budget_id, start_date=start, end_date=end)
        return sum(e.amount for e in expenses if e.status.value != "cancelled")

    # --- Budget Status & Alerts ---

    def get_spending_for_budget(self, budget_id: str, ref_date: Optional[date] = None) -> float:
        """Get total spending for a budget in its current period."""
        budget = self.store.get_budget(budget_id)
        if not budget:
            raise ValueError(f"Budget {budget_id} not found")
        period_start = budget.get_period_start(ref_date)
        period_end = budget.get_period_end(ref_date)
        expenses = self.store.list_expenses(
            budget_id=budget_id,
            start_date=period_start,
            end_date=period_end,
        )
        return sum(e.amount for e in expenses if e.status.value != "cancelled")

    def get_budget_status(self, budget_id: str, ref_date: Optional[date] = None) -> BudgetComparison:
        """Get budget vs. actual comparison for a single budget."""
        budget = self.store.get_budget(budget_id)
        if not budget:
            raise ValueError(f"Budget {budget_id} not found")
        spent = self.get_spending_for_budget(budget_id, ref_date)
        effective_limit = budget.effective_limit
        remaining = effective_limit - spent
        percent_used = (spent / effective_limit * 100) if effective_limit > 0 else 0

        if percent_used >= 100:
            status = "critical"
        elif percent_used >= 90:
            status = "over"
        elif percent_used >= 75:
            status = "on_track"
        else:
            status = "under"

        return BudgetComparison(
            budget_id=budget.id,
            budget_name=budget.name,
            category=budget.category,
            budget_limit=budget.limit,
            actual_spent=spent,
            remaining=remaining,
            percent_used=round(percent_used, 1),
            period=budget.period,
            period_start=budget.get_period_start(ref_date),
            period_end=budget.get_period_end(ref_date),
            status=status,
            rollover_amount=budget.current_rollover,
            effective_limit=effective_limit,
        )

    def get_all_budget_status(self, ref_date: Optional[date] = None) -> list[BudgetComparison]:
        """Get budget vs. actual for all active budgets."""
        budgets = self.store.list_budgets(active_only=True)
        return [self.get_budget_status(b.id, ref_date) for b in budgets]

    def _check_budget_alerts(self, budget_id: str) -> list[BudgetAlert]:
        """Check if any alert thresholds are crossed and create alerts."""
        budget = self.store.get_budget(budget_id)
        if not budget:
            return []
        spent = self.get_spending_for_budget(budget_id)
        effective_limit = budget.effective_limit
        percent = (spent / effective_limit * 100) if effective_limit > 0 else 0
        remaining = effective_limit - spent

        # Don't re-alert for the same threshold
        existing_alerts = self.store.list_alerts(budget_id=budget_id)
        alerted_percents = set()
        for a in existing_alerts:
            # Check alerts from today only
            if a.created_at.date() == date.today():
                alerted_percents.add(int(a.percent_spent))

        new_alerts = []
        for threshold in budget.alert_thresholds:
            if percent >= threshold.percent and int(threshold.percent) not in alerted_percents:
                alert = BudgetAlert(
                    budget_id=budget.id,
                    budget_name=budget.name,
                    level=threshold.level,
                    percent_spent=round(percent, 1),
                    amount_spent=spent,
                    budget_limit=budget.limit,
                    remaining=remaining,
                    period=budget.period,
                    message=f"Budget '{budget.name}' is at {percent:.1f}% (${spent:.2f} of ${effective_limit:.2f})",
                )
                self.store.save_alert(alert)
                new_alerts.append(alert)

        return new_alerts

    def get_alerts(self, budget_id: Optional[str] = None) -> list[BudgetAlert]:
        return self.store.list_alerts(budget_id=budget_id)

    def clear_alerts(self, budget_id: Optional[str] = None) -> int:
        return self.store.clear_alerts(budget_id=budget_id)

    def update_alert_thresholds(self, budget_id: str, thresholds: list[AlertThreshold]) -> Budget:
        """Update alert thresholds for a budget."""
        return self.update_budget(budget_id=budget_id, alert_thresholds=thresholds)

    # --- Savings Goals ---

    def create_savings_goal(
        self,
        name: str,
        target_amount: float,
        currency: str = "USD",
        target_date: Optional[date] = None,
        category: Optional[str] = None,
        description: str = "",
    ) -> SavingsGoal:
        goal = SavingsGoal(
            name=name,
            target_amount=target_amount,
            currency=currency,
            target_date=target_date,
            category=category,
            description=description,
        )
        return self.store.save_savings_goal(goal)

    def update_savings_goal(
        self,
        goal_id: str,
        name: Optional[str] = None,
        target_amount: Optional[float] = None,
        target_date: Optional[date] = None,
        description: Optional[str] = None,
        status: Optional[SavingsGoalStatus] = None,
    ) -> SavingsGoal:
        goal = self.store.get_savings_goal(goal_id)
        if not goal:
            raise ValueError(f"Savings goal {goal_id} not found")
        if name is not None:
            goal.name = name
        if target_amount is not None:
            goal.target_amount = target_amount
        if target_date is not None:
            goal.target_date = target_date
        if description is not None:
            goal.description = description
        if status is not None:
            goal.status = status
        goal.updated_at = datetime.now(timezone.utc)
        return self.store.save_savings_goal(goal)

    def contribute_to_savings(
        self,
        goal_id: str,
        amount: float,
        note: str = "",
        contribution_date: Optional[date] = None,
    ) -> SavingsGoal:
        """Add a contribution to a savings goal."""
        goal = self.store.get_savings_goal(goal_id)
        if not goal:
            raise ValueError(f"Savings goal {goal_id} not found")
        if amount <= 0:
            raise ValueError("Contribution amount must be positive")

        contribution = SavingsContribution(
            amount=amount,
            note=note,
            contribution_date=contribution_date or date.today(),
        )
        goal.contributions.append(contribution)
        goal.current_amount += amount
        goal.updated_at = datetime.now(timezone.utc)

        # Auto-complete if target reached
        if goal.is_complete and goal.status == SavingsGoalStatus.ACTIVE:
            goal.status = SavingsGoalStatus.COMPLETED

        return self.store.save_savings_goal(goal)

    def withdraw_from_savings(
        self,
        goal_id: str,
        amount: float,
        note: str = "",
    ) -> SavingsGoal:
        """Withdraw from a savings goal (negative contribution)."""
        goal = self.store.get_savings_goal(goal_id)
        if not goal:
            raise ValueError(f"Savings goal {goal_id} not found")
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")
        if amount > goal.current_amount:
            raise ValueError(f"Cannot withdraw ${amount:.2f}, only ${goal.current_amount:.2f} available")

        # Record as negative contribution
        contribution = SavingsContribution(
            amount=-amount,
            note=note or "Withdrawal",
            contribution_date=date.today(),
        )
        goal.contributions.append(contribution)
        goal.current_amount -= amount
        goal.updated_at = datetime.now(timezone.utc)

        # If was completed, move back to active
        if goal.status == SavingsGoalStatus.COMPLETED and not goal.is_complete:
            goal.status = SavingsGoalStatus.ACTIVE

        return self.store.save_savings_goal(goal)

    def list_savings_goals(self, status: Optional[str] = None) -> list[SavingsGoal]:
        return self.store.list_savings_goals(status=status)

    def get_savings_goal(self, goal_id: str) -> Optional[SavingsGoal]:
        return self.store.get_savings_goal(goal_id)

    def delete_savings_goal(self, goal_id: str) -> bool:
        return self.store.delete_savings_goal(goal_id)

    def pause_savings_goal(self, goal_id: str) -> SavingsGoal:
        return self.update_savings_goal(goal_id, status=SavingsGoalStatus.PAUSED)

    def resume_savings_goal(self, goal_id: str) -> SavingsGoal:
        return self.update_savings_goal(goal_id, status=SavingsGoalStatus.ACTIVE)

    # --- Spending Rules ---

    def create_spending_rule(
        self,
        name: str,
        category: str,
        action: SpendingRuleAction,
        threshold_amount: Optional[float] = None,
        threshold_percent: Optional[float] = None,
        budget_id: Optional[str] = None,
        requires_approval_above: Optional[float] = None,
        description: str = "",
    ) -> SpendingRule:
        rule = SpendingRule(
            name=name,
            category=category,
            action=action,
            threshold_amount=threshold_amount,
            threshold_percent=threshold_percent,
            budget_id=budget_id,
            requires_approval_above=requires_approval_above,
            description=description,
        )
        return self.store.save_spending_rule(rule)

    def update_spending_rule(
        self,
        rule_id: str,
        name: Optional[str] = None,
        action: Optional[SpendingRuleAction] = None,
        threshold_amount: Optional[float] = None,
        threshold_percent: Optional[float] = None,
        enabled: Optional[bool] = None,
        requires_approval_above: Optional[float] = None,
        description: Optional[str] = None,
    ) -> SpendingRule:
        rule = self.store.get_spending_rule(rule_id)
        if not rule:
            raise ValueError(f"Spending rule {rule_id} not found")
        if name is not None:
            rule.name = name
        if action is not None:
            rule.action = action
        if threshold_amount is not None:
            rule.threshold_amount = threshold_amount
        if threshold_percent is not None:
            rule.threshold_percent = threshold_percent
        if enabled is not None:
            rule.enabled = enabled
        if requires_approval_above is not None:
            rule.requires_approval_above = requires_approval_above
        if description is not None:
            rule.description = description
        return self.store.save_spending_rule(rule)

    def list_spending_rules(self, enabled_only: bool = False) -> list[SpendingRule]:
        return self.store.list_spending_rules(enabled_only=enabled_only)

    def get_spending_rule(self, rule_id: str) -> Optional[SpendingRule]:
        return self.store.get_spending_rule(rule_id)

    def delete_spending_rule(self, rule_id: str) -> bool:
        return self.store.delete_spending_rule(rule_id)

    def _check_spending_rules(self, expense: Expense) -> list[str]:
        """Check an expense against all applicable spending rules.

        Returns a list of violation messages. Empty means no violations.
        """
        violations = []
        for rule in self.store.list_spending_rules(enabled_only=True):
            if rule.category.lower() != expense.category.lower():
                continue

            # Get current spending for the category if rule needs it
            budget_spent = 0.0
            budget_limit = 0.0
            if rule.threshold_amount or rule.threshold_percent:
                if rule.budget_id:
                    try:
                        budget_spent = self.get_spending_for_budget(rule.budget_id)
                        budget = self.store.get_budget(rule.budget_id)
                        if budget:
                            budget_limit = budget.effective_limit
                    except ValueError:
                        pass
                else:
                    # No specific budget, use total spending for category
                    category_expenses = self.store.list_expenses(category=expense.category)
                    budget_spent = sum(e.amount for e in category_expenses if e.status.value != "cancelled")

            result = rule.check_expense(expense, budget_spent, budget_limit)
            if result:
                violations.append(result)

        return violations

    def check_expense_rules(self, expense: Expense) -> list[str]:
        """Public method to check expense against spending rules without adding it."""
        return self._check_spending_rules(expense)

    # --- Forecasting ---

    def get_spending_forecast(
        self,
        months: int = 3,
        category: Optional[str] = None,
        budget_id: Optional[str] = None,
    ) -> list[SpendingForecast]:
        """Forecast spending based on historical data."""
        today = date.today()
        forecasts = []

        if budget_id:
            budgets = [self.store.get_budget(budget_id)]
            budgets = [b for b in budgets if b is not None]
        else:
            budgets = self.store.list_budgets(active_only=True)

        for budget in budgets:
            if category and budget.category and budget.category.lower() != category.lower():
                continue

            # Gather historical data: last 6 periods
            historical_spends = []
            for i in range(6, 0, -1):
                ref = today - timedelta(days=i * 30)  # approximate
                try:
                    spent = self.get_spending_for_budget(budget.id, ref_date=ref)
                    historical_spends.append(spent)
                except Exception:
                    pass

            if not historical_spends:
                # No history, use budget limit as estimate
                projected = budget.limit
                confidence = 0.1
                based_on = 0
            else:
                avg_spend = sum(historical_spends) / len(historical_spends)
                projected = avg_spend
                confidence = min(0.95, 0.3 + 0.1 * len(historical_spends))
                based_on = len(historical_spends)

            # Forecast for each month ahead
            for m in range(1, months + 1):
                forecast_date = today + timedelta(days=m * 30)
                period_desc = forecast_date.strftime("%B %Y")
                forecasts.append(SpendingForecast(
                    budget_id=budget.id,
                    category=budget.category,
                    period=period_desc,
                    projected_spending=round(projected, 2),
                    budget_limit=budget.limit,
                    confidence=round(confidence, 2),
                    based_on_periods=based_on,
                ))

        return forecasts

    # --- Export ---

    def export_data(self, format: str = "json") -> str:
        """Export all data in the specified format."""
        budgets = self.store.list_budgets()
        expenses = self.store.list_expenses()
        recurring = self.store.list_recurring_expenses()
        alerts = self.store.list_alerts()
        savings_goals = self.store.list_savings_goals()
        spending_rules = self.store.list_spending_rules()
        rollovers = self.store.list_rollovers()
        incomes = self.store.list_income()
        recurring_incomes = self.store.list_recurring_income()

        if format == "json":
            import json
            data = {
                "budgets": [b.model_dump() for b in budgets],
                "expenses": [e.model_dump() for e in expenses],
                "recurring_expenses": [r.model_dump() for r in recurring],
                "savings_goals": [g.model_dump() for g in savings_goals],
                "spending_rules": [r.model_dump() for r in spending_rules],
                "rollovers": [r.model_dump() for r in rollovers],
                "alerts": [a.model_dump() for a in alerts],
                "incomes": [i.model_dump() for i in incomes],
                "recurring_incomes": [r.model_dump() for r in recurring_incomes],
            }
            return json.dumps(data, indent=2, default=str)

        elif format == "csv":
            lines = ["type,id,name/description,amount,category/source,date,currency"]
            for b in budgets:
                lines.append(f"budget,{b.id},{b.name},{b.limit},{b.category or ''},,{b.currency}")
            for e in expenses:
                lines.append(f"expense,{e.id},{e.description},{e.amount},{e.category},{e.expense_date},{e.currency}")
            for r in recurring:
                lines.append(f"recurring,{r.id},{r.name},{r.amount},{r.category},{r.frequency.value},{r.currency}")
            for g in savings_goals:
                lines.append(f"savings,{g.id},{g.name},{g.current_amount}/{g.target_amount},{g.category or ''},,{g.currency}")
            for r in spending_rules:
                lines.append(f"rule,{r.id},{r.name},{r.threshold_amount or ''},{r.category},,{r.action.value}")
            for i in incomes:
                lines.append(f"income,{i.id},{i.description},{i.amount},{i.source},{i.income_date},{i.currency}")
            for ri in recurring_incomes:
                lines.append(f"recurring-income,{ri.id},{ri.name},{ri.amount},{ri.source},{ri.frequency.value},{ri.currency}")
            return "\n".join(lines)

        elif format == "markdown":
            lines = ["# Agent Budget Export", ""]
            lines.append("## Budgets")
            lines.append("")
            lines.append("| Name | Limit | Rollover | Period | Category | Currency |")
            lines.append("|------|-------|----------|--------|----------|----------|")
            for b in budgets:
                ro = f"+${b.current_rollover:.2f}" if b.rollover_enabled and b.current_rollover > 0 else ("Yes" if b.rollover_enabled else "-")
                lines.append(f"| {b.name} | {b.limit:.2f} | {ro} | {b.period.value} | {b.category or '-'} | {b.currency} |")
            lines.append("")
            lines.append("## Recent Expenses")
            lines.append("")
            lines.append("| Date | Category | Amount | Vendor | Reimbursable |")
            lines.append("|------|----------|--------|--------|--------------|")
            for e in expenses[:50]:
                lines.append(f"| {e.expense_date} | {e.category} | {e.amount:.2f} | {e.vendor or '-'} | {'Yes' if e.reimbursable else 'No'} |")
            lines.append("")
            lines.append("## Savings Goals")
            lines.append("")
            lines.append("| Name | Progress | Current | Target | Status |")
            lines.append("|------|----------|---------|--------|--------|")
            for g in savings_goals:
                lines.append(f"| {g.name} | {g.progress_percent:.0f}% | {g.current_amount:.2f} | {g.target_amount:.2f} | {g.status.value} |")
            lines.append("")
            lines.append("## Spending Rules")
            lines.append("")
            lines.append("| Name | Category | Action | Threshold | Approval Above |")
            lines.append("|------|----------|--------|-----------|----------------|")
            for r in spending_rules:
                thresh = f"${r.threshold_amount:.2f}" if r.threshold_amount else (f"{r.threshold_percent:.0f}%" if r.threshold_percent else "-")
                approval = f"${r.requires_approval_above:.2f}" if r.requires_approval_above else "-"
                lines.append(f"| {r.name} | {r.category} | {r.action.value} | {thresh} | {approval} |")
            lines.append("")
            lines.append("## Recurring Expenses")
            lines.append("")
            lines.append("| Name | Amount | Frequency | Category | Next Due |")
            lines.append("|------|--------|-----------|----------|----------|")
            for r in recurring:
                lines.append(f"| {r.name} | {r.amount:.2f} | {r.frequency.value} | {r.category} | {r.next_due} |")
            lines.append("")
            lines.append("## Income")
            lines.append("")
            lines.append("| Date | Source | Amount | Status | Invoice Ref |")
            lines.append("|------|--------|--------|--------|-------------|")
            for i in incomes[:50]:
                lines.append(f"| {i.income_date} | {i.source} | {i.amount:.2f} | {i.status.value} | {i.invoice_ref or '-'} |")
            lines.append("")
            lines.append("## Recurring Income")
            lines.append("")
            lines.append("| Name | Amount | Frequency | Source | Next Due |")
            lines.append("|------|--------|-----------|--------|----------|")
            for ri in recurring_incomes:
                lines.append(f"| {ri.name} | {ri.amount:.2f} | {ri.frequency.value} | {ri.source} | {ri.next_due} |")
            return "\n".join(lines)

        else:
            raise ValueError(f"Unsupported export format: {format}")

    # --- Helpers ---

    def _find_budget_for_category(self, category: str) -> Optional[Budget]:
        """Find an active budget that matches the given category."""
        for budget in self.store.list_budgets(active_only=True):
            if budget.category and budget.category.lower() == category.lower():
                return budget
        return None

    def get_total_spending(
        self,
        category: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> float:
        """Get total spending across all expenses matching filters."""
        expenses = self.store.list_expenses(category=category, start_date=start_date, end_date=end_date)
        return sum(e.amount for e in expenses if e.status.value != "cancelled")

    def get_category_summary(self, start_date: Optional[date] = None, end_date: Optional[date] = None) -> dict[str, float]:
        """Get spending grouped by category."""
        expenses = self.store.list_expenses(start_date=start_date, end_date=end_date)
        summary: dict[str, float] = {}
        for e in expenses:
            if e.status.value == "cancelled":
                continue
            summary[e.category] = summary.get(e.category, 0) + e.amount
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    @staticmethod
    def list_currencies() -> list[dict]:
        """List all supported currencies."""
        return [c.model_dump() for c in SUPPORTED_CURRENCIES.values()]

    # --- v0.3.0: CSV Import ---

    def import_csv(
        self,
        file_path: str,
        category: Optional[str] = None,
        currency: str = "USD",
        budget_id: Optional[str] = None,
        skip_duplicates: bool = True,
        mapping: Optional[dict] = None,
    ) -> CSVImportResult:
        """Import expenses from a CSV file.

        Expected CSV columns (with default mapping):
          - date/expense_date: Date of the expense (YYYY-MM-DD)
          - amount: Expense amount
          - category: Expense category
          - description/memo: Description
          - vendor/merchant: Vendor name
          - tags: Comma-separated tags
          - currency: Currency code (optional)

        If 'category' column is missing, the provided default category is used.
        If 'date' column is missing, today's date is used.

        Args:
            file_path: Path to the CSV file
            category: Default category for expenses without one
            currency: Default currency
            budget_id: Default budget ID
            skip_duplicates: Skip rows that look like existing expenses
            mapping: Optional column name mapping {csv_name: model_name}
        """
        import csv as csv_module

        result = CSVImportResult(total_rows=0, imported=0, skipped=0)

        # Default column name mappings (CSV name -> model field)
        default_mapping = {
            "date": "date",
            "expense_date": "date",
            "transaction_date": "date",
            "amount": "amount",
            "description": "description",
            "memo": "description",
            "note": "description",
            "category": "category",
            "cat": "category",
            "vendor": "vendor",
            "merchant": "vendor",
            "payee": "vendor",
            "tags": "tags",
            "tag": "tags",
            "currency": "currency",
        }

        if mapping:
            default_mapping.update(mapping)

        try:
            with open(file_path, "r", newline="", encoding="utf-8") as f:
                # Try to sniff the dialect
                sample = f.read(8192)
                f.seek(0)

                try:
                    dialect = csv_module.Sniffer().sniff(sample)
                except csv_module.Error:
                    dialect = csv_module.excel

                reader = csv_module.DictReader(f, dialect=dialect)
                rows = list(reader)
                result.total_rows = len(rows)

                # Normalize headers
                normalized_rows = []
                for row in rows:
                    normalized = {}
                    for key, value in row.items():
                        if key is None:
                            continue
                        key_lower = key.strip().lower().replace(" ", "_")
                        mapped_key = default_mapping.get(key_lower, key_lower)
                        normalized[mapped_key] = value.strip() if isinstance(value, str) else value
                    normalized_rows.append(normalized)

                # Get existing expenses for duplicate checking
                existing_expenses = set()
                if skip_duplicates:
                    for e in self.store.list_expenses():
                        key = (round(e.amount, 2), e.category.lower(), str(e.expense_date))
                        existing_expenses.add(key)

                for row in normalized_rows:
                    try:
                        # Parse amount
                        amount_str = row.get("amount", "").strip()
                        if not amount_str:
                            result.skipped += 1
                            continue
                        # Handle currency formatting: remove $ € £ etc and commas
                        amount_str = amount_str.replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
                        if not amount_str:
                            result.skipped += 1
                            continue
                        amount = float(amount_str)
                        if amount <= 0:
                            result.skipped += 1
                            continue

                        # Parse date
                        date_str = row.get("date", "").strip()
                        if date_str:
                            try:
                                expense_date = date.fromisoformat(date_str)
                            except ValueError:
                                # Try common formats
                                for fmt in ["%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%Y/%m/%d"]:
                                    try:
                                        expense_date = datetime.strptime(date_str, fmt).date()
                                        break
                                    except ValueError:
                                        continue
                                else:
                                    expense_date = date.today()
                        else:
                            expense_date = date.today()

                        # Parse category
                        row_category = row.get("category", "").strip()
                        exp_category = row_category or category or "imported"

                        # Parse description
                        description = row.get("description", "").strip()

                        # Parse vendor
                        vendor = row.get("vendor", "").strip() or None

                        # Parse tags
                        tags_str = row.get("tags", "").strip()
                        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

                        # Parse currency
                        row_currency = row.get("currency", "").strip()
                        exp_currency = row_currency or currency

                        # Duplicate check
                        if skip_duplicates:
                            key = (round(amount, 2), exp_category.lower(), str(expense_date))
                            if key in existing_expenses:
                                result.skipped += 1
                                continue

                        # Create the expense
                        expense = self.add_expense(
                            amount=amount,
                            category=exp_category,
                            description=description,
                            expense_date=expense_date,
                            tags=tags,
                            currency=exp_currency,
                            budget_id=budget_id,
                            vendor=vendor,
                        )
                        result.imported += 1
                        result.expense_ids.append(expense.id)
                        result.total_amount += expense.amount

                        if skip_duplicates:
                            existing_expenses.add((round(amount, 2), exp_category.lower(), str(expense_date)))

                    except (ValueError, KeyError) as e:
                        result.errors.append(f"Row {result.imported + result.skipped + len(result.errors) + 1}: {str(e)}")
                        result.skipped += 1

        except FileNotFoundError:
            raise ValueError(f"CSV file not found: {file_path}")
        except Exception as e:
            raise ValueError(f"Error reading CSV file: {str(e)}")

        return result

    # --- v0.3.0: Spending Analytics ---

    def get_spending_trends(
        self,
        category: Optional[str] = None,
        period_type: str = "monthly",
    ) -> list[SpendingTrend]:
        """Analyze spending trends between current and previous periods.

        Args:
            category: Filter by specific category (None for all categories)
            period_type: Period type ('monthly', 'weekly', 'quarterly')
        """
        today = date.today()

        # Determine period boundaries
        if period_type == "weekly":
            current_start = today - timedelta(days=today.weekday())
            current_end = today
            prev_start = current_start - timedelta(weeks=1)
            prev_end = current_start - timedelta(days=1)
        elif period_type == "quarterly":
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            current_start = today.replace(month=quarter_start_month, day=1)
            current_end = today
            if quarter_start_month <= 3:
                prev_start = today.replace(year=today.year - 1, month=10, day=1)
            else:
                prev_start = today.replace(month=quarter_start_month - 3, day=1)
            prev_end = current_start - timedelta(days=1)
        else:  # monthly
            current_start = today.replace(day=1)
            current_end = today
            if today.month == 1:
                prev_start = today.replace(year=today.year - 1, month=12, day=1)
            else:
                prev_start = today.replace(month=today.month - 1, day=1)
            prev_end = current_start - timedelta(days=1)

        current_period_label = f"{current_start} to {current_end}"
        previous_period_label = f"{prev_start} to {prev_end}"

        # Get expenses for both periods
        current_expenses = self.store.list_expenses(
            category=category,
            start_date=current_start,
            end_date=current_end,
        )
        prev_expenses = self.store.list_expenses(
            category=category,
            start_date=prev_start,
            end_date=prev_end,
        )

        # Filter out cancelled
        current_expenses = [e for e in current_expenses if e.status.value != "cancelled"]
        prev_expenses = [e for e in prev_expenses if e.status.value != "cancelled"]

        # Get all categories present in either period
        categories = set()
        for e in current_expenses:
            categories.add(e.category)
        for e in prev_expenses:
            categories.add(e.category)

        if category:
            categories = {c for c in categories if c.lower() == category.lower()}

        trends = []
        for cat in sorted(categories):
            current_spent = sum(e.amount for e in current_expenses if e.category.lower() == cat.lower())
            prev_spent = sum(e.amount for e in prev_expenses if e.category.lower() == cat.lower())

            change = current_spent - prev_spent
            if prev_spent > 0:
                change_pct = (change / prev_spent) * 100
            elif current_spent > 0:
                change_pct = 100.0  # New spending
            else:
                change_pct = 0.0

            if abs(change_pct) < 5:
                direction = TrendDirection.FLAT
            elif change_pct > 0:
                direction = TrendDirection.UP
            else:
                direction = TrendDirection.DOWN

            trends.append(SpendingTrend(
                category=cat,
                current_period_spending=round(current_spent, 2),
                previous_period_spending=round(prev_spent, 2),
                change_amount=round(change, 2),
                change_percent=round(change_pct, 1),
                direction=direction,
                period_type=period_type,
                current_period=current_period_label,
                previous_period=previous_period_label,
            ))

        return trends

    def get_category_breakdown(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        top_n: int = 10,
    ) -> list[CategoryBreakdown]:
        """Get detailed spending breakdown by category.

        Args:
            start_date: Start of period (defaults to current month)
            end_date: End of period (defaults to today)
            top_n: Number of top categories to return
        """
        today = date.today()
        if not start_date:
            start_date = today.replace(day=1)
        if not end_date:
            end_date = today

        expenses = self.store.list_expenses(start_date=start_date, end_date=end_date)
        expenses = [e for e in expenses if e.status.value != "cancelled"]

        if not expenses:
            return []

        # Group by category
        cat_data: dict[str, dict] = {}
        for e in expenses:
            cat = e.category
            if cat not in cat_data:
                cat_data[cat] = {"amounts": [], "vendors": set()}
            cat_data[cat]["amounts"].append(e.amount)
            if e.vendor:
                cat_data[cat]["vendors"].add(e.vendor)

        total_spending = sum(e.amount for e in expenses)

        breakdowns = []
        for cat, data in sorted(cat_data.items(), key=lambda x: sum(x[1]["amounts"]), reverse=True)[:top_n]:
            amounts = data["amounts"]
            cat_total = sum(amounts)
            percentage = (cat_total / total_spending * 100) if total_spending > 0 else 0

            breakdowns.append(CategoryBreakdown(
                category=cat,
                total=round(cat_total, 2),
                count=len(amounts),
                average=round(cat_total / len(amounts), 2),
                percentage=round(percentage, 1),
                largest_expense=round(max(amounts), 2),
                vendors=sorted(data["vendors"])[:5],
            ))

        return breakdowns

    def compare_periods(
        self,
        period_a_start: date,
        period_a_end: date,
        period_b_start: date,
        period_b_end: date,
    ) -> PeriodComparison:
        """Compare spending between two time periods.

        Args:
            period_a_start: Start of period A (typically the older period)
            period_a_end: End of period A
            period_b_start: Start of period B (typically the newer period)
            period_b_end: End of period B
        """
        expenses_a = self.store.list_expenses(start_date=period_a_start, end_date=period_a_end)
        expenses_b = self.store.list_expenses(start_date=period_b_start, end_date=period_b_end)

        expenses_a = [e for e in expenses_a if e.status.value != "cancelled"]
        expenses_b = [e for e in expenses_b if e.status.value != "cancelled"]

        total_a = sum(e.amount for e in expenses_a)
        total_b = sum(e.amount for e in expenses_b)

        change = total_b - total_a
        change_pct = (change / total_a * 100) if total_a > 0 else (100.0 if total_b > 0 else 0.0)

        if abs(change_pct) < 5:
            direction = TrendDirection.FLAT
        elif change_pct > 0:
            direction = TrendDirection.UP
        else:
            direction = TrendDirection.DOWN

        # Per-category trends
        all_categories = set()
        for e in expenses_a + expenses_b:
            all_categories.add(e.category)

        category_trends = []
        for cat in sorted(all_categories):
            cat_a = sum(e.amount for e in expenses_a if e.category.lower() == cat.lower())
            cat_b = sum(e.amount for e in expenses_b if e.category.lower() == cat.lower())

            cat_change = cat_b - cat_a
            if cat_a > 0:
                cat_change_pct = (cat_change / cat_a) * 100
            elif cat_b > 0:
                cat_change_pct = 100.0
            else:
                cat_change_pct = 0.0

            if abs(cat_change_pct) < 5:
                cat_direction = TrendDirection.FLAT
            elif cat_change_pct > 0:
                cat_direction = TrendDirection.UP
            else:
                cat_direction = TrendDirection.DOWN

            category_trends.append(SpendingTrend(
                category=cat,
                current_period_spending=round(cat_b, 2),
                previous_period_spending=round(cat_a, 2),
                change_amount=round(cat_change, 2),
                change_percent=round(cat_change_pct, 1),
                direction=cat_direction,
                period_type="custom",
                current_period=f"{period_b_start} to {period_b_end}",
                previous_period=f"{period_a_start} to {period_a_end}",
            ))

        return PeriodComparison(
            period_a_start=period_a_start,
            period_a_end=period_a_end,
            period_b_start=period_b_start,
            period_b_end=period_b_end,
            period_a_total=round(total_a, 2),
            period_b_total=round(total_b, 2),
            change_amount=round(change, 2),
            change_percent=round(change_pct, 1),
            direction=direction,
            category_trends=category_trends,
        )

    # --- v0.3.0: Budget Templates ---

    def list_budget_templates(self, category: Optional[str] = None) -> list[BudgetTemplate]:
        """List available budget templates (built-in + custom)."""
        templates = list(BUILTIN_BUDGET_TEMPLATES)

        # Load custom templates from store
        custom_templates = self.store.list_budget_templates()
        templates.extend(custom_templates)

        if category:
            templates = [t for t in templates if t.category.lower() == category.lower() or t.category == "all"]

        return templates

    def get_budget_template(self, template_id: str) -> Optional[BudgetTemplate]:
        """Get a specific budget template by ID."""
        for t in self.list_budget_templates():
            if t.id == template_id:
                return t
        return None

    def create_budget_template(
        self,
        name: str,
        category: str,
        default_limit: float,
        period: BudgetPeriod,
        description: str = "",
        currency: str = "USD",
        suggested_alerts: Optional[list[AlertThreshold]] = None,
        suggested_rules: Optional[list[dict]] = None,
        tags: Optional[list[str]] = None,
    ) -> BudgetTemplate:
        """Create a custom budget template."""
        template = BudgetTemplate(
            name=name,
            description=description,
            category=category,
            default_limit=default_limit,
            period=period,
            currency=currency,
            suggested_alerts=suggested_alerts or [],
            suggested_rules=suggested_rules or [],
            tags=tags or [],
            is_builtin=False,
        )
        return self.store.save_budget_template(template)

    def instantiate_budget_template(
        self,
        template_id: str,
        name: Optional[str] = None,
        limit: Optional[float] = None,
        currency: Optional[str] = None,
    ) -> Budget:
        """Create a budget from a template.

        Args:
            template_id: Template to instantiate
            name: Override template name
            limit: Override template default limit
            currency: Override template currency
        """
        template = self.get_budget_template(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        budget = self.create_budget(
            name=name or template.name,
            limit=limit or template.default_limit,
            period=template.period,
            category=template.category if template.category != "all" else None,
            currency=currency or template.currency,
        )

        # Apply suggested alerts if template has them
        if template.suggested_alerts:
            budget = self.update_alert_thresholds(budget.id, template.suggested_alerts)

        # Create suggested spending rules
        for rule_config in template.suggested_rules:
            try:
                self.create_spending_rule(
                    name=rule_config.get("name", f"Rule from {template.name}"),
                    category=template.category if template.category != "all" else rule_config.get("category", "all"),
                    action=SpendingRuleAction(rule_config.get("action", "warn")),
                    threshold_amount=rule_config.get("threshold_amount"),
                    requires_approval_above=rule_config.get("requires_approval_above"),
                    budget_id=budget.id,
                )
            except (ValueError, KeyError):
                pass  # Skip invalid rules

        return budget

    # --- v0.4.0: Income Tracking ---

    def add_income(
        self,
        amount: float,
        source: str,
        description: str = "",
        income_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        currency: str = "USD",
        status: IncomeStatus = IncomeStatus.RECEIVED,
        invoice_ref: Optional[str] = None,
        metadata: Optional[dict] = None,
        recurring_id: Optional[str] = None,
    ) -> Income:
        """Record a new income entry."""
        if amount <= 0:
            raise ValueError("Income amount must be positive")
        income = Income(
            amount=amount,
            source=source,
            description=description,
            income_date=income_date or date.today(),
            tags=tags or [],
            currency=currency,
            status=status,
            invoice_ref=invoice_ref,
            metadata=metadata or {},
            recurring_id=recurring_id,
        )
        return self.store.save_income(income)

    def update_income(
        self,
        income_id: str,
        amount: Optional[float] = None,
        source: Optional[str] = None,
        description: Optional[str] = None,
        income_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        status: Optional[IncomeStatus] = None,
        invoice_ref: Optional[str] = None,
    ) -> Income:
        """Update an existing income entry."""
        income = self.store.get_income(income_id)
        if not income:
            raise ValueError(f"Income {income_id} not found")
        if amount is not None:
            if amount <= 0:
                raise ValueError("Income amount must be positive")
            income.amount = amount
        if source is not None:
            income.source = source
        if description is not None:
            income.description = description
        if income_date is not None:
            income.income_date = income_date
        if tags is not None:
            income.tags = tags
        if status is not None:
            income.status = status
        if invoice_ref is not None:
            income.invoice_ref = invoice_ref
        return self.store.save_income(income)

    def delete_income(self, income_id: str) -> bool:
        return self.store.delete_income(income_id)

    def list_income(
        self,
        source: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        tags: Optional[list[str]] = None,
        status: Optional[str] = None,
    ) -> list[Income]:
        return self.store.list_income(
            source=source, start_date=start_date, end_date=end_date,
            tags=tags, status=status,
        )

    def get_income(self, income_id: str) -> Optional[Income]:
        return self.store.get_income(income_id)

    def get_total_income(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source: Optional[str] = None,
    ) -> float:
        """Get total income for a period."""
        incomes = self.list_income(start_date=start_date, end_date=end_date, source=source)
        return sum(i.amount for i in incomes if i.status != IncomeStatus.CANCELLED)

    def get_income_summary(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict[str, float]:
        """Get income breakdown by source."""
        incomes = self.list_income(start_date=start_date, end_date=end_date)
        summary: dict[str, float] = {}
        for inc in incomes:
            if inc.status == IncomeStatus.CANCELLED:
                continue
            summary[inc.source] = summary.get(inc.source, 0.0) + inc.amount
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))

    # --- v0.4.0: Recurring Income ---

    def add_recurring_income(
        self,
        name: str,
        amount: float,
        source: str,
        frequency: RecurringFrequency,
        description: str = "",
        currency: str = "USD",
        tags: Optional[list[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> RecurringIncome:
        """Create a recurring income template."""
        if amount <= 0:
            raise ValueError("Recurring income amount must be positive")
        recurring = RecurringIncome(
            name=name,
            amount=amount,
            source=source,
            frequency=frequency,
            description=description,
            currency=currency,
            tags=tags or [],
            start_date=start_date or date.today(),
            end_date=end_date,
            next_due=start_date or date.today(),
        )
        return self.store.save_recurring_income(recurring)

    def process_recurring_income(self, ref_date: Optional[date] = None) -> list[Income]:
        """Process all due recurring income, generating income entries."""
        ref = ref_date or date.today()
        recurring_list = self.store.list_recurring_income(active_only=True)
        generated: list[Income] = []

        for rec in recurring_list:
            # Check if end_date has passed
            if rec.end_date and ref > rec.end_date:
                continue

            # Process all due occurrences
            while rec.next_due <= ref:
                if rec.end_date and rec.next_due > rec.end_date:
                    break

                income = self.add_income(
                    amount=rec.amount,
                    source=rec.source,
                    description=rec.description or rec.name,
                    income_date=rec.next_due,
                    tags=rec.tags,
                    currency=rec.currency,
                    recurring_id=rec.id,
                )
                generated.append(income)
                rec.next_due = rec.advance_next_due()

            self.store.save_recurring_income(rec)

        return generated

    def list_recurring_income(self, active_only: bool = False) -> list[RecurringIncome]:
        return self.store.list_recurring_income(active_only=active_only)

    def get_recurring_income(self, recurring_id: str) -> Optional[RecurringIncome]:
        return self.store.get_recurring_income(recurring_id)

    def delete_recurring_income(self, recurring_id: str) -> bool:
        return self.store.delete_recurring_income(recurring_id)

    def pause_recurring_income(self, recurring_id: str) -> RecurringIncome:
        rec = self.store.get_recurring_income(recurring_id)
        if not rec:
            raise ValueError(f"Recurring income {recurring_id} not found")
        rec.active = False
        return self.store.save_recurring_income(rec)

    def resume_recurring_income(self, recurring_id: str) -> RecurringIncome:
        rec = self.store.get_recurring_income(recurring_id)
        if not rec:
            raise ValueError(f"Recurring income {recurring_id} not found")
        rec.active = True
        return self.store.save_recurring_income(rec)

    # --- v0.4.0: Cash Flow Analysis ---

    def get_cash_flow(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        currency: str = "USD",
    ) -> CashFlowSummary:
        """Get cash flow analysis for a period."""
        end = end_date or date.today()
        if start_date is None:
            # Default to current month
            start = end.replace(day=1)
        else:
            start = start_date

        incomes = self.list_income(start_date=start, end_date=end)
        expenses = self.store.list_expenses(start_date=start, end_date=end)

        total_income = sum(i.amount for i in incomes if i.status != IncomeStatus.CANCELLED)
        total_expenses = sum(e.amount for e in expenses if e.status.value != "cancelled")

        net = total_income - total_expenses

        # Calculate ratios
        if total_income > 0:
            savings_rate = (net / total_income) * 100
            expense_ratio = (total_expenses / total_income) * 100
        else:
            savings_rate = 0.0
            expense_ratio = 0.0

        # Find largest income source and expense category
        income_by_source: dict[str, float] = {}
        for inc in incomes:
            if inc.status != IncomeStatus.CANCELLED:
                income_by_source[inc.source] = income_by_source.get(inc.source, 0.0) + inc.amount
        largest_source = max(income_by_source, key=lambda k: income_by_source[k]) if income_by_source else None

        expense_by_cat: dict[str, float] = {}
        for exp in expenses:
            if exp.status.value != "cancelled":
                expense_by_cat[exp.category] = expense_by_cat.get(exp.category, 0.0) + exp.amount
        largest_cat = max(expense_by_cat, key=lambda k: expense_by_cat[k]) if expense_by_cat else None

        return CashFlowSummary(
            start_date=start,
            end_date=end,
            total_income=total_income,
            total_expenses=total_expenses,
            net_cash_flow=net,
            savings_rate=round(savings_rate, 2),
            expense_ratio=round(expense_ratio, 2),
            income_count=len(incomes),
            expense_count=len(expenses),
            largest_income_source=largest_source,
            largest_expense_category=largest_cat,
            currency=currency,
            is_profitable=net > 0,
        )

    def get_burn_rate(self, months: int = 3, currency: str = "USD") -> BurnRate:
        """Calculate burn rate and runway over the past N months."""
        if months < 1:
            raise ValueError("months must be at least 1")

        today = date.today()
        period_start = today - timedelta(days=30 * months)

        total_expenses = 0.0
        total_income = 0.0
        monthly_burns: list[float] = []

        # Calculate per-month burn for trend analysis
        for m in range(months):
            month_start = today - timedelta(days=30 * (m + 1))
            month_end = today - timedelta(days=30 * m)
            month_expenses = sum(
                e.amount for e in self.store.list_expenses(start_date=month_start, end_date=month_end)
                if e.status.value != "cancelled"
            )
            month_income = sum(
                i.amount for i in self.list_income(start_date=month_start, end_date=month_end)
                if i.status != IncomeStatus.CANCELLED
            )
            monthly_burns.append(month_expenses - month_income)

        total_expenses = sum(
            e.amount for e in self.store.list_expenses(start_date=period_start)
            if e.status.value != "cancelled" and e.expense_date <= today
        )
        total_income = sum(
            i.amount for i in self.list_income(start_date=period_start)
            if i.status != IncomeStatus.CANCELLED and i.income_date <= today
        )

        avg_monthly_burn = total_expenses / months
        avg_monthly_income = total_income / months
        net_burn = avg_monthly_burn - avg_monthly_income

        # Total savings across all goals
        savings_goals = self.store.list_savings_goals()
        total_savings = sum(g.current_amount for g in savings_goals)

        # Calculate runway
        if net_burn <= 0:
            runway_months = None
            projected_depletion = None
            is_sustainable = True
        else:
            runway_months = total_savings / net_burn if net_burn > 0 else None
            if runway_months is not None:
                projected_depletion = today + timedelta(days=int(runway_months * 30))
            else:
                projected_depletion = None
            is_sustainable = False

        # Determine burn trend
        if len(monthly_burns) >= 2:
            recent = monthly_burns[0]  # Most recent month (index 0)
            older = monthly_burns[-1]  # Oldest month
            if recent > older * 1.1:
                burn_trend = TrendDirection.UP
            elif recent < older * 0.9:
                burn_trend = TrendDirection.DOWN
            else:
                burn_trend = TrendDirection.FLAT
        else:
            burn_trend = TrendDirection.FLAT

        return BurnRate(
            avg_monthly_burn=round(avg_monthly_burn, 2),
            avg_monthly_income=round(avg_monthly_income, 2),
            net_burn=round(net_burn, 2),
            runway_months=round(runway_months, 1) if runway_months else None,
            total_savings=total_savings,
            analysis_period_months=months,
            is_sustainable=is_sustainable,
            currency=currency,
            burn_trend=burn_trend,
            projected_depletion=projected_depletion,
        )

    def get_financial_dashboard(self, currency: str = "USD") -> FinancialDashboard:
        """Get a comprehensive financial health dashboard."""
        today = date.today()
        budgets = self.store.list_budgets(active_only=True)
        alerts = self.store.list_alerts()
        savings_goals = self.store.list_savings_goals()

        # Budget metrics
        total_remaining = 0.0
        total_limit = 0.0
        over_limit = 0
        for budget in budgets:
            spent = self.get_spending_for_budget(budget.id, ref_date=today)
            effective_limit = budget.effective_limit
            total_limit += effective_limit
            total_remaining += max(0, effective_limit - spent)
            if spent > effective_limit:
                over_limit += 1

        # Savings metrics
        total_savings = sum(g.current_amount for g in savings_goals if g.status != SavingsGoalStatus.COMPLETED)
        total_savings += sum(g.current_amount for g in savings_goals if g.status == SavingsGoalStatus.COMPLETED)
        total_targets = sum(g.target_amount for g in savings_goals)
        savings_pct = (total_savings / total_targets * 100) if total_targets > 0 else 0.0

        # Current month cash flow
        month_start = today.replace(day=1)
        cash_flow = self.get_cash_flow(start_date=month_start, end_date=today, currency=currency)

        # Burn rate
        burn_rate = self.get_burn_rate(months=3, currency=currency)

        # Top spending categories this month
        category_summary = self.get_category_summary(start_date=month_start, end_date=today)
        top_categories = list(category_summary.keys())[:5]

        # Calculate health score (0-100)
        health_score = 0.0
        # 1. Profitability (30 points)
        if cash_flow.is_profitable:
            health_score += 30
        elif cash_flow.total_income > 0 and cash_flow.expense_ratio < 90:
            health_score += 15

        # 2. Budget adherence (25 points)
        if len(budgets) > 0:
            budget_health = 1.0 - (over_limit / len(budgets))
            health_score += 25 * budget_health
        else:
            health_score += 10  # Some credit for having no budgets (neutral)

        # 3. Savings (20 points)
        if total_targets > 0:
            health_score += 20 * min(1.0, savings_pct / 100)
        elif total_savings > 0:
            health_score += 10

        # 4. Burn rate sustainability (15 points)
        if burn_rate.is_sustainable:
            health_score += 15
        elif burn_rate.runway_months and burn_rate.runway_months > 6:
            health_score += 8
        elif burn_rate.runway_months and burn_rate.runway_months > 3:
            health_score += 4

        # 5. Alert health (10 points)
        critical_alerts = sum(1 for a in alerts if a.level == AlertLevel.CRITICAL)
        warning_alerts = sum(1 for a in alerts if a.level == AlertLevel.WARNING)
        alert_penalty = min(10, critical_alerts * 5 + warning_alerts * 2)
        health_score += 10 - alert_penalty

        health_score = max(0, min(100, round(health_score, 1)))

        if health_score >= 80:
            health_status = "excellent"
        elif health_score >= 60:
            health_status = "good"
        elif health_score >= 40:
            health_status = "fair"
        elif health_score >= 20:
            health_status = "poor"
        else:
            health_status = "critical"

        return FinancialDashboard(
            as_of=today,
            total_budget_remaining=round(total_remaining, 2),
            total_budget_limit=round(total_limit, 2),
            total_savings=round(total_savings, 2),
            total_savings_targets=round(total_targets, 2),
            savings_progress_pct=round(savings_pct, 1),
            active_budgets=len(budgets),
            budgets_over_limit=over_limit,
            active_alerts=len(alerts),
            monthly_cash_flow=cash_flow,
            burn_rate=burn_rate,
            health_score=health_score,
            health_status=health_status,
            currency=currency,
            top_categories=top_categories,
        )
