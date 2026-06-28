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
            }
            return json.dumps(data, indent=2, default=str)

        elif format == "csv":
            lines = ["type,id,name/description,amount,category,date,currency"]
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
