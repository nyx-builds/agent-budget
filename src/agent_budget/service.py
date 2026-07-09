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
    CostGuardrail, GuardrailScope, GuardrailAction, GuardrailDecision,
    KillSwitch, CostAlertEvent,
    SpendProjection, LoopDetectionConfig, LoopDetectionResult,
    WebhookConfig,
    WebhookDelivery,
    WebhookEvent,
    ProjectionIntegration,
    ThrottleTier, DEFAULT_THROTTLE_TIERS,
    SpendReservation, ReservationStatus,
    SpendAnomalyRule, AnomalyEvent, AnomalyType, AnomalySeverity,
    AnomalyAction, AnomalySummary,
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

    # --- v0.5.0 Cost Guardrails ---

    def create_guardrail(
        self,
        name: str,
        scope: GuardrailScope,
        scope_id: Optional[str] = None,
        daily_limit_usd: Optional[float] = None,
        hourly_limit_usd: Optional[float] = None,
        per_call_limit_usd: Optional[float] = None,
        monthly_limit_usd: Optional[float] = None,
        warn_at_percent: float = 80.0,
        block_at_percent: float = 100.0,
        cooldown_minutes: int = 0,
        throttle_enabled: bool = False,
        throttle_tiers: Optional[list] = None,
        enabled: bool = True,
        priority: int = 0,
        description: str = "",
    ) -> CostGuardrail:
        """Create a cost guardrail.

        At least one limit (daily, hourly, per_call, monthly) must be set.

        v0.8.0: If ``throttle_enabled`` is True, progressive cost throttling
        is activated between warn and block thresholds. ``throttle_tiers``
        overrides the default tiers if provided.
        """
        if not any([daily_limit_usd, hourly_limit_usd, per_call_limit_usd, monthly_limit_usd]):
            raise ValueError("At least one limit must be set (daily, hourly, per_call, or monthly)")

        guardrail = CostGuardrail(
            name=name,
            scope=scope,
            scope_id=scope_id,
            daily_limit_usd=daily_limit_usd,
            hourly_limit_usd=hourly_limit_usd,
            per_call_limit_usd=per_call_limit_usd,
            monthly_limit_usd=monthly_limit_usd,
            warn_at_percent=warn_at_percent,
            block_at_percent=block_at_percent,
            cooldown_minutes=cooldown_minutes,
            throttle_enabled=throttle_enabled,
            throttle_tiers=throttle_tiers if throttle_tiers is not None else list(DEFAULT_THROTTLE_TIERS),
            enabled=enabled,
            priority=priority,
            description=description,
        )
        return self.store.save_guardrail(guardrail)

    def update_guardrail(
        self,
        guardrail_id: str,
        name: Optional[str] = None,
        daily_limit_usd: Optional[float] = None,
        hourly_limit_usd: Optional[float] = None,
        per_call_limit_usd: Optional[float] = None,
        monthly_limit_usd: Optional[float] = None,
        warn_at_percent: Optional[float] = None,
        block_at_percent: Optional[float] = None,
        cooldown_minutes: Optional[int] = None,
        throttle_enabled: Optional[bool] = None,
        throttle_tiers: Optional[list] = None,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
        description: Optional[str] = None,
    ) -> CostGuardrail:
        """Update an existing guardrail."""
        guardrail = self.store.get_guardrail(guardrail_id)
        if not guardrail:
            raise ValueError(f"Guardrail {guardrail_id} not found")

        if name is not None:
            guardrail.name = name
        if daily_limit_usd is not None:
            guardrail.daily_limit_usd = daily_limit_usd
        if hourly_limit_usd is not None:
            guardrail.hourly_limit_usd = hourly_limit_usd
        if per_call_limit_usd is not None:
            guardrail.per_call_limit_usd = per_call_limit_usd
        if monthly_limit_usd is not None:
            guardrail.monthly_limit_usd = monthly_limit_usd
        if warn_at_percent is not None:
            guardrail.warn_at_percent = warn_at_percent
        if block_at_percent is not None:
            guardrail.block_at_percent = block_at_percent
        if cooldown_minutes is not None:
            guardrail.cooldown_minutes = cooldown_minutes
        if throttle_enabled is not None:
            guardrail.throttle_enabled = throttle_enabled
        if throttle_tiers is not None:
            guardrail.throttle_tiers = throttle_tiers
        if enabled is not None:
            guardrail.enabled = enabled
        if priority is not None:
            guardrail.priority = priority
        if description is not None:
            guardrail.description = description

        from datetime import timezone
        guardrail.updated_at = datetime.now(timezone.utc)
        return self.store.save_guardrail(guardrail)

    def list_guardrails(self, enabled_only: bool = False) -> list[CostGuardrail]:
        """List all guardrails, sorted by priority (highest first)."""
        return self.store.list_guardrails(enabled_only=enabled_only)

    def get_guardrail(self, guardrail_id: str) -> Optional[CostGuardrail]:
        """Get a guardrail by ID."""
        return self.store.get_guardrail(guardrail_id)

    def delete_guardrail(self, guardrail_id: str) -> bool:
        """Delete a guardrail."""
        return self.store.delete_guardrail(guardrail_id)

    def _get_spend_for_period(
        self,
        scope: GuardrailScope,
        scope_id: Optional[str],
        period_start: datetime,
        now: datetime,
        include_reservations: bool = False,
    ) -> float:
        """Get total LLM spend for a scope in a time period.

        When ``include_reservations`` is True (v0.9.0), active spend
        reservations are added to the settled spend total.  This gives
        guardrail checks a view of *committed* spend, preventing the
        parallel-agent race where N calls all read the same under-limit
        total and fire past the ceiling.
        """
        records = self.store.list_llm_usage(from_date=period_start.date())
        total = 0.0
        for r in records:
            # Filter by time if period is sub-daily
            if r.recorded_at < period_start:
                continue
            if r.recorded_at > now:
                continue
            # Filter by scope
            if scope == GuardrailScope.GLOBAL:
                total += r.cost_usd
            elif scope == GuardrailScope.AGENT and scope_id:
                if r.agent_id and r.agent_id.lower() == scope_id.lower():
                    total += r.cost_usd
            elif scope == GuardrailScope.MODEL and scope_id:
                if r.model_id.lower() == scope_id.lower():
                    total += r.cost_usd
            # For BUDGET and TASK scope, check metadata
            elif scope == GuardrailScope.TASK and scope_id:
                if r.task_id and r.task_id.lower() == scope_id.lower():
                    total += r.cost_usd
            elif scope == GuardrailScope.BUDGET and scope_id:
                if r.metadata.get("budget_id", "").lower() == scope_id.lower():
                    total += r.cost_usd

        if include_reservations:
            total += self._reserved_spend_for_scope(scope, scope_id, now)
        return total

    def _reserved_spend_for_scope(
        self,
        scope: GuardrailScope,
        scope_id: Optional[str],
        now: datetime,
    ) -> float:
        """Sum the reserved amounts of all ACTIVE reservations matching this scope.

        Global scope counts all active reservations; scoped checks match on
        the reservation's agent_id / model_id / task_id / budget_id field.
        """
        reservations = self.store.list_reservations(active_only=True, now=now)
        total = 0.0
        for rsv in reservations:
            if scope == GuardrailScope.GLOBAL:
                total += rsv.reserved_amount_usd
            elif scope == GuardrailScope.AGENT and scope_id:
                if rsv.agent_id and rsv.agent_id.lower() == scope_id.lower():
                    total += rsv.reserved_amount_usd
            elif scope == GuardrailScope.MODEL and scope_id:
                if rsv.model_id and rsv.model_id.lower() == scope_id.lower():
                    total += rsv.reserved_amount_usd
            elif scope == GuardrailScope.TASK and scope_id:
                if rsv.task_id and rsv.task_id.lower() == scope_id.lower():
                    total += rsv.reserved_amount_usd
            elif scope == GuardrailScope.BUDGET and scope_id:
                if rsv.budget_id and rsv.budget_id.lower() == scope_id.lower():
                    total += rsv.reserved_amount_usd
        return total

    def _get_active_cooldown(
        self,
        guardrail_id: str,
        now: datetime,
    ) -> Optional[datetime]:
        """Check if a guardrail is in active cooldown."""
        # Look for recent block/throttle alerts
        alerts = self.store.list_cost_alerts(guardrail_id=guardrail_id, unacknowledged_only=False)
        for alert in alerts:
            if alert.level == AlertLevel.CRITICAL:
                cooldown_end = alert.triggered_at + timedelta(minutes=0)  # Will be set by caller
                # Check if there's an active cooldown
                pass
        # Simplified: check for cost alert metadata in store
        return None

    def check_guardrails(
        self,
        estimated_cost_usd: float = 0.0,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        budget_id: Optional[str] = None,
        task_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> GuardrailDecision:
        """Pre-flight check: should the agent proceed with this LLM call?

        This is the core guardrail function. Agents call this BEFORE making
        an LLM call to check if they're within budget. Returns a decision
        with allow/deny + reason.

        Args:
            estimated_cost_usd: Estimated cost of the upcoming call
            agent_id: Agent making the call
            model_id: Model being called
            budget_id: Associated budget
            task_id: Task/session ID
            now: Override current time (for testing)

        Returns:
            GuardrailDecision with allowed=True/False and details
        """
        now = now or datetime.now(timezone.utc)

        # 1. Check kill switch first
        kill_switch = self.store.get_kill_switch()
        if kill_switch.is_active(now):
            return GuardrailDecision(
                allowed=False,
                action=GuardrailAction.KILL,
                reason=f"KILL SWITCH ACTIVE: {kill_switch.reason}. All LLM calls blocked. Triggered at {kill_switch.triggered_at.isoformat() if kill_switch.triggered_at else 'unknown'}.",
                current_spend_usd=0.0,
                suggestions=["Reset the kill switch if this is intentional", "Contact the operator who triggered it"],
            )

        # 2. Check per-call limit (global check, applies to all guardrails)
        guardrails = self.store.list_guardrails(enabled_only=True)
        if not guardrails:
            return GuardrailDecision(
                allowed=True,
                action=GuardrailAction.ALLOW,
                reason="No guardrails configured — all calls allowed",
                current_spend_usd=0.0,
            )

        # 3. Check each applicable guardrail (priority order)
        worst_decision: Optional[GuardrailDecision] = None

        for g in guardrails:
            # Determine if this guardrail applies
            applies = False
            check_scope_id = None

            if g.scope == GuardrailScope.GLOBAL:
                applies = True
            elif g.scope == GuardrailScope.AGENT and agent_id:
                if g.scope_id is None or g.scope_id.lower() == agent_id.lower():
                    applies = True
                    check_scope_id = agent_id
            elif g.scope == GuardrailScope.MODEL and model_id:
                if g.scope_id is None or g.scope_id.lower() == model_id.lower():
                    applies = True
                    check_scope_id = model_id
            elif g.scope == GuardrailScope.BUDGET and budget_id:
                if g.scope_id is None or g.scope_id.lower() == budget_id.lower():
                    applies = True
                    check_scope_id = budget_id
            elif g.scope == GuardrailScope.TASK and task_id:
                if g.scope_id is None or g.scope_id.lower() == task_id.lower():
                    applies = True
                    check_scope_id = task_id

            if not applies:
                continue

            # Check per-call limit
            if g.per_call_limit_usd is not None and estimated_cost_usd > g.per_call_limit_usd:
                decision = GuardrailDecision(
                    allowed=False,
                    action=GuardrailAction.BLOCK,
                    reason=f"Per-call cost ${estimated_cost_usd:.4f} exceeds limit ${g.per_call_limit_usd:.4f} (guardrail: {g.name})",
                    guardrail_id=g.id,
                    limit_usd=g.per_call_limit_usd,
                    percent_used=100.0,
                    suggestions=[
                        f"Reduce token count or use a cheaper model",
                        f"Per-call limit is ${g.per_call_limit_usd:.4f}",
                    ],
                )
                self._record_cost_alert(g, decision, AlertLevel.CRITICAL)
                if worst_decision is None or decision.action.value > worst_decision.action.value:
                    worst_decision = decision
                continue

            # Check daily limit
            if g.daily_limit_usd is not None:
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                daily_spend = self._get_spend_for_period(g.scope, check_scope_id, day_start, now)
                projected = daily_spend + estimated_cost_usd
                pct = (projected / g.daily_limit_usd * 100) if g.daily_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Daily limit ${g.daily_limit_usd:.2f} {'exceeded' if pct >= 100 else f'{g.block_at_percent:.0f}% reached'} — current spend ${daily_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=daily_spend,
                        limit_usd=g.daily_limit_usd,
                        percent_used=pct,
                        suggestions=self._cost_suggestions(g, projected, g.daily_limit_usd),
                    )
                    self._record_cost_alert(g, decision, AlertLevel.CRITICAL)
                    # Check cooldown
                    if g.cooldown_minutes > 0:
                        decision.cooldown_until = now + timedelta(minutes=g.cooldown_minutes)
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif g.throttle_enabled:
                    # Check progressive throttle tiers
                    throttle_decision = self._check_throttle_tier(
                        g, estimated_cost_usd, pct, daily_spend, g.daily_limit_usd, "daily"
                    )
                    if throttle_decision:
                        self._record_cost_alert(g, throttle_decision, AlertLevel.WARNING)
                        if worst_decision is None or (worst_decision.allowed and not throttle_decision.allowed):
                            worst_decision = throttle_decision
                        elif worst_decision and worst_decision.action == GuardrailAction.WARN and throttle_decision.action == GuardrailAction.THROTTLE:
                            worst_decision = throttle_decision
                        continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Daily spend approaching limit — ${daily_spend:.2f}/${g.daily_limit_usd:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=daily_spend,
                        limit_usd=g.daily_limit_usd,
                        percent_used=pct,
                    )
                    self._record_cost_alert(g, decision, AlertLevel.WARNING)
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

            # Check hourly limit
            if g.hourly_limit_usd is not None:
                hour_start = now - timedelta(hours=1)
                hourly_spend = self._get_spend_for_period(g.scope, check_scope_id, hour_start, now)
                projected = hourly_spend + estimated_cost_usd
                pct = (projected / g.hourly_limit_usd * 100) if g.hourly_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Hourly limit ${g.hourly_limit_usd:.2f} {'exceeded' if pct >= 100 else f'{g.block_at_percent:.0f}% reached'} — current spend ${hourly_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=hourly_spend,
                        limit_usd=g.hourly_limit_usd,
                        percent_used=pct,
                    )
                    self._record_cost_alert(g, decision, AlertLevel.CRITICAL)
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif g.throttle_enabled:
                    throttle_decision = self._check_throttle_tier(
                        g, estimated_cost_usd, pct, hourly_spend, g.hourly_limit_usd, "hourly"
                    )
                    if throttle_decision:
                        self._record_cost_alert(g, throttle_decision, AlertLevel.WARNING)
                        if worst_decision is None or (worst_decision.allowed and not throttle_decision.allowed):
                            worst_decision = throttle_decision
                        elif worst_decision and worst_decision.action == GuardrailAction.WARN and throttle_decision.action == GuardrailAction.THROTTLE:
                            worst_decision = throttle_decision
                        continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Hourly spend approaching limit — ${hourly_spend:.2f}/${g.hourly_limit_usd:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=hourly_spend,
                        limit_usd=g.hourly_limit_usd,
                        percent_used=pct,
                    )
                    self._record_cost_alert(g, decision, AlertLevel.WARNING)
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

            # Check monthly limit
            if g.monthly_limit_usd is not None:
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_spend = self._get_spend_for_period(g.scope, check_scope_id, month_start, now)
                projected = monthly_spend + estimated_cost_usd
                pct = (projected / g.monthly_limit_usd * 100) if g.monthly_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Monthly limit ${g.monthly_limit_usd:.2f} {'exceeded' if pct >= 100 else f'{g.block_at_percent:.0f}% reached'} — current spend ${monthly_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=monthly_spend,
                        limit_usd=g.monthly_limit_usd,
                        percent_used=pct,
                    )
                    self._record_cost_alert(g, decision, AlertLevel.CRITICAL)
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif g.throttle_enabled:
                    throttle_decision = self._check_throttle_tier(
                        g, estimated_cost_usd, pct, monthly_spend, g.monthly_limit_usd, "monthly"
                    )
                    if throttle_decision:
                        self._record_cost_alert(g, throttle_decision, AlertLevel.WARNING)
                        if worst_decision is None or (worst_decision.allowed and not throttle_decision.allowed):
                            worst_decision = throttle_decision
                        elif worst_decision and worst_decision.action == GuardrailAction.WARN and throttle_decision.action == GuardrailAction.THROTTLE:
                            worst_decision = throttle_decision
                        continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Monthly spend approaching limit — ${monthly_spend:.2f}/${g.monthly_limit_usd:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=monthly_spend,
                        limit_usd=g.monthly_limit_usd,
                        percent_used=pct,
                    )
                    self._record_cost_alert(g, decision, AlertLevel.WARNING)
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

        # Fire webhooks if any guardrail triggered
        if worst_decision:
            if worst_decision.action == GuardrailAction.KILL:
                wh_event = WebhookEvent.GUARDRAIL_KILL
            elif worst_decision.action == GuardrailAction.BLOCK:
                wh_event = WebhookEvent.GUARDRAIL_BLOCK
            elif worst_decision.action == GuardrailAction.THROTTLE:
                wh_event = WebhookEvent.BUDGET_THRESHOLD
            elif worst_decision.action == GuardrailAction.WARN:
                wh_event = WebhookEvent.GUARDRAIL_WARN
            else:
                wh_event = None

            if wh_event:
                worst_decision.webhooks_fired = self._fire_webhooks(
                    wh_event,
                    {
                        "event": wh_event.value,
                        "allowed": worst_decision.allowed,
                        "action": worst_decision.action.value,
                        "reason": worst_decision.reason,
                        "guardrail_id": worst_decision.guardrail_id,
                        "current_spend_usd": worst_decision.current_spend_usd,
                        "limit_usd": worst_decision.limit_usd,
                        "percent_used": worst_decision.percent_used,
                        "estimated_cost_usd": estimated_cost_usd,
                        "agent_id": agent_id,
                        "model_id": model_id,
                        "budget_id": budget_id,
                        "task_id": task_id,
                        "throttle_tier": worst_decision.throttle_tier,
                        "max_recommended_cost_usd": worst_decision.max_recommended_cost_usd,
                        "recommended_model": worst_decision.recommended_model,
                        "suggestions": worst_decision.suggestions,
                        "timestamp": now.isoformat(),
                    },
                )

            return worst_decision

        return GuardrailDecision(
            allowed=True,
            action=GuardrailAction.ALLOW,
            reason="All guardrails passed — within all limits",
            current_spend_usd=0.0,
        )

    def _check_throttle_tier(
        self,
        guardrail: CostGuardrail,
        estimated_cost_usd: float,
        pct: float,
        current_spend: float,
        limit: float,
        period_label: str,
    ) -> Optional[GuardrailDecision]:
        """Check if the current spend level triggers a throttle tier.

        Progressive throttling sits between WARN and BLOCK. Instead of a
        binary allow/deny, it recommends a max per-call cost and optionally
        a cheaper model. If ``block_if_exceeded`` is True on the active tier,
        calls above ``max_cost_usd`` are hard-blocked.

        Returns a GuardrailDecision if a throttle tier is active, or None.
        """
        tier = guardrail.get_active_throttle_tier(pct)
        if tier is None:
            return None

        # Determine if this call exceeds the tier's max cost
        exceeds = (
            tier.max_cost_usd is not None
            and estimated_cost_usd > tier.max_cost_usd
        )

        blocked = exceeds and tier.block_if_exceeded

        suggestions = []
        if tier.max_cost_usd is not None:
            suggestions.append(f"Reduce per-call cost to ≤${tier.max_cost_usd:.4f}")
        if tier.recommended_model:
            suggestions.append(f"Switch to model: {tier.recommended_model}")
        suggestions.append(f"Current tier: {tier.threshold_percent:.0f}% of {period_label} limit")

        reason_parts = [
            f"THROTTLE @ {tier.threshold_percent:.0f}%: {tier.message}" if tier.message
            else f"THROTTLE: {period_label} spend at {pct:.1f}% (${current_spend:.2f}/${limit:.2f})",
        ]
        if tier.max_cost_usd is not None:
            reason_parts.append(f"max per-call: ${tier.max_cost_usd:.4f}")
        if tier.recommended_model:
            reason_parts.append(f"recommended model: {tier.recommended_model}")
        if blocked:
            reason_parts.append(f"COST BLOCKED: ${estimated_cost_usd:.4f} exceeds tier max ${tier.max_cost_usd:.4f}")

        return GuardrailDecision(
            allowed=not blocked,
            action=GuardrailAction.BLOCK if blocked else GuardrailAction.THROTTLE,
            reason=" — ".join(reason_parts),
            guardrail_id=guardrail.id,
            current_spend_usd=current_spend,
            limit_usd=limit,
            percent_used=pct,
            suggestions=suggestions,
            throttle_tier=f"{tier.threshold_percent:.0f}%",
            max_recommended_cost_usd=tier.max_cost_usd,
            recommended_model=tier.recommended_model,
        )

    def _record_cost_alert(
        self,
        guardrail: CostGuardrail,
        decision: GuardrailDecision,
        level: AlertLevel,
    ) -> None:
        """Record a cost alert event when a guardrail triggers."""
        alert = CostAlertEvent(
            guardrail_id=guardrail.id,
            scope=guardrail.scope,
            scope_id=guardrail.scope_id,
            level=level,
            message=decision.reason,
            current_spend_usd=decision.current_spend_usd,
            limit_usd=decision.limit_usd,
        )
        self.store.save_cost_alert(alert)

    def _cost_suggestions(self, guardrail: CostGuardrail, projected: float, limit: float) -> list[str]:
        """Generate cost-saving suggestions based on the guardrail."""
        suggestions = []
        overage = projected - limit
        if overage > 0:
            suggestions.append(f"Reduce spending by ${overage:.2f} to stay within limit")
        suggestions.append("Consider using a cheaper model (e.g., gpt-4o-mini instead of gpt-4o)")
        suggestions.append("Reduce token count by summarizing context or using caching")
        suggestions.append(f"Guardrail '{guardrail.name}' is set at ${limit:.2f}")
        return suggestions

    # --- Kill Switch ---

    def trigger_kill_switch(
        self,
        reason: str,
        triggered_by: Optional[str] = None,
        expires_in_minutes: Optional[int] = None,
        override_token: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> KillSwitch:
        """Trigger the emergency kill switch — blocks ALL LLM calls.

        Args:
            reason: Why the kill switch is being triggered
            triggered_by: Who/what triggered it
            expires_in_minutes: Auto-reset after N minutes (None = manual only)
            override_token: Token required to reset (for safety)
            now: Override current time (for testing)
        """
        now = now or datetime.now(timezone.utc)
        ks = self.store.get_kill_switch()
        ks.active = True
        ks.reason = reason
        ks.triggered_at = now
        ks.triggered_by = triggered_by
        ks.expires_at = now + timedelta(minutes=expires_in_minutes) if expires_in_minutes else None
        ks.override_token = override_token
        ks.breach_count += 1
        saved_ks = self.store.save_kill_switch(ks)

        # Fire kill switch triggered webhook
        self._fire_webhooks(
            WebhookEvent.KILL_SWITCH_TRIGGERED,
            {
                "event": "kill_switch_triggered",
                "active": True,
                "reason": saved_ks.reason,
                "triggered_by": saved_ks.triggered_by,
                "breach_count": saved_ks.breach_count,
                "timestamp": saved_ks.triggered_at.isoformat() if saved_ks.triggered_at else now.isoformat(),
            },
        )
        return saved_ks

    def reset_kill_switch(self, override_token: Optional[str] = None) -> KillSwitch:
        """Reset the kill switch, allowing LLM calls again.

        Args:
            override_token: Required if the kill switch was set with a token
        """
        ks = self.store.get_kill_switch()
        if not ks.is_active():
            return ks  # Already inactive
        if ks.override_token and override_token != ks.override_token:
            raise ValueError("Invalid override token — kill switch requires authentication to reset")
        ks.active = False
        ks.reason = ""
        ks.triggered_at = None
        ks.triggered_by = None
        ks.expires_at = None
        ks.override_token = None
        saved_ks = self.store.save_kill_switch(ks)

        # Fire kill switch reset webhook
        self._fire_webhooks(
            WebhookEvent.KILL_SWITCH_RESET,
            {
                "event": "kill_switch_reset",
                "active": False,
                "breach_count": saved_ks.breach_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        return saved_ks

    def get_kill_switch_status(self) -> KillSwitch:
        """Get current kill switch status."""
        return self.store.get_kill_switch()

    # --- Cost Alert Management ---

    def list_cost_alerts(
        self,
        guardrail_id: Optional[str] = None,
        unacknowledged_only: bool = False,
        limit: Optional[int] = None,
    ) -> list[CostAlertEvent]:
        """List cost alert events."""
        return self.store.list_cost_alerts(
            guardrail_id=guardrail_id,
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )

    def acknowledge_cost_alert(self, alert_id: str) -> Optional[CostAlertEvent]:
        """Acknowledge a cost alert."""
        return self.store.acknowledge_cost_alert(alert_id)

    def clear_cost_alerts(self, guardrail_id: Optional[str] = None) -> int:
        """Clear cost alerts, optionally filtered by guardrail."""
        return self.store.clear_cost_alerts(guardrail_id=guardrail_id)

    # --- v0.6.0: Spend Projection ---

    def project_spend(
        self,
        scope: GuardrailScope = GuardrailScope.GLOBAL,
        scope_id: Optional[str] = None,
        period: str = "daily",
        now: Optional[datetime] = None,
    ) -> SpendProjection:
        """Project spend for a scope/period and predict if limits will be hit.

        This is the 'burn forecast' — analyzes recent LLM usage to extrapolate
        when guardrail limits will be breached. Agents use this to proactively
        slow down BEFORE a guardrail hard-blocks them.

        Args:
            scope: Guardrail scope to project
            scope_id: Entity ID for scoped projections
            period: 'daily', 'hourly', or 'monthly'
            now: Override current time (for testing)

        Returns:
            SpendProjection with projected spend, ETA, and recommendations
        """
        now = now or datetime.now(timezone.utc)

        # Determine period boundaries
        if period == "hourly":
            period_start = now - timedelta(hours=1)
            period_label = "hourly"
        elif period == "monthly":
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_label = "monthly"
        else:  # daily (default)
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_label = "daily"

        # Get spend data for this period
        records = self.store.list_llm_usage(from_date=period_start.date())
        period_records = []
        for r in records:
            if r.recorded_at < period_start or r.recorded_at > now:
                continue
            if scope == GuardrailScope.GLOBAL:
                period_records.append(r)
            elif scope == GuardrailScope.AGENT and scope_id:
                if r.agent_id and r.agent_id.lower() == scope_id.lower():
                    period_records.append(r)
            elif scope == GuardrailScope.MODEL and scope_id:
                if r.model_id.lower() == scope_id.lower():
                    period_records.append(r)
            elif scope == GuardrailScope.TASK and scope_id:
                if r.task_id and r.task_id.lower() == scope_id.lower():
                    period_records.append(r)
            elif scope == GuardrailScope.BUDGET and scope_id:
                if r.metadata.get("budget_id", "").lower() == scope_id.lower():
                    period_records.append(r)

        current_spend = sum(r.cost_usd for r in period_records)
        call_count = len(period_records)
        avg_cost_per_call = current_spend / call_count if call_count > 0 else 0.0

        # Calculate spend rate (USD/hour) based on elapsed time
        elapsed_minutes = (now - period_start).total_seconds() / 60.0
        elapsed_hours = max(elapsed_minutes / 60.0, 0.01)  # avoid div by zero
        spend_rate = current_spend / elapsed_hours if elapsed_hours > 0 else 0.0

        # Determine remaining time in period
        if period == "hourly":
            remaining_hours = max(((period_start + timedelta(hours=1)) - now).total_seconds() / 3600.0, 0.0)
            period_total_hours = 1.0
        elif period == "monthly":
            # Days in current month
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            remaining_hours = max((next_month - now).total_seconds() / 3600.0, 0.0)
            period_total_hours = ((next_month - period_start).total_seconds() / 3600.0)
        else:  # daily
            end_of_day = now.replace(hour=23, minute=59, second=59)
            remaining_hours = max((end_of_day - now).total_seconds() / 3600.0, 0.0)
            period_total_hours = 24.0

        # Project: current spend + rate * remaining hours
        projected_spend = current_spend + (spend_rate * remaining_hours)

        # Find applicable guardrail for this scope
        applicable_limit = None
        applicable_guardrail = None
        for g in self.store.list_guardrails(enabled_only=True):
            if g.scope != scope:
                continue
            if scope != GuardrailScope.GLOBAL and scope_id and g.scope_id:
                if g.scope_id.lower() != scope_id.lower():
                    continue
            if period == "daily" and g.daily_limit_usd is not None:
                applicable_limit = g.daily_limit_usd
                applicable_guardrail = g
                break
            elif period == "hourly" and g.hourly_limit_usd is not None:
                applicable_limit = g.hourly_limit_usd
                applicable_guardrail = g
                break
            elif period == "monthly" and g.monthly_limit_usd is not None:
                applicable_limit = g.monthly_limit_usd
                applicable_guardrail = g
                break

        # Compute ETA to limit
        eta_minutes = None
        projected_exceeds = False
        will_breach = False
        if applicable_limit and spend_rate > 0:
            remaining_budget = applicable_limit - current_spend
            if remaining_budget > 0:
                eta_hours = remaining_budget / spend_rate
                eta_minutes = eta_hours * 60.0
            else:
                eta_minutes = 0.0  # already over
                projected_exceeds = True
            if projected_spend > applicable_limit:
                projected_exceeds = True
            # Check if guardrail will trigger (at warn/block percent)
            if applicable_guardrail:
                warn_threshold = applicable_limit * (applicable_guardrail.warn_at_percent / 100.0)
                block_threshold = applicable_limit * (applicable_guardrail.block_at_percent / 100.0)
                if projected_spend >= block_threshold or projected_spend >= warn_threshold:
                    will_breach = True

        # Confidence: more data points = higher confidence
        if call_count >= 20:
            confidence = 0.9
        elif call_count >= 10:
            confidence = 0.75
        elif call_count >= 5:
            confidence = 0.6
        elif call_count >= 2:
            confidence = 0.4
        elif call_count >= 1:
            confidence = 0.2
        else:
            confidence = 0.0

        # Build recommendation
        recommendation = self._build_projection_recommendation(
            projected_spend, applicable_limit, eta_minutes, will_breach, spend_rate
        )

        return SpendProjection(
            scope=scope,
            scope_id=scope_id,
            period=period_label,
            current_spend_usd=round(current_spend, 6),
            projected_spend_usd=round(projected_spend, 6),
            spend_rate_per_hour=round(spend_rate, 6),
            limit_usd=applicable_limit,
            projected_exceeds_limit=projected_exceeds,
            eta_minutes_to_limit=round(eta_minutes, 1) if eta_minutes is not None else None,
            will_breach_guardrail=will_breach,
            guardrail_id=applicable_guardrail.id if applicable_guardrail else None,
            call_count_in_period=call_count,
            avg_cost_per_call=round(avg_cost_per_call, 6),
            confidence=confidence,
            recommendation=recommendation,
        )

    def _build_projection_recommendation(
        self,
        projected: float,
        limit: Optional[float],
        eta_minutes: Optional[float],
        will_breach: bool,
        rate: float,
    ) -> str:
        """Build a human-readable recommendation from a projection."""
        if not limit:
            if rate > 0:
                return f"Spending at ${rate:.2f}/hour. No guardrail limit set for this scope — consider adding one."
            return "No spending detected in this period."

        if will_breach:
            if eta_minutes is not None and eta_minutes > 0:
                if eta_minutes < 60:
                    return f"⚠️ At current rate (${rate:.2f}/hr), you'll hit the ${limit:.2f} limit in {eta_minutes:.0f} minutes. Reduce call frequency or switch to a cheaper model now."
                else:
                    return f"⚠️ At current rate (${rate:.2f}/hr), you'll hit the ${limit:.2f} limit in {eta_minutes/60:.1f} hours. Consider throttling."
            return f"⚠️ Projected spend ${projected:.2f} will exceed limit ${limit:.2f}. Take action now."

        if projected > limit * 0.8:
            return f"Projecting ${projected:.2f} of ${limit:.2f} limit ({projected/limit*100:.0f}%). Approaching limit — monitor closely."

        if projected > limit * 0.5:
            return f"Projecting ${projected:.2f} of ${limit:.2f} limit ({projected/limit*100:.0f}%). On track but trending up."

        return f"Projecting ${projected:.2f} of ${limit:.2f} limit ({projected/limit*100:.0f}%). Well within budget."

    # --- v0.6.0: Loop Detection ---

    @staticmethod
    def _call_signature(record) -> str:
        """Create a signature for an LLM call for similarity comparison.

        Uses model_id + input_tokens range + output_tokens range as a proxy
        for call content. Two calls with the same model and similar token
        counts are likely doing similar work.
        """
        model = record.model_id
        # Bucket tokens to 1000s to group near-identical calls
        in_bucket = record.input_tokens // 1000
        out_bucket = record.output_tokens // 1000
        return f"{model}|in:{in_bucket}k|out:{out_bucket}k"

    @staticmethod
    def _jaccard_similarity(set_a: set, set_b: set) -> float:
        """Jaccard similarity between two sets."""
        if not set_a and not set_b:
            return 1.0
        union = set_a | set_b
        if not union:
            return 0.0
        return len(set_a & set_b) / len(union)

    def check_loop(
        self,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> LoopDetectionResult:
        """Check if an agent is in a runaway loop based on recent call patterns.

        Scans recent LLM usage records and detects repeated similar calls
        within the configured detection window. If a loop is detected,
        returns details including recommended action.

        Args:
            agent_id: Agent to check (if None, checks all agents)
            model_id: Filter to specific model
            now: Override current time (for testing)

        Returns:
            LoopDetectionResult with detection status and details
        """
        now = now or datetime.now(timezone.utc)

        configs = self.store.list_loop_configs(enabled_only=True)
        if not configs:
            return LoopDetectionResult(
                detected=False,
                recommendation="No loop detection configs enabled. Configure one to detect runaway loops.",
            )

        # Check each config (most recently created first)
        for config in configs:
            # Scope filtering
            if config.agent_id and agent_id:
                if config.agent_id.lower() != agent_id.lower():
                    continue
            if config.model_id and model_id:
                if config.model_id.lower() != model_id.lower():
                    continue

            window_start = now - timedelta(minutes=config.window_minutes)
            records = self.store.list_llm_usage(from_date=window_start.date())

            # Filter to window and scope
            window_records = []
            for r in records:
                if r.recorded_at < window_start or r.recorded_at > now:
                    continue
                if agent_id and r.agent_id and r.agent_id.lower() != agent_id.lower():
                    continue
                if model_id and r.model_id.lower() != model_id.lower():
                    continue
                window_records.append(r)

            if len(window_records) < config.repeat_threshold:
                continue

            # Group records by call signature
            signatures: dict[str, list] = {}
            for r in window_records:
                sig = self._call_signature(r)
                signatures.setdefault(sig, []).append(r)

            # Check if any signature group exceeds threshold
            for sig, group in signatures.items():
                if len(group) >= config.repeat_threshold:
                    cumulative_cost = sum(r.cost_usd for r in group)
                    # Check min cost threshold
                    if config.min_cost_usd > 0 and cumulative_cost < config.min_cost_usd:
                        continue

                    # Compute average pairwise similarity
                    similarities = []
                    for i in range(len(group)):
                        for j in range(i + 1, len(group)):
                            set_i = {self._call_signature(group[i])}
                            set_j = {self._call_signature(group[j])}
                            similarities.append(self._jaccard_similarity(set_i, set_j))
                    avg_sim = sum(similarities) / len(similarities) if similarities else 1.0

                    if avg_sim >= config.similarity_threshold:
                        # Auto-block?
                        blocked_until = None
                        recommendation_parts = [
                            f"Loop detected: {len(group)} similar calls ('{sig}') in {config.window_minutes} min window.",
                            f"Cumulative cost: ${cumulative_cost:.4f}.",
                            "The agent may be stuck retrying the same operation.",
                        ]
                        if config.auto_block_minutes > 0:
                            blocked_until = now + timedelta(minutes=config.auto_block_minutes)
                            recommendation_parts.append(
                                f"Agent auto-blocked for {config.auto_block_minutes} minutes (until {blocked_until.isoformat()})."
                            )
                        recommendation_parts.append("Review agent logic — consider adding a retry limit or different error handling.")

                        return LoopDetectionResult(
                            detected=True,
                            config_id=config.id,
                            agent_id=agent_id,
                            model_id=group[0].model_id,
                            call_count=len(group),
                            window_minutes=config.window_minutes,
                            cumulative_cost_usd=round(cumulative_cost, 6),
                            avg_similarity=round(avg_sim, 4),
                            sample_signature=sig,
                            recommendation=" ".join(recommendation_parts),
                            blocked_until=blocked_until,
                        )

        return LoopDetectionResult(
            detected=False,
            agent_id=agent_id,
            recommendation="No loops detected in recent call history.",
        )

    def create_loop_config(
        self,
        name: str,
        window_minutes: int = 10,
        repeat_threshold: int = 5,
        similarity_threshold: float = 0.9,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        auto_block_minutes: int = 0,
        min_cost_usd: float = 0.0,
        enabled: bool = True,
    ) -> LoopDetectionConfig:
        """Create a new loop detection configuration."""
        config = LoopDetectionConfig(
            name=name,
            window_minutes=window_minutes,
            repeat_threshold=repeat_threshold,
            similarity_threshold=similarity_threshold,
            agent_id=agent_id,
            model_id=model_id,
            auto_block_minutes=auto_block_minutes,
            min_cost_usd=min_cost_usd,
            enabled=enabled,
        )
        return self.store.save_loop_config(config)

    def update_loop_config(self, config_id: str, **kwargs) -> LoopDetectionConfig:
        """Update a loop detection config. Pass field names as kwargs."""
        config = self.store.get_loop_config(config_id)
        if not config:
            raise ValueError(f"Loop detection config {config_id} not found")
        for key, value in kwargs.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)
        config.updated_at = datetime.now(timezone.utc)
        return self.store.save_loop_config(config)

    def list_loop_configs(self, enabled_only: bool = False) -> list[LoopDetectionConfig]:
        """List loop detection configurations."""
        return self.store.list_loop_configs(enabled_only=enabled_only)

    def delete_loop_config(self, config_id: str) -> bool:
        """Delete a loop detection configuration."""
        return self.store.delete_loop_config(config_id)

    # --- v0.7.0 Guardrail Webhooks ---

    def create_webhook(
        self,
        name: str,
        url: str,
        events: Optional[list[str]] = None,
        secret: Optional[str] = None,
        scope: Optional[str] = None,
        scope_id: Optional[str] = None,
        enabled: bool = True,
        max_retries: int = 3,
        timeout_seconds: float = 10.0,
        headers: Optional[dict] = None,
        description: str = "",
    ) -> WebhookConfig:
        """Register a webhook endpoint for guardrail/budget notifications.

        When a guardrail triggers (warn/block/kill), kill switch activates,
        or projection predicts a breach, matching webhooks receive a POST.
        """
        event_enums = []
        if events:
            for e in events:
                try:
                    event_enums.append(WebhookEvent(e))
                except ValueError:
                    raise ValueError(f"Unknown event type: {e}. Valid: {[e.value for e in WebhookEvent]}")
        else:
            event_enums = list(WebhookEvent)

        scope_enum = None
        if scope:
            try:
                scope_enum = GuardrailScope(scope)
            except ValueError:
                raise ValueError(f"Unknown scope: {scope}. Valid: {[s.value for s in GuardrailScope]}")

        webhook = WebhookConfig(
            name=name,
            url=url,
            events=event_enums,
            secret=secret,
            scope=scope_enum,
            scope_id=scope_id,
            enabled=enabled,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            headers=headers or {},
        )
        self.store.save_webhook(webhook)
        return webhook

    def update_webhook(self, webhook_id: str, **kwargs) -> WebhookConfig:
        """Update an existing webhook configuration."""
        webhook = self.store.get_webhook(webhook_id)
        if not webhook:
            raise ValueError(f"Webhook not found: {webhook_id}")

        update_data = kwargs.copy()
        if 'events' in update_data and update_data['events']:
            update_data['events'] = [WebhookEvent(e) if isinstance(e, str) else e for e in update_data['events']]
        if 'scope' in update_data and update_data['scope'] and isinstance(update_data['scope'], str):
            update_data['scope'] = GuardrailScope(update_data['scope'])

        updated = webhook.model_copy(update=update_data)
        updated.updated_at = datetime.now(timezone.utc)
        self.store.save_webhook(updated)
        return updated

    def list_webhooks(self, enabled_only: bool = False) -> list[WebhookConfig]:
        """List all registered webhooks."""
        return self.store.list_webhooks(enabled_only=enabled_only)

    def get_webhook(self, webhook_id: str) -> Optional[WebhookConfig]:
        """Get a webhook by ID."""
        return self.store.get_webhook(webhook_id)

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook."""
        return self.store.delete_webhook(webhook_id)

    def _match_webhooks(
        self,
        event: WebhookEvent,
        scope: Optional[GuardrailScope] = None,
        scope_id: Optional[str] = None,
    ) -> list[WebhookConfig]:
        """Find webhooks that match the given event and scope."""
        matching = []
        for hook in self.store.list_webhooks(enabled_only=True):
            if event not in hook.events:
                continue
            if hook.scope and scope:
                if hook.scope != scope:
                    continue
                if hook.scope_id and scope_id:
                    if hook.scope_id.lower() != scope_id.lower():
                        continue
            matching.append(hook)
        return matching

    def _fire_webhooks(
        self,
        event: WebhookEvent,
        payload: dict,
        scope: Optional[GuardrailScope] = None,
        scope_id: Optional[str] = None,
    ) -> int:
        """Fire all matching webhooks for an event. Returns count fired.

        Webhook delivery is best-effort: failures are logged but do not
        block the guardrail decision. Each delivery attempt is recorded.
        """
        import urllib.request
        import hashlib
        import hmac as hmac_module
        import json as json_module
        import time

        matching = self._match_webhooks(event, scope, scope_id)
        fired = 0

        for hook in matching:
            body = json_module.dumps(payload, default=str).encode('utf-8')

            headers = dict(hook.headers)
            headers["Content-Type"] = "application/json"
            headers["X-Webhook-Event"] = event.value
            headers["X-Webhook-Id"] = hook.id
            headers["X-Webhook-Timestamp"] = str(int(time.time()))

            # HMAC signing if secret is set
            if hook.secret:
                signature = hmac_module.new(
                    hook.secret.encode('utf-8'),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            success = False
            status_code = None
            error_msg = None
            response_body = None

            for attempt in range(1, hook.max_retries + 1):
                try:
                    req = urllib.request.Request(
                        hook.url,
                        data=body,
                        headers=headers,
                        method="POST",
                    )
                    start_time = time.time()
                    resp = urllib.request.urlopen(req, timeout=hook.timeout_seconds)
                    duration_ms = (time.time() - start_time) * 1000
                    status_code = resp.status
                    response_body = resp.read().decode('utf-8', errors='replace')[:500]

                    if 200 <= status_code < 300:
                        success = True
                        break
                    elif status_code >= 500 and attempt < hook.max_retries:
                        time.sleep(0.5 * attempt)  # backoff
                        continue
                    else:
                        error_msg = f"HTTP {status_code}"
                        break
                except Exception as e:
                    duration_ms = 0
                    error_msg = str(e)[:200]
                    if attempt < hook.max_retries:
                        time.sleep(0.5 * attempt)
                        continue

            delivery = WebhookDelivery(
                webhook_id=hook.id,
                event=event,
                payload=payload,
                success=success,
                status_code=status_code,
                response_body=response_body,
                error=error_msg,
                attempt=attempt,
                duration_ms=round(duration_ms, 2),
            )
            self.store.save_webhook_delivery(delivery)

            if success:
                fired += 1

        return fired

    def list_webhook_deliveries(
        self,
        webhook_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[WebhookDelivery]:
        """List recent webhook delivery records."""
        return self.store.list_webhook_deliveries(webhook_id=webhook_id, limit=limit)

    def test_webhook(self, webhook_id: str) -> dict:
        """Send a test event to a webhook. Returns the delivery result."""
        webhook = self.store.get_webhook(webhook_id)
        if not webhook:
            raise ValueError(f"Webhook not found: {webhook_id}")

        payload = {
            "test": True,
            "message": "Test webhook delivery from agent-budget",
            "webhook_name": webhook.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        fired = self._fire_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            payload,
        )

        return {
            "webhook_id": webhook_id,
            "fired": fired,
            "message": "Test event sent" if fired > 0 else "No webhooks fired (check event filter)",
        }

    def check_guardrails_with_projection(
        self,
        estimated_cost_usd: float = 0.0,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        budget_id: Optional[str] = None,
        task_id: Optional[str] = None,
        use_projection: bool = True,
        now: Optional[datetime] = None,
    ) -> GuardrailDecision:
        """Enhanced guardrail check that integrates spend projection.

        This wraps check_guardrails() but also runs project_spend() to
        get a forward-looking view. If projection shows the spend will
        breach a guardrail before period end, the decision includes
        that warning even if current spend is within limits.

        This is the 'smart' check — agents who want to be proactive
        (rather than just reactive) should use this.
        """
        now = now or datetime.now(timezone.utc)

        # Get the standard guardrail decision
        decision = self.check_guardrails(
            estimated_cost_usd=estimated_cost_usd,
            agent_id=agent_id,
            model_id=model_id,
            budget_id=budget_id,
            task_id=task_id,
            now=now,
        )

        if not use_projection:
            return decision

        # Run projection for the most relevant scope
        scope = GuardrailScope.GLOBAL
        scope_id = None
        if agent_id:
            scope = GuardrailScope.AGENT
            scope_id = agent_id
        elif model_id:
            scope = GuardrailScope.MODEL
            scope_id = model_id
        elif budget_id:
            scope = GuardrailScope.BUDGET
            scope_id = budget_id
        elif task_id:
            scope = GuardrailScope.TASK
            scope_id = task_id

        try:
            projection = self.project_spend(
                scope=scope,
                scope_id=scope_id,
                period="daily",
                now=now,
            )

            proj_integration = ProjectionIntegration(
                enabled=True,
                projected_spend_usd=projection.projected_spend_usd,
                projected_percent=(projection.projected_spend_usd / projection.limit_usd * 100)
                    if projection.limit_usd else None,
                projected_exceeds=projection.projected_exceeds_limit,
                eta_minutes=projection.eta_minutes_to_limit,
                will_breach=projection.will_breach_guardrail,
                projection_confidence=projection.confidence,
            )
            decision.projection = proj_integration

            # If projection says we'll breach and current decision is ALLOW,
            # upgrade to WARN if confidence is sufficient
            if (proj_integration.will_breach and
                decision.allowed and
                proj_integration.projection_confidence >= 0.4 and
                proj_integration.projected_percent is not None and
                proj_integration.projected_percent >= 80):
                decision.action = GuardrailAction.WARN
                decision.allowed = True  # still allowed, just warned
                eta_str = f" ETA to limit: ~{proj_integration.eta_minutes:.0f} min." if proj_integration.eta_minutes else ""
                proj_msg = (
                    f"PROJECTION WARNING: At current rate (${projection.spend_rate_per_hour:.2f}/hr), "
                    f"daily spend will reach ${projection.projected_spend_usd:.2f} "
                    f"({proj_integration.projected_percent:.0f}% of limit) by end of day.{eta_str}"
                )
                decision.reason = decision.reason + " | " + proj_msg if decision.reason else proj_msg
                decision.suggestions.append(
                    f"Projected to breach — consider switching to a cheaper model or reducing call frequency"
                )

                # Fire projection breach webhook
                fired = self._fire_webhooks(
                    WebhookEvent.PROJECTION_BREACH,
                    {
                        "scope": scope.value,
                        "scope_id": scope_id,
                        "projected_spend_usd": projection.projected_spend_usd,
                        "limit_usd": projection.limit_usd,
                        "projected_percent": proj_integration.projected_percent,
                        "eta_minutes": proj_integration.eta_minutes,
                        "confidence": proj_integration.projection_confidence,
                        "recommendation": projection.recommendation,
                        "agent_id": agent_id,
                        "model_id": model_id,
                        "task_id": task_id,
                        "timestamp": now.isoformat(),
                    },
                    scope=scope,
                    scope_id=scope_id,
                )
                decision.webhooks_fired += fired

        except Exception:
            # Projection is best-effort; don't fail the guardrail check
            pass

        return decision

    # ------------------------------------------------------------------ #
    # v0.9.0 — Concurrency-safe reserve/settle protocol
    # ------------------------------------------------------------------ #
    #
    # The race: ``check_guardrails`` reads settled spend, the agent makes the
    # call, then ``record`` writes the cost.  Between read and write, N
    # concurrent agents can all observe the same under-limit total and all
    # fire — blowing past the ceiling.  This is the exact bug floe-guard
    # markets against.
    #
    # The fix: ``reserve_and_check`` runs the guardrail check under the
    # store lock with ``include_reservations=True`` and, if the call is
    # allowed, *immediately* creates an ACTIVE reservation before releasing
    # the lock.  Subsequent concurrent calls now see the reserved amount
    # counted against the budget.  After the real call completes, the agent
    # calls ``settle_reservation`` to record actual usage and close the
    # reservation, or ``release_reservation`` to return the budget if the
    # call never happened.

    def reserve_and_check(
        self,
        estimated_cost_usd: float = 0.0,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        budget_id: Optional[str] = None,
        task_id: Optional[str] = None,
        ttl_minutes: int = 5,
        now: Optional[datetime] = None,
    ) -> tuple[GuardrailDecision, Optional[SpendReservation]]:
        """Atomically check guardrails *and* reserve the estimated cost.

        This is the concurrency-safe replacement for the check → call →
        record pattern.  Under the store lock it:

        1. Expires stale reservations (cleanup)
        2. Runs ``check_guardrails`` with ``include_reservations=True``
           so committed spend is counted
        3. If the decision is ALLOW/WARN (call proceeds), creates an
           ACTIVE reservation for ``estimated_cost_usd``

        Returns ``(decision, reservation)``.  If the call was blocked the
        reservation is ``None``.  The caller MUST later call
        ``settle_reservation`` (with actual usage) or
        ``release_reservation`` (if the call was abandoned).

        Args:
            estimated_cost_usd: Best-guess cost of the upcoming call
            agent_id: Agent making the call
            model_id: Model being called
            budget_id: Associated budget
            task_id: Task/session ID
            ttl_minutes: How long the reservation holds before auto-expiry
            now: Override current time (testing)
        """
        now = now or datetime.now(timezone.utc)

        with self.store._lock:
            # 1. Reap expired reservations so their budget is released
            self.store.expire_stale_reservations(now=now)

            # 2. Run the guardrail check counting committed (reserved) spend.
            #    We call the check logic directly but need the reservation-aware
            #    spend path.  check_guardrails uses _get_spend_for_period which
            #    now accepts include_reservations; we temporarily monkey-patch
            #    by wrapping.  Simpler: re-run with a reservation-aware variant.
            decision = self._check_guardrails_internal(
                estimated_cost_usd=estimated_cost_usd,
                agent_id=agent_id,
                model_id=model_id,
                budget_id=budget_id,
                task_id=task_id,
                now=now,
                include_reservations=True,
            )

            # 3. Reserve if the call is allowed to proceed
            if decision.allowed and estimated_cost_usd >= 0:
                reservation = SpendReservation(
                    reserved_amount_usd=estimated_cost_usd,
                    agent_id=agent_id,
                    model_id=model_id,
                    task_id=task_id,
                    budget_id=budget_id,
                    status=ReservationStatus.ACTIVE,
                    expires_at=now + timedelta(minutes=max(1, ttl_minutes)),
                    metadata={"guardrail_id": decision.guardrail_id} if decision.guardrail_id else {},
                )
                self.store.save_reservation(reservation)
                return decision, reservation

            return decision, None

    def _check_guardrails_internal(
        self,
        estimated_cost_usd: float = 0.0,
        agent_id: Optional[str] = None,
        model_id: Optional[str] = None,
        budget_id: Optional[str] = None,
        task_id: Optional[str] = None,
        now: Optional[datetime] = None,
        include_reservations: bool = False,
    ) -> GuardrailDecision:
        """Internal guardrail check that optionally counts reserved spend.

        This mirrors ``check_guardrails`` but threads the
        ``include_reservations`` flag through to ``_get_spend_for_period``
        so that the reserve_and_check path sees committed spend.
        """
        now = now or datetime.now(timezone.utc)

        # Kill switch
        kill_switch = self.store.get_kill_switch()
        if kill_switch.is_active(now):
            return GuardrailDecision(
                allowed=False,
                action=GuardrailAction.KILL,
                reason=f"KILL SWITCH ACTIVE: {kill_switch.reason}.",
                current_spend_usd=0.0,
            )

        guardrails = self.store.list_guardrails(enabled_only=True)
        if not guardrails:
            return GuardrailDecision(
                allowed=True,
                action=GuardrailAction.ALLOW,
                reason="No guardrails configured — all calls allowed",
                current_spend_usd=0.0,
            )

        worst_decision: Optional[GuardrailDecision] = None

        for g in guardrails:
            applies = False
            check_scope_id = None

            if g.scope == GuardrailScope.GLOBAL:
                applies = True
            elif g.scope == GuardrailScope.AGENT and agent_id:
                if g.scope_id is None or g.scope_id.lower() == agent_id.lower():
                    applies = True
                    check_scope_id = agent_id
            elif g.scope == GuardrailScope.MODEL and model_id:
                if g.scope_id is None or g.scope_id.lower() == model_id.lower():
                    applies = True
                    check_scope_id = model_id
            elif g.scope == GuardrailScope.BUDGET and budget_id:
                if g.scope_id is None or g.scope_id.lower() == budget_id.lower():
                    applies = True
                    check_scope_id = budget_id
            elif g.scope == GuardrailScope.TASK and task_id:
                if g.scope_id is None or g.scope_id.lower() == task_id.lower():
                    applies = True
                    check_scope_id = task_id

            if not applies:
                continue

            # Per-call limit
            if g.per_call_limit_usd is not None and estimated_cost_usd > g.per_call_limit_usd:
                decision = GuardrailDecision(
                    allowed=False,
                    action=GuardrailAction.BLOCK,
                    reason=f"Per-call cost ${estimated_cost_usd:.4f} exceeds limit ${g.per_call_limit_usd:.4f} (guardrail: {g.name})",
                    guardrail_id=g.id,
                    limit_usd=g.per_call_limit_usd,
                    percent_used=100.0,
                )
                if worst_decision is None or decision.action.value > worst_decision.action.value:
                    worst_decision = decision
                continue

            # Daily limit (reservation-aware)
            if g.daily_limit_usd is not None:
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                daily_spend = self._get_spend_for_period(
                    g.scope, check_scope_id, day_start, now,
                    include_reservations=include_reservations,
                )
                projected = daily_spend + estimated_cost_usd
                pct = (projected / g.daily_limit_usd * 100) if g.daily_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Daily limit ${g.daily_limit_usd:.2f} reached — committed spend ${daily_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=daily_spend,
                        limit_usd=g.daily_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif g.throttle_enabled:
                    throttle_decision = self._check_throttle_tier(
                        g, estimated_cost_usd, pct, daily_spend, g.daily_limit_usd, "daily"
                    )
                    if throttle_decision:
                        if worst_decision is None or (worst_decision.allowed and not throttle_decision.allowed):
                            worst_decision = throttle_decision
                        elif worst_decision and worst_decision.action == GuardrailAction.WARN and throttle_decision.action == GuardrailAction.THROTTLE:
                            worst_decision = throttle_decision
                        continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Approaching daily limit: {pct:.1f}% of ${g.daily_limit_usd:.2f} (committed ${daily_spend:.2f})",
                        guardrail_id=g.id,
                        current_spend_usd=daily_spend,
                        limit_usd=g.daily_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

            # Hourly limit (reservation-aware)
            if g.hourly_limit_usd is not None:
                hour_start = now - timedelta(hours=1)
                hourly_spend = self._get_spend_for_period(
                    g.scope, check_scope_id, hour_start, now,
                    include_reservations=include_reservations,
                )
                projected = hourly_spend + estimated_cost_usd
                pct = (projected / g.hourly_limit_usd * 100) if g.hourly_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Hourly limit ${g.hourly_limit_usd:.2f} reached — committed ${hourly_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=hourly_spend,
                        limit_usd=g.hourly_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Approaching hourly limit: {pct:.1f}% of ${g.hourly_limit_usd:.2f}",
                        guardrail_id=g.id,
                        current_spend_usd=hourly_spend,
                        limit_usd=g.hourly_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

            # Monthly limit (reservation-aware)
            if g.monthly_limit_usd is not None:
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_spend = self._get_spend_for_period(
                    g.scope, check_scope_id, month_start, now,
                    include_reservations=include_reservations,
                )
                projected = monthly_spend + estimated_cost_usd
                pct = (projected / g.monthly_limit_usd * 100) if g.monthly_limit_usd > 0 else 0

                if pct >= g.block_at_percent:
                    decision = GuardrailDecision(
                        allowed=False,
                        action=GuardrailAction.BLOCK,
                        reason=f"Monthly limit ${g.monthly_limit_usd:.2f} reached — committed ${monthly_spend:.2f}, projected ${projected:.2f} ({pct:.1f}%) (guardrail: {g.name})",
                        guardrail_id=g.id,
                        current_spend_usd=monthly_spend,
                        limit_usd=g.monthly_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or decision.action.value > worst_decision.action.value:
                        worst_decision = decision
                    continue
                elif pct >= g.warn_at_percent:
                    decision = GuardrailDecision(
                        allowed=True,
                        action=GuardrailAction.WARN,
                        reason=f"Approaching monthly limit: {pct:.1f}% of ${g.monthly_limit_usd:.2f}",
                        guardrail_id=g.id,
                        current_spend_usd=monthly_spend,
                        limit_usd=g.monthly_limit_usd,
                        percent_used=pct,
                    )
                    if worst_decision is None or (worst_decision.allowed and not decision.allowed):
                        worst_decision = decision
                    continue

        return worst_decision or GuardrailDecision(
            allowed=True,
            action=GuardrailAction.ALLOW,
            reason="All guardrails passed",
            current_spend_usd=0.0,
        )

    def settle_reservation(
        self,
        reservation_id: str,
        actual_cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> SpendReservation:
        """Settle a reservation with actual usage data.

        Records the true cost as an ``LLMUsageRecord`` and closes the
        reservation as SETTLED.  If the actual cost exceeds the reserved
        estimate, the difference is still recorded (the budget took the
        hit at reserve time; settle just makes the books accurate).

        Raises ``ValueError`` if the reservation doesn't exist or isn't
        in a settlable state.
        """
        now = now or datetime.now(timezone.utc)
        rsv = self.store.get_reservation(reservation_id)
        if rsv is None:
            raise ValueError(f"Reservation {reservation_id} not found")

        if rsv.status not in (ReservationStatus.ACTIVE, ReservationStatus.EXPIRED):
            raise ValueError(
                f"Reservation {reservation_id} is {rsv.status.value} — cannot settle"
            )

        # Record actual usage
        from .llm_costs import LLMUsageRecord
        record = LLMUsageRecord(
            model_id=model_id or rsv.model_id or "unknown",
            agent_id=rsv.agent_id,
            task_id=rsv.task_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=actual_cost_usd,
            metadata={
                **(rsv.metadata or {}),
                "reservation_id": rsv.id,
                "reserved_amount_usd": rsv.reserved_amount_usd,
                "budget_id": rsv.budget_id,
            } if rsv.budget_id else {
                **(rsv.metadata or {}),
                "reservation_id": rsv.id,
                "reserved_amount_usd": rsv.reserved_amount_usd,
            },
        )
        self.store.save_llm_usage(record)

        # Close the reservation
        updated = rsv.model_copy(update={
            "status": ReservationStatus.SETTLED,
            "settled_at": now,
            "settled_amount_usd": actual_cost_usd,
            "usage_record_id": record.id,
        })
        self.store.save_reservation(updated)
        return updated

    def release_reservation(
        self,
        reservation_id: str,
        reason: str = "released",
        now: Optional[datetime] = None,
    ) -> SpendReservation:
        """Release a reservation without settling (call never made / failed).

        The reserved budget is returned to the pool immediately.
        """
        now = now or datetime.now(timezone.utc)
        rsv = self.store.get_reservation(reservation_id)
        if rsv is None:
            raise ValueError(f"Reservation {reservation_id} not found")
        if rsv.status != ReservationStatus.ACTIVE:
            raise ValueError(
                f"Reservation {reservation_id} is {rsv.status.value} — cannot release"
            )
        updated = rsv.model_copy(update={
            "status": ReservationStatus.RELEASED,
            "settled_at": now,
            "metadata": {**(rsv.metadata or {}), "release_reason": reason},
        })
        self.store.save_reservation(updated)
        return updated

    def list_reservations(
        self,
        status: Optional[ReservationStatus] = None,
        agent_id: Optional[str] = None,
        active_only: bool = False,
        now: Optional[datetime] = None,
    ) -> list[SpendReservation]:
        """List spend reservations."""
        return self.store.list_reservations(
            status=status, agent_id=agent_id, active_only=active_only, now=now,
        )

    def get_reservation(self, reservation_id: str) -> Optional[SpendReservation]:
        """Fetch a single reservation."""
        return self.store.get_reservation(reservation_id)

    # ------------------------------------------------------------------ #
    # v0.10.0 — Spend Anomaly Detection                                  #
    # ------------------------------------------------------------------ #

    def create_anomaly_rule(
        self,
        name: str,
        anomaly_type: AnomalyType,
        method: str = "zscore",
        threshold: float = 3.0,
        baseline_window_hours: int = 24,
        min_samples: int = 5,
        scope: GuardrailScope = GuardrailScope.GLOBAL,
        scope_id: Optional[str] = None,
        action: AnomalyAction = AnomalyAction.LOG,
        after_hours_start: Optional[int] = None,
        after_hours_end: Optional[int] = None,
        cooldown_minutes: int = 30,
        enabled: bool = True,
    ) -> SpendAnomalyRule:
        """Create a new anomaly detection rule."""
        rule = SpendAnomalyRule(
            name=name,
            anomaly_type=anomaly_type,
            method=method,
            threshold=threshold,
            baseline_window_hours=baseline_window_hours,
            min_samples=min_samples,
            scope=scope,
            scope_id=scope_id,
            action=action,
            after_hours_start=after_hours_start,
            after_hours_end=after_hours_end,
            cooldown_minutes=cooldown_minutes,
            enabled=enabled,
        )
        self.store.save_anomaly_rule(rule)
        return rule

    def update_anomaly_rule(self, rule_id: str, **kwargs) -> SpendAnomalyRule:
        """Update an anomaly rule. Raises ValueError if not found."""
        rule = self.store.get_anomaly_rule(rule_id)
        if not rule:
            raise ValueError(f"Anomaly rule {rule_id} not found")
        updated = rule.model_copy(update=kwargs)
        self.store.save_anomaly_rule(updated)
        return updated

    def list_anomaly_rules(self, enabled_only: bool = False) -> list[SpendAnomalyRule]:
        return self.store.list_anomaly_rules(enabled_only=enabled_only)

    def get_anomaly_rule(self, rule_id: str) -> Optional[SpendAnomalyRule]:
        return self.store.get_anomaly_rule(rule_id)

    def delete_anomaly_rule(self, rule_id: str) -> bool:
        return self.store.delete_anomaly_rule(rule_id)

    def _compute_baseline(
        self,
        records: list,
        window_hours: int,
        now: Optional[datetime] = None,
    ) -> tuple[float, float, int]:
        """Compute mean, stddev, and sample count from historical records.

        Records should be LLMUsageRecord objects. Only records within the
        baseline window (now - window_hours to now) are used.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=window_hours)
        baseline_costs = [
            r.cost_usd for r in records
            if r.recorded_at >= cutoff and r.cost_usd > 0
        ]
        if not baseline_costs:
            # Fall back to all records if no in-window data
            baseline_costs = [r.cost_usd for r in records if r.cost_usd > 0]
        if not baseline_costs:
            return 0.0, 0.0, 0
        mean = sum(baseline_costs) / len(baseline_costs)
        if len(baseline_costs) > 1:
            variance = sum((c - mean) ** 2 for c in baseline_costs) / (len(baseline_costs) - 1)
            stddev = variance ** 0.5
        else:
            stddev = 0.0
        return mean, stddev, len(baseline_costs)

    def _is_in_cooldown(
        self,
        rule_id: str,
        cooldown_minutes: int,
        now: Optional[datetime] = None,
    ) -> bool:
        """Check if this rule is still in cooldown (recently fired)."""
        if cooldown_minutes <= 0:
            return False
        now = now or datetime.now(timezone.utc)
        recent = self.store.list_anomaly_events(rule_id=rule_id, limit=1)
        if not recent:
            return False
        last_event = recent[0]
        if now - last_event.detected_at < timedelta(minutes=cooldown_minutes):
            return True
        return False

    def detect_anomalies(
        self,
        now: Optional[datetime] = None,
        check_record: Optional[dict] = None,
    ) -> list[AnomalyEvent]:
        """Run all enabled anomaly rules and return any events detected.

        Args:
            now: Reference timestamp (defaults to utcnow).
            check_record: Optional context for a just-occurred call
                         (e.g. {"agent_id": ..., "model_id": ..., "cost_usd": ...,
                                "input_tokens": ..., "output_tokens": ...}).
                         When provided, detection focuses on this record.

        Returns:
            List of AnomalyEvent objects created (empty if none detected).
        """
        now = now or datetime.now(timezone.utc)
        rules = self.store.list_anomaly_rules(enabled_only=True)
        events: list[AnomalyEvent] = []

        for rule in rules:
            if self._is_in_cooldown(rule.id, rule.cooldown_minutes, now):
                continue

            # Get relevant usage records
            all_records = self.store.list_llm_usage(limit=500)

            # Filter by scope
            scoped_records = self._filter_records_by_scope(all_records, rule, check_record)
            if len(scoped_records) < rule.min_samples:
                continue

            event = self._evaluate_rule(rule, scoped_records, now, check_record)
            if event:
                events.append(event)
                self.store.save_anomaly_event(event)
                self._execute_anomaly_action(rule, event, now)

        return events

    def _filter_records_by_scope(
        self,
        records: list,
        rule: SpendAnomalyRule,
        check_record: Optional[dict] = None,
    ) -> list:
        """Filter usage records by the rule's scope."""
        if rule.scope == GuardrailScope.GLOBAL:
            return records
        result = []
        for r in records:
            if rule.scope == GuardrailScope.AGENT and rule.scope_id:
                if r.agent_id and r.agent_id.lower() == rule.scope_id.lower():
                    result.append(r)
            elif rule.scope == GuardrailScope.MODEL and rule.scope_id:
                if r.model_id and r.model_id.lower() == rule.scope_id.lower():
                    result.append(r)
            elif rule.scope == GuardrailScope.TASK and rule.scope_id:
                if r.task_id and r.task_id.lower() == rule.scope_id.lower():
                    result.append(r)
            else:
                result.append(r)
        return result

    def _evaluate_rule(
        self,
        rule: SpendAnomalyRule,
        records: list,
        now: datetime,
        check_record: Optional[dict] = None,
    ) -> Optional[AnomalyEvent]:
        """Evaluate a single anomaly rule against records. Returns event if anomaly detected."""
        mean, stddev, sample_count = self._compute_baseline(records, rule.baseline_window_hours, now)

        # Handle after-hours detection
        if rule.anomaly_type == AnomalyType.AFTER_HOURS:
            if rule.after_hours_start is not None and rule.after_hours_end is not None:
                hour = now.hour
                start, end = rule.after_hours_start, rule.after_hours_end
                in_window = hour >= start or hour < end if start > end else (start <= hour < end)
                if not in_window:
                    return None
                recent_cost = sum(
                    r.cost_usd for r in records
                    if (now - r.recorded_at).total_seconds() < 3600
                )
                if recent_cost > 0:
                    deviation = recent_cost / (mean + 0.001)
                    if deviation >= 1.0:
                        return self._build_event(
                            rule, recent_cost, mean, stddev, deviation, 60,
                            sample_count, recent_cost, now, check_record,
                            message=f"After-hours spend detected: ${recent_cost:.4f} between {start}:00-{end}:00 UTC",
                        )
            return None

        # Handle new agent / new model detection
        if rule.anomaly_type == AnomalyType.NEW_AGENT:
            if check_record and check_record.get("agent_id"):
                known_agents = {r.agent_id for r in records if r.agent_id}
                if check_record["agent_id"] not in known_agents:
                    cost = check_record.get("cost_usd", 0.0)
                    return self._build_event(
                        rule, cost, 0.0, 0.0, 999.0, 0, sample_count,
                        cost, now, check_record,
                        message=f"New agent '{check_record['agent_id']}' spending detected (first call, ${cost:.4f})",
                    )
            return None

        if rule.anomaly_type == AnomalyType.NEW_MODEL:
            if check_record and check_record.get("model_id"):
                known_models = {r.model_id for r in records if r.model_id}
                if check_record["model_id"] not in known_models:
                    cost = check_record.get("cost_usd", 0.0)
                    return self._build_event(
                        rule, cost, 0.0, 0.0, 999.0, 0, sample_count,
                        cost, now, check_record,
                        message=f"New model '{check_record['model_id']}' spending detected (first call, ${cost:.4f})",
                    )
            return None

        # Handle rate burst (calls per minute)
        if rule.anomaly_type == AnomalyType.RATE_BURST:
            recent_window = timedelta(minutes=max(1, int(rule.threshold)) if rule.method == "rate" else 5)
            recent_calls = [r for r in records if (now - r.recorded_at) < recent_window]
            calls_per_min = len(recent_calls) / max(1, recent_window.total_seconds() / 60)
            # Baseline: average calls per minute over baseline window
            baseline_window = timedelta(hours=rule.baseline_window_hours)
            baseline_calls = [r for r in records if (now - r.recorded_at) < baseline_window]
            baseline_cpm = len(baseline_calls) / max(1, baseline_window.total_seconds() / 60)

            if rule.method == "rate":
                if calls_per_min >= rule.threshold:
                    deviation = calls_per_min / max(0.001, baseline_cpm)
                    return self._build_event(
                        rule, calls_per_min, baseline_cpm, 0.0, deviation,
                        int(recent_window.total_seconds() / 60), sample_count,
                        sum(r.cost_usd for r in recent_calls), now, check_record,
                        message=f"Rate burst: {calls_per_min:.1f} calls/min (baseline {baseline_cpm:.1f})",
                    )
            return None

        # Handle cost-per-call anomaly
        if rule.anomaly_type == AnomalyType.COST_PER_CALL:
            if check_record and check_record.get("cost_usd") is not None:
                call_cost = check_record["cost_usd"]
                if rule.method == "absolute":
                    if call_cost >= rule.threshold:
                        deviation = call_cost / max(0.001, mean)
                        return self._build_event(
                            rule, call_cost, mean, stddev, deviation, 0,
                            sample_count, call_cost, now, check_record,
                            message=f"High-cost call: ${call_cost:.4f} (abs threshold ${rule.threshold:.4f})",
                        )
                elif rule.method == "zscore" and stddev > 0:
                    z = (call_cost - mean) / stddev
                    if z >= rule.threshold:
                        return self._build_event(
                            rule, call_cost, mean, stddev, z, 0,
                            sample_count, call_cost, now, check_record,
                            message=f"Cost-per-call anomaly: ${call_cost:.4f} is {z:.1f}σ above mean ${mean:.4f}",
                        )
                elif rule.method == "multiplier" and mean > 0:
                    mult = call_cost / mean
                    if mult >= rule.threshold:
                        return self._build_event(
                            rule, call_cost, mean, stddev, mult, 0,
                            sample_count, call_cost, now, check_record,
                            message=f"Cost-per-call anomaly: ${call_cost:.4f} is {mult:.1f}× the avg ${mean:.4f}",
                        )
            return None

        # Handle spike and sustained drift (window-based cost anomalies)
        # Get recent window costs
        recent_window_minutes = max(1, rule.baseline_window_hours * 60 // 24)  # 1/24 of baseline
        recent_cutoff = now - timedelta(minutes=recent_window_minutes)
        recent_records = [r for r in records if r.recorded_at >= recent_cutoff]
        recent_cost = sum(r.cost_usd for r in recent_records)

        # For window-based anomalies, recompute baseline EXCLUDING the recent
        # window so the anomaly itself doesn't contaminate the "normal" stats.
        historical_records = [r for r in records if r.recorded_at < recent_cutoff]
        if historical_records:
            h_mean, h_stddev, h_count = self._compute_baseline(
                historical_records, rule.baseline_window_hours, now
            )
            if h_count > 0:
                mean, stddev, sample_count = h_mean, h_stddev, h_count

        if rule.anomaly_type == AnomalyType.SPIKE:
            if rule.method == "zscore":
                if stddev > 0:
                    z = (recent_cost - mean) / stddev
                    if z >= rule.threshold:
                        return self._build_event(
                            rule, recent_cost, mean, stddev, z, recent_window_minutes,
                            sample_count, recent_cost, now, check_record,
                            message=f"Cost spike: ${recent_cost:.4f} in {recent_window_minutes}min ({z:.1f}σ above mean ${mean:.4f})",
                        )
                elif mean > 0 and recent_cost > mean:
                    # When baseline is perfectly flat (stddev=0), any cost
                    # above the mean is an infinitely-significant spike.
                    z = (recent_cost - mean) / max(mean, 0.001)  # use mean as scale
                    if z >= rule.threshold:
                        return self._build_event(
                            rule, recent_cost, mean, 0.0, z, recent_window_minutes,
                            sample_count, recent_cost, now, check_record,
                            message=f"Cost spike: ${recent_cost:.4f} in {recent_window_minutes}min ({z:.1f}× above flat baseline ${mean:.4f})",
                        )
            elif rule.method == "multiplier" and mean > 0:
                mult = recent_cost / mean
                if mult >= rule.threshold:
                    return self._build_event(
                        rule, recent_cost, mean, stddev, mult, recent_window_minutes,
                        sample_count, recent_cost, now, check_record,
                        message=f"Cost spike: ${recent_cost:.4f} is {mult:.1f}× the avg call cost ${mean:.4f}",
                    )
            elif rule.method == "absolute":
                if recent_cost >= rule.threshold:
                    deviation = recent_cost / max(0.001, mean)
                    return self._build_event(
                        rule, recent_cost, mean, stddev, deviation, recent_window_minutes,
                        sample_count, recent_cost, now, check_record,
                        message=f"Cost spike: ${recent_cost:.4f} exceeds absolute threshold ${rule.threshold:.4f}",
                    )
            return None

        if rule.anomaly_type == AnomalyType.SUSTAINED_DRIFT:
            # Compare rolling averages in two halves of the baseline window
            half_hours = rule.baseline_window_hours / 2
            half_cutoff = now - timedelta(hours=half_hours)
            older = [r for r in records if r.recorded_at < half_cutoff]
            newer = [r for r in records if r.recorded_at >= half_cutoff]
            if len(older) >= rule.min_samples // 2 and len(newer) >= rule.min_samples // 2:
                older_avg = sum(r.cost_usd for r in older) / len(older)
                newer_avg = sum(r.cost_usd for r in newer) / len(newer)
                if older_avg > 0:
                    drift_ratio = newer_avg / older_avg
                    if rule.method == "multiplier" and drift_ratio >= rule.threshold:
                        return self._build_event(
                            rule, newer_avg, older_avg, stddev, drift_ratio,
                            rule.baseline_window_hours * 60, sample_count,
                            newer_avg, now, check_record,
                            message=f"Sustained drift: avg cost rose from ${older_avg:.4f} to ${newer_avg:.4f} ({drift_ratio:.1f}×)",
                        )
                    elif rule.method == "zscore" and stddev > 0:
                        z = (newer_avg - mean) / stddev
                        if z >= rule.threshold:
                            return self._build_event(
                                rule, newer_avg, mean, stddev, z,
                                rule.baseline_window_hours * 60, sample_count,
                                newer_avg, now, check_record,
                                message=f"Sustained drift: avg cost ${newer_avg:.4f} is {z:.1f}σ above baseline ${mean:.4f}",
                            )
            return None

        return None

    def _build_event(
        self,
        rule: SpendAnomalyRule,
        observed: float,
        mean: float,
        stddev: float,
        deviation: float,
        window_min: int,
        sample_count: int,
        cost: float,
        now: datetime,
        check_record: Optional[dict],
        message: str,
    ) -> AnomalyEvent:
        """Build an AnomalyEvent from detection results."""
        event = AnomalyEvent(
            rule_id=rule.id,
            rule_name=rule.name,
            anomaly_type=rule.anomaly_type,
            scope=rule.scope,
            scope_id=rule.scope_id,
            observed_value=observed,
            baseline_mean=mean,
            baseline_stddev=stddev,
            deviation_score=deviation,
            window_minutes=window_min,
            sample_count=sample_count,
            anomaly_cost_usd=cost,
            action_taken=rule.action,
            message=message,
            detected_at=now,
        )
        event.severity = event.compute_severity(
            rule.threshold,
            rule.severity_medium_multiplier,
            rule.severity_high_multiplier,
            rule.severity_critical_multiplier,
        )
        if check_record:
            event.metadata = {
                k: v for k, v in check_record.items()
                if k in ("agent_id", "model_id", "task_id", "cost_usd", "input_tokens", "output_tokens")
            }
        return event

    def _execute_anomaly_action(
        self,
        rule: SpendAnomalyRule,
        event: AnomalyEvent,
        now: datetime,
    ) -> str:
        """Execute the action specified by the rule when an anomaly fires."""
        result = ""
        if rule.action == AnomalyAction.LOG:
            result = "logged"
        elif rule.action == AnomalyAction.NOTIFY:
            # Fire anomaly webhook events
            count = self._fire_anomaly_webhooks(event, now)
            event.webhooks_fired = count
            result = f"notified ({count} webhooks fired)"
        elif rule.action == AnomalyAction.THROTTLE:
            count = self._fire_anomaly_webhooks(event, now)
            event.webhooks_fired = count
            result = f"throttle recommended + {count} webhooks"
        elif rule.action == AnomalyAction.BLOCK:
            # For BLOCK, trigger cooldown on the relevant scope
            count = self._fire_anomaly_webhooks(event, now)
            event.webhooks_fired = count
            result = f"block recommended + {count} webhooks"
        elif rule.action == AnomalyAction.KILL_SWITCH:
            try:
                self.trigger_kill_switch(
                    reason=f"Anomaly detected: {event.message}",
                    triggered_by=f"anomaly_rule:{rule.id}",
                )
                result = "kill switch triggered"
            except Exception:
                result = "kill switch trigger failed"
            count = self._fire_anomaly_webhooks(event, now)
            event.webhooks_fired = count
        event.action_result = result
        self.store.save_anomaly_event(event)
        return result

    def _fire_anomaly_webhooks(self, event: AnomalyEvent, now: datetime) -> int:
        """Fire webhooks for anomaly events. Returns count fired."""
        payload = {
            "event_type": "spend_anomaly",
            "anomaly_id": event.id,
            "anomaly_type": event.anomaly_type.value,
            "severity": event.severity.value,
            "rule_id": event.rule_id,
            "rule_name": event.rule_name,
            "observed_value": event.observed_value,
            "baseline_mean": event.baseline_mean,
            "deviation_score": event.deviation_score,
            "anomaly_cost_usd": event.anomaly_cost_usd,
            "message": event.message,
            "detected_at": event.detected_at.isoformat(),
        }
        try:
            return self._fire_webhooks(
                WebhookEvent.GUARDRAIL_WARN, payload,
                scope=event.scope, scope_id=event.scope_id,
            )
        except Exception:
            return 0

    def list_anomaly_events(
        self,
        rule_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        resolved: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> list[AnomalyEvent]:
        return self.store.list_anomaly_events(
            rule_id=rule_id, acknowledged=acknowledged,
            resolved=resolved, limit=limit,
        )

    def get_anomaly_event(self, event_id: str) -> Optional[AnomalyEvent]:
        return self.store.get_anomaly_event(event_id)

    def acknowledge_anomaly_event(
        self, event_id: str, acknowledged_by: Optional[str] = None,
    ) -> Optional[AnomalyEvent]:
        event = self.store.get_anomaly_event(event_id)
        if not event:
            return None
        updated = event.model_copy(update={
            "acknowledged": True,
            "acknowledged_by": acknowledged_by,
            "acknowledged_at": datetime.now(timezone.utc),
        })
        self.store.save_anomaly_event(updated)
        return updated

    def resolve_anomaly_event(self, event_id: str) -> Optional[AnomalyEvent]:
        event = self.store.get_anomaly_event(event_id)
        if not event:
            return None
        updated = event.model_copy(update={
            "resolved": True,
            "resolved_at": datetime.now(timezone.utc),
        })
        self.store.save_anomaly_event(updated)
        return updated

    def delete_anomaly_event(self, event_id: str) -> bool:
        return self.store.delete_anomaly_event(event_id)

    def clear_anomaly_events(self, rule_id: Optional[str] = None) -> int:
        return self.store.clear_anomaly_events(rule_id=rule_id)

    def get_anomaly_summary(self, recent_limit: int = 20) -> AnomalySummary:
        """Get a summary of anomaly detection status."""
        all_rules = self.store.list_anomaly_rules()
        all_events = self.store.list_anomaly_events()
        unack = [e for e in all_events if not e.acknowledged]
        unresolved = [e for e in all_events if not e.resolved]

        by_severity: dict[str, int] = {}
        by_type: dict[str, int] = {}
        total_cost = 0.0
        for e in all_events:
            sev = e.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1
            typ = e.anomaly_type.value
            by_type[typ] = by_type.get(typ, 0) + 1
            total_cost += e.anomaly_cost_usd

        return AnomalySummary(
            total_rules=len(all_rules),
            active_rules=len([r for r in all_rules if r.enabled]),
            total_events=len(all_events),
            unacknowledged_events=len(unack),
            unresolved_events=len(unresolved),
            events_by_severity=by_severity,
            events_by_type=by_type,
            recent_events=all_events[:recent_limit],
            total_anomaly_cost_usd=total_cost,
        )
