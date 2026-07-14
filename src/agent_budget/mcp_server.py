"""MCP server for Agent Budget."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .models import (
    BudgetPeriod, RecurringFrequency, SpendingRuleAction,
    SavingsGoalStatus, SUPPORTED_CURRENCIES,
    IncomeStatus,
    GuardrailScope,
)
from .service import BudgetService
from .store import BudgetStore
from .optimizer import ModelOptimizer, capability_tier
from .llm_costs import ModelProvider

mcp = FastMCP("agent-budget")

_service: Optional[BudgetService] = None


def get_service() -> BudgetService:
    global _service
    if _service is None:
        _service = BudgetService(BudgetStore())
    return _service


# --- Budget Tools ---

@mcp.tool()
def create_budget(
    name: str,
    limit: float,
    period: str,
    category: str | None = None,
    currency: str = "USD",
    rollover_enabled: bool = False,
    rollover_cap: float | None = None,
) -> str:
    """Create a new budget with a spending limit and period.

    Args:
        name: Budget name
        limit: Spending limit for the period
        period: Budget period (daily, weekly, monthly, quarterly, yearly)
        category: Optional category this budget applies to
        currency: Currency code (default USD)
        rollover_enabled: Whether unspent budget rolls over to next period
        rollover_cap: Max amount that can roll over (None = no cap)
    """
    svc = get_service()
    budget = svc.create_budget(
        name=name,
        limit=limit,
        period=BudgetPeriod(period),
        category=category,
        currency=currency,
        rollover_enabled=rollover_enabled,
        rollover_cap=rollover_cap,
    )
    return json.dumps(budget.model_dump(), default=str, indent=2)


@mcp.tool()
def list_budgets(active_only: bool = True) -> str:
    """List all budgets.

    Args:
        active_only: Only show active budgets (default True)
    """
    svc = get_service()
    budgets = svc.list_budgets(active_only=active_only)
    return json.dumps([b.model_dump() for b in budgets], default=str, indent=2)


@mcp.tool()
def get_budget(budget_id: str) -> str:
    """Get budget details.

    Args:
        budget_id: Budget ID
    """
    svc = get_service()
    budget = svc.get_budget(budget_id)
    if not budget:
        return json.dumps({"error": f"Budget {budget_id} not found"})
    return json.dumps(budget.model_dump(), default=str, indent=2)


@mcp.tool()
def update_budget(
    budget_id: str,
    name: str | None = None,
    limit: float | None = None,
    period: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    rollover_enabled: bool | None = None,
    rollover_cap: float | None = None,
) -> str:
    """Update a budget's settings.

    Args:
        budget_id: Budget ID to update
        name: New name
        limit: New spending limit
        period: New period (daily, weekly, monthly, quarterly, yearly)
        category: New category
        active: Activate or deactivate
        rollover_enabled: Enable or disable budget rollover
        rollover_cap: New rollover cap
    """
    svc = get_service()
    try:
        budget = svc.update_budget(
            budget_id=budget_id,
            name=name,
            limit=limit,
            period=BudgetPeriod(period) if period else None,
            category=category,
            active=active,
            rollover_enabled=rollover_enabled,
            rollover_cap=rollover_cap,
        )
        return json.dumps(budget.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_budget(budget_id: str) -> str:
    """Delete a budget.

    Args:
        budget_id: Budget ID to delete
    """
    svc = get_service()
    if svc.delete_budget(budget_id):
        return json.dumps({"deleted": budget_id})
    return json.dumps({"error": f"Budget {budget_id} not found"})


@mcp.tool()
def process_budget_rollover(budget_id: str | None = None) -> str:
    """Process budget rollovers — carry unspent budget forward to the next period.

    Args:
        budget_id: Specific budget ID (default: process all eligible budgets)
    """
    svc = get_service()
    if budget_id:
        try:
            result = svc.process_budget_rollover(budget_id)
            if result:
                return json.dumps(result.model_dump(), default=str, indent=2)
            return json.dumps({"message": "No rollover processed (disabled or already processed)"})
        except ValueError as e:
            return json.dumps({"error": str(e)})
    else:
        results = svc.process_all_rollovers()
        return json.dumps({
            "processed": len(results),
            "rollovers": [r.model_dump() for r in results],
        }, default=str, indent=2)


# --- Expense Tools ---

@mcp.tool()
def add_expense(
    amount: float,
    category: str,
    description: str = "",
    expense_date: str | None = None,
    tags: list[str] | None = None,
    currency: str = "USD",
    budget_id: str | None = None,
    metadata: dict | None = None,
    vendor: str | None = None,
    receipt_url: str | None = None,
    reimbursable: bool = False,
    approved_by: str | None = None,
) -> str:
    """Log a new expense.

    Args:
        amount: Expense amount
        category: Expense category
        description: Description of the expense
        expense_date: Date in YYYY-MM-DD format (defaults to today)
        tags: List of tags for grouping/filtering
        currency: Currency code (default USD)
        budget_id: Budget to count against (auto-assigned if category matches)
        metadata: Extra metadata (e.g., vendor, receipt URL)
        vendor: Vendor or merchant name
        receipt_url: URL to receipt or invoice
        reimbursable: Whether this expense is reimbursable
        approved_by: Who approved this expense
    """
    svc = get_service()
    parsed_date = date.fromisoformat(expense_date) if expense_date else None
    try:
        expense = svc.add_expense(
            amount=amount,
            category=category,
            description=description,
            expense_date=parsed_date,
            tags=tags or [],
            currency=currency,
            budget_id=budget_id,
            metadata=metadata or {},
            vendor=vendor,
            receipt_url=receipt_url,
            reimbursable=reimbursable,
            approved_by=approved_by,
        )
        return json.dumps(expense.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e), "blocked": True})


@mcp.tool()
def update_expense(
    expense_id: str,
    amount: float | None = None,
    category: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    vendor: str | None = None,
    receipt_url: str | None = None,
    reimbursable: bool | None = None,
    approved_by: str | None = None,
) -> str:
    """Update an existing expense.

    Args:
        expense_id: Expense ID to update
        amount: New amount
        category: New category
        description: New description
        tags: New tags
        status: New status (planned, confirmed, cancelled)
        vendor: New vendor
        receipt_url: New receipt URL
        reimbursable: New reimbursable flag
        approved_by: Set approver
    """
    svc = get_service()
    try:
        expense = svc.update_expense(
            expense_id=expense_id,
            amount=amount,
            category=category,
            description=description,
            tags=tags,
            status=status,
            vendor=vendor,
            receipt_url=receipt_url,
            reimbursable=reimbursable,
            approved_by=approved_by,
        )
        return json.dumps(expense.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_expenses(
    category: str | None = None,
    budget_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    tags: list[str] | None = None,
    vendor: str | None = None,
    reimbursable: bool | None = None,
) -> str:
    """List expenses with optional filtering.

    Args:
        category: Filter by category
        budget_id: Filter by budget
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        tags: Filter by tags
        vendor: Filter by vendor
        reimbursable: Filter by reimbursable status
    """
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    expenses = svc.list_expenses(
        category=category,
        budget_id=budget_id,
        start_date=parsed_start,
        end_date=parsed_end,
        tags=tags,
        vendor=vendor,
        reimbursable=reimbursable,
    )
    return json.dumps([e.model_dump() for e in expenses], default=str, indent=2)


@mcp.tool()
def get_expense(expense_id: str) -> str:
    """Get expense details.

    Args:
        expense_id: Expense ID
    """
    svc = get_service()
    expense = svc.get_expense(expense_id)
    if not expense:
        return json.dumps({"error": f"Expense {expense_id} not found"})
    return json.dumps(expense.model_dump(), default=str, indent=2)


@mcp.tool()
def delete_expense(expense_id: str) -> str:
    """Delete an expense.

    Args:
        expense_id: Expense ID to delete
    """
    svc = get_service()
    if svc.delete_expense(expense_id):
        return json.dumps({"deleted": expense_id})
    return json.dumps({"error": f"Expense {expense_id} not found"})


# --- Budget Status & Analysis ---

@mcp.tool()
def get_budget_status(budget_id: str | None = None) -> str:
    """Get current spending vs. budget. If budget_id is provided, returns status for that budget. Otherwise returns all active budgets.

    Args:
        budget_id: Optional specific budget ID
    """
    svc = get_service()
    if budget_id:
        try:
            comparisons = [svc.get_budget_status(budget_id)]
        except ValueError as e:
            return json.dumps({"error": str(e)})
    else:
        comparisons = svc.get_all_budget_status()
    return json.dumps([c.model_dump() for c in comparisons], default=str, indent=2)


@mcp.tool()
def compare_budget_actual(budget_id: str | None = None) -> str:
    """Detailed budget vs. actual comparison.

    Args:
        budget_id: Optional specific budget ID (defaults to all)
    """
    return get_budget_status(budget_id)


@mcp.tool()
def get_spending_forecast(
    months: int = 3,
    category: str | None = None,
    budget_id: str | None = None,
) -> str:
    """Project future spending based on historical data.

    Args:
        months: Number of months to forecast (default 3)
        category: Filter by category
        budget_id: Filter by budget
    """
    svc = get_service()
    forecasts = svc.get_spending_forecast(months=months, category=category, budget_id=budget_id)
    return json.dumps([f.model_dump() for f in forecasts], default=str, indent=2)


@mcp.tool()
def get_spending_summary(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get spending summary grouped by category.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    summary = svc.get_category_summary(start_date=parsed_start, end_date=parsed_end)
    total = sum(summary.values())
    return json.dumps({
        "categories": summary,
        "total": total,
        "period": f"{start_date or 'all'} to {end_date or 'now'}",
    }, indent=2)


# --- Savings Goals ---

@mcp.tool()
def create_savings_goal(
    name: str,
    target_amount: float,
    currency: str = "USD",
    target_date: str | None = None,
    category: str | None = None,
    description: str = "",
) -> str:
    """Create a savings goal with a target amount.

    Args:
        name: Goal name (e.g., 'Emergency Fund')
        target_amount: Target amount to save
        currency: Currency code (default USD)
        target_date: Target date to reach goal (YYYY-MM-DD)
        category: Associated budget category
        description: Goal description
    """
    svc = get_service()
    parsed_date = date.fromisoformat(target_date) if target_date else None
    goal = svc.create_savings_goal(
        name=name,
        target_amount=target_amount,
        currency=currency,
        target_date=parsed_date,
        category=category,
        description=description,
    )
    return json.dumps(goal.model_dump(), default=str, indent=2)


@mcp.tool()
def list_savings_goals(status: str | None = None) -> str:
    """List savings goals.

    Args:
        status: Filter by status (active, completed, paused)
    """
    svc = get_service()
    goals = svc.list_savings_goals(status=status)
    result = []
    for g in goals:
        data = g.model_dump()
        data["progress_percent"] = g.progress_percent
        data["remaining"] = g.remaining
        data["is_complete"] = g.is_complete
        data["monthly_contribution_needed"] = g.monthly_contribution_needed
        result.append(data)
    return json.dumps(result, default=str, indent=2)


@mcp.tool()
def get_savings_goal(goal_id: str) -> str:
    """Get savings goal details including progress.

    Args:
        goal_id: Savings goal ID
    """
    svc = get_service()
    goal = svc.get_savings_goal(goal_id)
    if not goal:
        return json.dumps({"error": f"Savings goal {goal_id} not found"})
    data = goal.model_dump()
    data["progress_percent"] = goal.progress_percent
    data["remaining"] = goal.remaining
    data["is_complete"] = goal.is_complete
    data["monthly_contribution_needed"] = goal.monthly_contribution_needed
    return json.dumps(data, default=str, indent=2)


@mcp.tool()
def contribute_to_savings(
    goal_id: str,
    amount: float,
    note: str = "",
    contribution_date: str | None = None,
) -> str:
    """Add a contribution to a savings goal.

    Args:
        goal_id: Savings goal ID
        amount: Amount to contribute
        note: Optional note about this contribution
        contribution_date: Date of contribution (YYYY-MM-DD, defaults to today)
    """
    svc = get_service()
    parsed_date = date.fromisoformat(contribution_date) if contribution_date else None
    try:
        goal = svc.contribute_to_savings(goal_id, amount=amount, note=note, contribution_date=parsed_date)
        return json.dumps({
            "goal_id": goal.id,
            "goal_name": goal.name,
            "contribution_amount": amount,
            "current_amount": goal.current_amount,
            "target_amount": goal.target_amount,
            "progress_percent": goal.progress_percent,
            "remaining": goal.remaining,
            "is_complete": goal.is_complete,
            "status": goal.status.value,
        }, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def withdraw_from_savings(
    goal_id: str,
    amount: float,
    note: str = "",
) -> str:
    """Withdraw from a savings goal.

    Args:
        goal_id: Savings goal ID
        amount: Amount to withdraw
        note: Optional note about this withdrawal
    """
    svc = get_service()
    try:
        goal = svc.withdraw_from_savings(goal_id, amount=amount, note=note)
        return json.dumps({
            "goal_id": goal.id,
            "goal_name": goal.name,
            "withdrawal_amount": amount,
            "current_amount": goal.current_amount,
            "target_amount": goal.target_amount,
            "progress_percent": goal.progress_percent,
            "remaining": goal.remaining,
            "status": goal.status.value,
        }, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_savings_goal(
    goal_id: str,
    name: str | None = None,
    target_amount: float | None = None,
    target_date: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> str:
    """Update a savings goal.

    Args:
        goal_id: Savings goal ID
        name: New name
        target_amount: New target amount
        target_date: New target date (YYYY-MM-DD)
        description: New description
        status: New status (active, completed, paused)
    """
    svc = get_service()
    parsed_date = date.fromisoformat(target_date) if target_date else None
    parsed_status = SavingsGoalStatus(status) if status else None
    try:
        goal = svc.update_savings_goal(
            goal_id=goal_id,
            name=name,
            target_amount=target_amount,
            target_date=parsed_date,
            description=description,
            status=parsed_status,
        )
        return json.dumps(goal.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_savings_goal(goal_id: str) -> str:
    """Delete a savings goal.

    Args:
        goal_id: Savings goal ID to delete
    """
    svc = get_service()
    if svc.delete_savings_goal(goal_id):
        return json.dumps({"deleted": goal_id})
    return json.dumps({"error": f"Savings goal {goal_id} not found"})


# --- Spending Rules ---

@mcp.tool()
def create_spending_rule(
    name: str,
    category: str,
    action: str,
    threshold_amount: float | None = None,
    threshold_percent: float | None = None,
    budget_id: str | None = None,
    requires_approval_above: float | None = None,
    description: str = "",
) -> str:
    """Create a spending rule to control expense behavior.

    Args:
        name: Rule name (e.g., 'API spending cap')
        category: Category this rule applies to
        action: Action when triggered (warn, block, approve)
        threshold_amount: Max total spending amount before triggering
        threshold_percent: Max percent of budget before triggering
        budget_id: Associated budget ID
        requires_approval_above: Single expenses above this need approval
        description: Rule description
    """
    svc = get_service()
    try:
        rule = svc.create_spending_rule(
            name=name,
            category=category,
            action=SpendingRuleAction(action),
            threshold_amount=threshold_amount,
            threshold_percent=threshold_percent,
            budget_id=budget_id,
            requires_approval_above=requires_approval_above,
            description=description,
        )
        return json.dumps(rule.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_spending_rules(enabled_only: bool = True) -> str:
    """List spending rules.

    Args:
        enabled_only: Only show enabled rules (default True)
    """
    svc = get_service()
    rules = svc.list_spending_rules(enabled_only=enabled_only)
    return json.dumps([r.model_dump() for r in rules], default=str, indent=2)


@mcp.tool()
def check_expense_rules(
    amount: float,
    category: str,
    budget_id: str | None = None,
) -> str:
    """Check if a hypothetical expense would violate any spending rules, without actually adding it.

    Args:
        amount: Expense amount to check
        category: Expense category to check
        budget_id: Optional budget ID for context
    """
    svc = get_service()
    from .models import Expense
    test_expense = Expense(amount=amount, category=category, budget_id=budget_id)
    violations = svc.check_expense_rules(test_expense)
    if not violations:
        return json.dumps({"allowed": True, "violations": []})
    blocked = any("would exceed" in v or "exceeds approval" in v for v in violations)
    return json.dumps({
        "allowed": not blocked,
        "violations": violations,
        "action": "blocked" if blocked else "warned",
    }, indent=2)


@mcp.tool()
def update_spending_rule(
    rule_id: str,
    name: str | None = None,
    action: str | None = None,
    threshold_amount: float | None = None,
    threshold_percent: float | None = None,
    enabled: bool | None = None,
    requires_approval_above: float | None = None,
    description: str | None = None,
) -> str:
    """Update a spending rule.

    Args:
        rule_id: Rule ID to update
        name: New name
        action: New action (warn, block, approve)
        threshold_amount: New threshold amount
        threshold_percent: New threshold percent
        enabled: Enable or disable
        requires_approval_above: New approval threshold
        description: New description
    """
    svc = get_service()
    try:
        rule = svc.update_spending_rule(
            rule_id=rule_id,
            name=name,
            action=SpendingRuleAction(action) if action else None,
            threshold_amount=threshold_amount,
            threshold_percent=threshold_percent,
            enabled=enabled,
            requires_approval_above=requires_approval_above,
            description=description,
        )
        return json.dumps(rule.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_spending_rule(rule_id: str) -> str:
    """Delete a spending rule.

    Args:
        rule_id: Rule ID to delete
    """
    svc = get_service()
    if svc.delete_spending_rule(rule_id):
        return json.dumps({"deleted": rule_id})
    return json.dumps({"error": f"Spending rule {rule_id} not found"})


# --- Recurring Expenses ---

@mcp.tool()
def add_recurring_expense(
    name: str,
    amount: float,
    category: str,
    frequency: str,
    description: str = "",
    currency: str = "USD",
    tags: list[str] | None = None,
    budget_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Set up a recurring expense.

    Args:
        name: Name of the recurring expense
        amount: Amount per occurrence
        category: Expense category
        frequency: How often (daily, weekly, biweekly, monthly, quarterly, yearly)
        description: Description
        currency: Currency code
        tags: Tags
        budget_id: Budget to count against
        start_date: Start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    recurring = svc.add_recurring_expense(
        name=name,
        amount=amount,
        category=category,
        frequency=RecurringFrequency(frequency),
        description=description,
        currency=currency,
        tags=tags or [],
        budget_id=budget_id,
        start_date=parsed_start,
        end_date=parsed_end,
    )
    return json.dumps(recurring.model_dump(), default=str, indent=2)


@mcp.tool()
def list_recurring_expenses(active_only: bool = True) -> str:
    """List recurring expense templates.

    Args:
        active_only: Only show active recurring expenses
    """
    svc = get_service()
    recurrings = svc.list_recurring_expenses(active_only=active_only)
    return json.dumps([r.model_dump() for r in recurrings], default=str, indent=2)


@mcp.tool()
def process_recurring_expenses() -> str:
    """Generate expenses for all due recurring templates. Run daily via cron."""
    svc = get_service()
    generated = svc.process_recurring_expenses()
    return json.dumps({
        "generated_count": len(generated),
        "expenses": [e.model_dump() for e in generated],
    }, default=str, indent=2)


# --- Alerts ---

@mcp.tool()
def get_alerts(budget_id: str | None = None) -> str:
    """Check for budget alerts.

    Args:
        budget_id: Filter by budget
    """
    svc = get_service()
    alerts = svc.get_alerts(budget_id=budget_id)
    return json.dumps([a.model_dump() for a in alerts], default=str, indent=2)


@mcp.tool()
def clear_alerts(budget_id: str | None = None) -> str:
    """Clear budget alerts.

    Args:
        budget_id: Clear alerts for a specific budget (default: all)
    """
    svc = get_service()
    count = svc.clear_alerts(budget_id=budget_id)
    return json.dumps({"cleared": count})


@mcp.tool()
def update_alert_thresholds(
    budget_id: str,
    thresholds: list[dict],
) -> str:
    """Update alert thresholds for a budget.

    Args:
        budget_id: Budget ID to update
        thresholds: List of threshold objects, each with 'percent' (0-100) and 'level' (info, warning, critical)
    """
    svc = get_service()
    from .models import AlertThreshold, AlertLevel
    try:
        parsed = [AlertThreshold(percent=t["percent"], level=AlertLevel(t["level"])) for t in thresholds]
        budget = svc.update_alert_thresholds(budget_id, parsed)
        return json.dumps(budget.model_dump(), default=str, indent=2)
    except (ValueError, KeyError) as e:
        return json.dumps({"error": str(e)})


# --- Export ---

@mcp.tool()
def export_data(format: str = "json") -> str:
    """Export budget and expense data.

    Args:
        format: Export format (json, csv, markdown)
    """
    svc = get_service()
    return svc.export_data(format=format)


@mcp.tool()
def list_currencies() -> str:
    """List all supported currencies."""
    return json.dumps([c.model_dump() for c in SUPPORTED_CURRENCIES.values()], indent=2)


# --- v0.3.0: CSV Import ---

@mcp.tool()
def import_csv(
    file_path: str,
    category: str | None = None,
    currency: str = "USD",
    budget_id: str | None = None,
    skip_duplicates: bool = True,
) -> str:
    """Import expenses from a CSV file. Supports common column names (date, amount, category, description, vendor, tags, currency).

    Args:
        file_path: Path to the CSV file
        category: Default category for expenses without one
        currency: Default currency code (default USD)
        budget_id: Default budget ID to assign
        skip_duplicates: Skip rows that match existing expenses (default True)
    """
    svc = get_service()
    try:
        result = svc.import_csv(
            file_path=file_path,
            category=category,
            currency=currency,
            budget_id=budget_id,
            skip_duplicates=skip_duplicates,
        )
        return json.dumps(result.model_dump(), indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


# --- v0.3.0: Spending Analytics ---

@mcp.tool()
def get_spending_trends(
    category: str | None = None,
    period_type: str = "monthly",
) -> str:
    """Analyze spending trends between current and previous periods.

    Args:
        category: Filter by specific category (default: all categories)
        period_type: Period type — 'monthly', 'weekly', or 'quarterly' (default monthly)
    """
    svc = get_service()
    trends = svc.get_spending_trends(category=category, period_type=period_type)
    return json.dumps([t.model_dump() for t in trends], default=str, indent=2)


@mcp.tool()
def get_category_breakdown(
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 10,
) -> str:
    """Get detailed spending breakdown by category with averages, counts, and top vendors.

    Args:
        start_date: Start date (YYYY-MM-DD, defaults to current month start)
        end_date: End date (YYYY-MM-DD, defaults to today)
        top_n: Number of top categories to return (default 10)
    """
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    breakdown = svc.get_category_breakdown(start_date=parsed_start, end_date=parsed_end, top_n=top_n)
    return json.dumps([b.model_dump() for b in breakdown], default=str, indent=2)


@mcp.tool()
def compare_periods(
    period_a_start: str,
    period_a_end: str,
    period_b_start: str,
    period_b_end: str,
) -> str:
    """Compare spending between two time periods with per-category trends.

    Args:
        period_a_start: Start of period A / older period (YYYY-MM-DD)
        period_a_end: End of period A (YYYY-MM-DD)
        period_b_start: Start of period B / newer period (YYYY-MM-DD)
        period_b_end: End of period B (YYYY-MM-DD)
    """
    svc = get_service()
    comparison = svc.compare_periods(
        period_a_start=date.fromisoformat(period_a_start),
        period_a_end=date.fromisoformat(period_a_end),
        period_b_start=date.fromisoformat(period_b_start),
        period_b_end=date.fromisoformat(period_b_end),
    )
    return json.dumps(comparison.model_dump(), default=str, indent=2)


# --- v0.3.0: Budget Templates ---

@mcp.tool()
def list_budget_templates(category: str | None = None) -> str:
    """List available budget templates (built-in and custom).

    Args:
        category: Filter by category
    """
    svc = get_service()
    templates = svc.list_budget_templates(category=category)
    return json.dumps([t.model_dump() for t in templates], default=str, indent=2)


@mcp.tool()
def get_budget_template(template_id: str) -> str:
    """Get details for a specific budget template.

    Args:
        template_id: Template ID
    """
    svc = get_service()
    template = svc.get_budget_template(template_id)
    if not template:
        return json.dumps({"error": f"Template {template_id} not found"})
    return json.dumps(template.model_dump(), default=str, indent=2)


@mcp.tool()
def create_budget_template(
    name: str,
    category: str,
    default_limit: float,
    period: str,
    description: str = "",
    currency: str = "USD",
) -> str:
    """Create a custom budget template for reuse.

    Args:
        name: Template name
        category: Budget category
        default_limit: Default spending limit
        period: Budget period (daily, weekly, monthly, quarterly, yearly)
        description: Template description
        currency: Currency code (default USD)
    """
    svc = get_service()
    try:
        template = svc.create_budget_template(
            name=name,
            category=category,
            default_limit=default_limit,
            period=BudgetPeriod(period),
            description=description,
            currency=currency,
        )
        return json.dumps(template.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def instantiate_budget_template(
    template_id: str,
    name: str | None = None,
    limit: float | None = None,
    currency: str | None = None,
) -> str:
    """Create a budget from a template, including suggested alerts and spending rules.

    Args:
        template_id: Template ID to instantiate
        name: Override template name (optional)
        limit: Override template default limit (optional)
        currency: Override template currency (optional)
    """
    svc = get_service()
    try:
        budget = svc.instantiate_budget_template(
            template_id=template_id,
            name=name,
            limit=limit,
            currency=currency,
        )
        return json.dumps(budget.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


# --- v0.4.0: Income & Cash Flow Tools ---

@mcp.tool()
def add_income(
    amount: float,
    source: str,
    description: str = "",
    income_date: str | None = None,
    tags: str = "",
    currency: str = "USD",
    status: str = "received",
    invoice_ref: str | None = None,
) -> str:
    """Record a new income/revenue entry.

    Args:
        amount: Income amount (must be positive)
        source: Income source (e.g., 'client-A', 'API-sales', 'consulting')
        description: Description of the income
        income_date: Date in YYYY-MM-DD format (defaults to today)
        tags: Comma-separated tags
        currency: Currency code (default USD)
        status: Income status - 'received', 'pending', or 'cancelled'
        invoice_ref: Optional invoice reference
    """
    svc = get_service()
    try:
        from datetime import datetime as dt
        parsed_date = dt.strptime(income_date, "%Y-%m-%d").date() if income_date else None
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        income = svc.add_income(
            amount=amount,
            source=source,
            description=description,
            income_date=parsed_date,
            tags=tag_list,
            currency=currency,
            status=IncomeStatus(status),
            invoice_ref=invoice_ref,
        )
        return json.dumps(income.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_income(
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> str:
    """List income entries with optional filters.

    Args:
        source: Filter by income source
        start_date: Filter from date (YYYY-MM-DD)
        end_date: Filter to date (YYYY-MM-DD)
        status: Filter by status (received, pending, cancelled)
    """
    svc = get_service()
    from datetime import datetime as dt
    parsed_start = dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    parsed_end = dt.strptime(end_date, "%Y-%m-%d").date() if end_date else None
    incomes = svc.list_income(
        source=source, start_date=parsed_start, end_date=parsed_end, status=status,
    )
    return json.dumps([i.model_dump() for i in incomes], default=str, indent=2)


@mcp.tool()
def update_income(
    income_id: str,
    amount: float | None = None,
    source: str | None = None,
    description: str | None = None,
    status: str | None = None,
    invoice_ref: str | None = None,
) -> str:
    """Update an existing income entry.

    Args:
        income_id: Income entry ID
        amount: New amount (optional)
        source: New source (optional)
        description: New description (optional)
        status: New status (optional)
        invoice_ref: New invoice reference (optional)
    """
    svc = get_service()
    try:
        income = svc.update_income(
            income_id=income_id,
            amount=amount,
            source=source,
            description=description,
            status=IncomeStatus(status) if status else None,
            invoice_ref=invoice_ref,
        )
        return json.dumps(income.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_income(income_id: str) -> str:
    """Delete an income entry.

    Args:
        income_id: Income entry ID to delete
    """
    svc = get_service()
    deleted = svc.delete_income(income_id)
    return json.dumps({"deleted": deleted, "income_id": income_id})


@mcp.tool()
def get_income_summary(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get income breakdown by source for a period.

    Args:
        start_date: Start date (YYYY-MM-DD), defaults to 90 days ago
        end_date: End date (YYYY-MM-DD), defaults to today
    """
    svc = get_service()
    from datetime import datetime as dt, timedelta
    end = dt.strptime(end_date, "%Y-%m-%d").date() if end_date else date.today()
    start = dt.strptime(start_date, "%Y-%m-%d").date() if start_date else end - timedelta(days=90)
    summary = svc.get_income_summary(start_date=start, end_date=end)
    return json.dumps({"summary": summary, "total": sum(summary.values())}, default=str, indent=2)


@mcp.tool()
def add_recurring_income(
    name: str,
    amount: float,
    source: str,
    frequency: str,
    description: str = "",
    currency: str = "USD",
    tags: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Create a recurring income template.

    Args:
        name: Name for this recurring income (e.g., 'Monthly consulting retainer')
        amount: Amount per occurrence (must be positive)
        source: Income source/counterparty
        frequency: How often - 'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'yearly'
        description: Description
        currency: Currency code (default USD)
        tags: Comma-separated tags
        start_date: Start date (YYYY-MM-DD), defaults to today
        end_date: Optional end date (YYYY-MM-DD)
    """
    svc = get_service()
    try:
        from datetime import datetime as dt
        parsed_start = dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        parsed_end = dt.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        recurring = svc.add_recurring_income(
            name=name,
            amount=amount,
            source=source,
            frequency=RecurringFrequency(frequency),
            description=description,
            currency=currency,
            tags=tag_list,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        return json.dumps(recurring.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_recurring_income(active_only: bool = True) -> str:
    """List recurring income templates.

    Args:
        active_only: If true, only show active templates (default true)
    """
    svc = get_service()
    recurring = svc.list_recurring_income(active_only=active_only)
    return json.dumps([r.model_dump() for r in recurring], default=str, indent=2)


@mcp.tool()
def process_recurring_income() -> str:
    """Process all due recurring income entries. Generates income records for any recurring income that is due."""
    svc = get_service()
    generated = svc.process_recurring_income()
    return json.dumps({
        "processed": len(generated),
        "total_amount": sum(i.amount for i in generated),
        "income_ids": [i.id for i in generated],
    }, default=str, indent=2)


@mcp.tool()
def get_cash_flow(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get cash flow analysis (income vs expenses) for a period.

    Args:
        start_date: Start date (YYYY-MM-DD), defaults to start of current month
        end_date: End date (YYYY-MM-DD), defaults to today

    Returns net cash flow, savings rate, expense ratio, and profitability status.
    """
    svc = get_service()
    from datetime import datetime as dt
    parsed_start = dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    parsed_end = dt.strptime(end_date, "%Y-%m-%d").date() if end_date else None
    flow = svc.get_cash_flow(start_date=parsed_start, end_date=parsed_end)
    return json.dumps(flow.model_dump(), default=str, indent=2)


@mcp.tool()
def get_burn_rate(months: int = 3) -> str:
    """Calculate burn rate and runway based on spending history.

    Args:
        months: Number of months to analyze (default 3)

    Returns average monthly burn, net burn, runway in months, and sustainability status.
    """
    svc = get_service()
    try:
        burn = svc.get_burn_rate(months=months)
        return json.dumps(burn.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_financial_dashboard() -> str:
    """Get a comprehensive financial health dashboard.

    Includes budget status, savings progress, cash flow, burn rate, and a health score (0-100).
    """
    svc = get_service()
    dashboard = svc.get_financial_dashboard()
    return json.dumps(dashboard.model_dump(), default=str, indent=2)


# --- v0.5.0: Cost Guardrail & Kill Switch Tools ---

@mcp.tool()
def check_cost_guardrail(
    estimated_cost_usd: float = 0.0,
    agent_id: str | None = None,
    model_id: str | None = None,
    budget_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """Pre-flight check before an LLM call — should the agent proceed?

    Call this BEFORE making an LLM API call to check if you're within budget
    limits. Returns a decision: allowed (proceed), warn (proceed but slow down),
    or blocked (do NOT proceed). If blocked, the reason explains which guardrail
    was triggered and provides cost-saving suggestions.

    Args:
        estimated_cost_usd: Estimated cost of the upcoming LLM call
        agent_id: Your agent identifier (for per-agent guardrails)
        model_id: Model you plan to call (e.g., 'gpt-4o')
        budget_id: Associated budget ID
        task_id: Task/session identifier
    """
    svc = get_service()
    decision = svc.check_guardrails(
        estimated_cost_usd=estimated_cost_usd,
        agent_id=agent_id,
        model_id=model_id,
        budget_id=budget_id,
        task_id=task_id,
    )
    return json.dumps(decision.model_dump(), default=str, indent=2)


@mcp.tool()
def create_cost_guardrail(
    name: str,
    scope: str,
    scope_id: str | None = None,
    daily_limit_usd: float | None = None,
    hourly_limit_usd: float | None = None,
    per_call_limit_usd: float | None = None,
    monthly_limit_usd: float | None = None,
    warn_at_percent: float = 80.0,
    block_at_percent: float = 100.0,
    cooldown_minutes: int = 0,
    throttle_enabled: bool = False,
    priority: int = 0,
    description: str = "",
) -> str:
    """Create a cost guardrail to enforce spending limits for LLM calls.

    Guardrails are checked before each LLM call (via check_cost_guardrail).
    Unlike spending rules (which check expenses after-the-fact), guardrails
    are pre-flight checks that can BLOCK calls before they happen.

    v0.8.0: Set throttle_enabled=True to activate progressive cost throttling.
    When enabled, the guardrail uses graduated spend tiers (60%, 75%, 90%)
    that recommend max per-call costs and model downgrades BEFORE hitting
    the hard block threshold. This enables graceful degradation instead of
    a cliff-edge cutoff.

    Args:
        name: Guardrail name (e.g., 'Daily LLM cap')
        scope: Scope — 'global', 'agent', 'model', 'budget', or 'task'
        scope_id: ID for scoped guardrails (agent_id, model_id, budget_id, task_id)
        daily_limit_usd: Max daily spend in USD
        hourly_limit_usd: Max hourly spend in USD
        per_call_limit_usd: Max cost per single LLM call
        monthly_limit_usd: Max monthly spend
        warn_at_percent: Percent of limit to start warning (default 80)
        block_at_percent: Percent of limit to block at (default 100)
        cooldown_minutes: If breached, block calls for N minutes
        throttle_enabled: Enable progressive cost throttling (default False)
        priority: Higher priority checked first (default 0)
        description: Optional description
    """
    svc = get_service()
    try:
        guardrail = svc.create_guardrail(
            name=name,
            scope=GuardrailScope(scope),
            scope_id=scope_id,
            daily_limit_usd=daily_limit_usd,
            hourly_limit_usd=hourly_limit_usd,
            per_call_limit_usd=per_call_limit_usd,
            monthly_limit_usd=monthly_limit_usd,
            warn_at_percent=warn_at_percent,
            block_at_percent=block_at_percent,
            cooldown_minutes=cooldown_minutes,
            throttle_enabled=throttle_enabled,
            priority=priority,
            description=description,
        )
        return json.dumps(guardrail.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_cost_guardrails(enabled_only: bool = True) -> str:
    """List all cost guardrails, sorted by priority.

    Args:
        enabled_only: Only show enabled guardrails (default true)
    """
    svc = get_service()
    guardrails = svc.list_guardrails(enabled_only=enabled_only)
    return json.dumps([g.model_dump() for g in guardrails], default=str, indent=2)


@mcp.tool()
def delete_cost_guardrail(guardrail_id: str) -> str:
    """Delete a cost guardrail by ID.

    Args:
        guardrail_id: The guardrail ID (starts with 'GDR-')
    """
    svc = get_service()
    deleted = svc.delete_guardrail(guardrail_id)
    return json.dumps({"deleted": deleted, "guardrail_id": guardrail_id})


@mcp.tool()
def enable_progressive_throttling(
    guardrail_id: str,
    enabled: bool = True,
) -> str:
    """Enable or disable progressive cost throttling on a guardrail.

    v0.8.0: Progressive throttling provides graceful spend degradation.
    Instead of a binary allow/block, the guardrail activates graduated
    cost tiers as spend increases:

      - 60% of limit: recommend max $0.50/call, reduce context
      - 75% of limit: recommend max $0.20/call, switch to cheaper model
      - 90% of limit: hard cap at $0.05/call (blocks expensive calls)

    This prevents "cliff edge" behavior where an agent goes from full-speed
    to completely blocked with no intermediate signal.

    Args:
        guardrail_id: The guardrail ID (starts with 'GDR-')
        enabled: True to enable throttling, False to disable
    """
    svc = get_service()
    try:
        guardrail = svc.update_guardrail(guardrail_id, throttle_enabled=enabled)
        return json.dumps({
            "guardrail_id": guardrail.id,
            "throttle_enabled": guardrail.throttle_enabled,
            "throttle_tiers": [t.model_dump() for t in guardrail.throttle_tiers],
        }, default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def trigger_kill_switch(
    reason: str,
    triggered_by: str | None = None,
    expires_in_minutes: int | None = None,
    override_token: str | None = None,
) -> str:
    """Trigger the emergency kill switch — blocks ALL LLM calls immediately.

    This is the nuclear option. When active, check_cost_guardrail will return
    action='kill' for every call, preventing any LLM spending.

    Args:
        reason: Why the kill switch is being triggered
        triggered_by: Who or what triggered it
        expires_in_minutes: Auto-reset after N minutes (None = manual reset only)
        override_token: Token required to reset (for safety, prevents accidental reset)
    """
    svc = get_service()
    ks = svc.trigger_kill_switch(
        reason=reason,
        triggered_by=triggered_by,
        expires_in_minutes=expires_in_minutes,
        override_token=override_token,
    )
    return json.dumps(ks.model_dump(), default=str, indent=2)


@mcp.tool()
def reset_kill_switch(override_token: str | None = None) -> str:
    """Reset the kill switch, allowing LLM calls again.

    Args:
        override_token: Required if the kill switch was set with a token
    """
    svc = get_service()
    try:
        ks = svc.reset_kill_switch(override_token=override_token)
        return json.dumps(ks.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_kill_switch_status() -> str:
    """Check if the kill switch is currently active."""
    svc = get_service()
    ks = svc.get_kill_switch_status()
    return json.dumps(ks.model_dump(), default=str, indent=2)


@mcp.tool()
def list_cost_alerts(
    guardrail_id: str | None = None,
    unacknowledged_only: bool = False,
    limit: int = 50,
) -> str:
    """List cost alert events triggered by guardrails.

    Args:
        guardrail_id: Filter by guardrail ID
        unacknowledged_only: Only show alerts that haven't been acknowledged
        limit: Max results (default 50)
    """
    svc = get_service()
    alerts = svc.list_cost_alerts(
        guardrail_id=guardrail_id,
        unacknowledged_only=unacknowledged_only,
        limit=limit,
    )
    return json.dumps([a.model_dump() for a in alerts], default=str, indent=2)


# --- v0.6.0 Spend Projection & Loop Detection Tools ---

@mcp.tool()
def project_spend(
    scope: str = "global",
    scope_id: str | None = None,
    period: str = "daily",
) -> str:
    """Project spend and predict when limits will be hit (burn forecast).

    Analyzes recent LLM usage to predict whether you'll breach guardrail
    limits before the period ends. Use this to proactively slow down
    BEFORE a guardrail hard-blocks your calls.

    Args:
        scope: global, agent, model, budget, or task
        scope_id: Entity ID for scoped projections (e.g., agent_id)
        period: daily, hourly, or monthly
    """
    svc = get_service()
    from .models import GuardrailScope
    projection = svc.project_spend(
        scope=GuardrailScope(scope),
        scope_id=scope_id,
        period=period,
    )
    return json.dumps(projection.model_dump(), default=str, indent=2)


@mcp.tool()
def check_loop(
    agent_id: str | None = None,
    model_id: str | None = None,
) -> str:
    """Check if an agent is in a runaway loop based on recent call patterns.

    Detects repeated similar LLM calls within a time window — a common
    failure mode where an agent burns budget retrying the same operation.

    Args:
        agent_id: Agent to check (None = all agents)
        model_id: Filter to specific model
    """
    svc = get_service()
    result = svc.check_loop(agent_id=agent_id, model_id=model_id)
    return json.dumps(result.model_dump(), default=str, indent=2)


@mcp.tool()
def create_loop_config(
    name: str,
    window_minutes: int = 10,
    repeat_threshold: int = 5,
    similarity_threshold: float = 0.9,
    agent_id: str | None = None,
    model_id: str | None = None,
    auto_block_minutes: int = 0,
    min_cost_usd: float = 0.0,
) -> str:
    """Create a loop detection configuration.

    Args:
        name: Config name (e.g., 'Global loop guard')
        window_minutes: Detection window in minutes (default 10)
        repeat_threshold: Number of similar calls to flag a loop (default 5)
        similarity_threshold: Jaccard similarity threshold 0-1 (default 0.9)
        agent_id: Only apply to this agent (None = all)
        model_id: Only apply to this model (None = all)
        auto_block_minutes: Auto-block agent for N minutes (0 = just alert)
        min_cost_usd: Minimum cumulative cost to flag (0 = always flag)
    """
    svc = get_service()
    config = svc.create_loop_config(
        name=name,
        window_minutes=window_minutes,
        repeat_threshold=repeat_threshold,
        similarity_threshold=similarity_threshold,
        agent_id=agent_id,
        model_id=model_id,
        auto_block_minutes=auto_block_minutes,
        min_cost_usd=min_cost_usd,
    )
    return json.dumps(config.model_dump(), default=str, indent=2)


@mcp.tool()
def list_loop_configs(enabled_only: bool = True) -> str:
    """List loop detection configurations.

    Args:
        enabled_only: Only show enabled configs (default True)
    """
    svc = get_service()
    configs = svc.list_loop_configs(enabled_only=enabled_only)
    return json.dumps([c.model_dump() for c in configs], default=str, indent=2)


@mcp.tool()
def delete_loop_config(config_id: str) -> str:
    """Delete a loop detection configuration.

    Args:
        config_id: Config ID to delete
    """
    svc = get_service()
    deleted = svc.delete_loop_config(config_id)
    return json.dumps({"deleted": deleted, "config_id": config_id}, indent=2)




@mcp.tool()
def create_webhook(
    name: str,
    url: str,
    events: str | None = None,
    secret: str | None = None,
    scope: str | None = None,
    scope_id: str | None = None,
    max_retries: int = 3,
    timeout_seconds: float = 10.0,
) -> str:
    """Register a webhook endpoint for guardrail/budget notifications.

    When guardrails trigger (warn/block/kill) or the kill switch activates,
    matching webhooks receive a POST with event details. Enables integration
    with Slack, Discord, PagerDuty, custom dashboards, etc.

    Args:
        name: Webhook name (e.g., 'Slack #alerts')
        url: Webhook URL to POST to
        events: Comma-separated event types (default: all). Options:
            guardrail_warn, guardrail_block, guardrail_kill,
            kill_switch_triggered, kill_switch_reset,
            projection_breach, loop_detected, budget_threshold
        secret: Optional HMAC-SHA256 signing secret
        scope: Filter to scope (global, agent, model, budget, task)
        scope_id: Filter to specific scope ID
        max_retries: Max delivery retries on failure (default 3)
        timeout_seconds: Request timeout (default 10)
    """
    svc = get_service()
    event_list = events.split(",") if events else None
    webhook = svc.create_webhook(
        name=name,
        url=url,
        events=event_list,
        secret=secret,
        scope=scope,
        scope_id=scope_id,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
    )
    return json.dumps(webhook.model_dump(), default=str, indent=2)


@mcp.tool()
def list_webhooks(enabled_only: bool = True) -> str:
    """List all registered webhooks.

    Args:
        enabled_only: Only show enabled webhooks (default True)
    """
    svc = get_service()
    webhooks = svc.list_webhooks(enabled_only=enabled_only)
    return json.dumps([w.model_dump() for w in webhooks], default=str, indent=2)


@mcp.tool()
def delete_webhook(webhook_id: str) -> str:
    """Delete a webhook endpoint.

    Args:
        webhook_id: Webhook ID (starts with 'WHK-')
    """
    svc = get_service()
    deleted = svc.delete_webhook(webhook_id)
    return json.dumps({"deleted": deleted, "webhook_id": webhook_id})


@mcp.tool()
def test_webhook(webhook_id: str) -> str:
    """Send a test event to a webhook to verify it works.

    Args:
        webhook_id: Webhook ID to test (starts with 'WHK-')
    """
    svc = get_service()
    result = svc.test_webhook(webhook_id)
    return json.dumps(result, default=str, indent=2)


@mcp.tool()
def list_webhook_deliveries(webhook_id: str | None = None, limit: int = 50) -> str:
    """List recent webhook delivery records.

    Args:
        webhook_id: Filter by webhook ID (optional)
        limit: Max deliveries to return (default 50)
    """
    svc = get_service()
    deliveries = svc.list_webhook_deliveries(webhook_id=webhook_id, limit=limit)
    return json.dumps([d.model_dump() for d in deliveries], default=str, indent=2)


@mcp.tool()
def check_guardrails_smart(
    estimated_cost_usd: float = 0.0,
    agent_id: str | None = None,
    model_id: str | None = None,
    budget_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """Enhanced guardrail check with spend projection integration.

    Like check_cost_guardrail, but also runs a spend projection to warn
    proactively if current spending rate will breach a guardrail before
    period end. Use this for 'smart' pre-flight checks that anticipate
    breaches rather than just reacting to them.

    Args:
        estimated_cost_usd: Estimated cost of the upcoming LLM call
        agent_id: Agent making the call
        model_id: Model being called
        budget_id: Associated budget
        task_id: Task/session ID
    """
    svc = get_service()
    decision = svc.check_guardrails_with_projection(
        estimated_cost_usd=estimated_cost_usd,
        agent_id=agent_id,
        model_id=model_id,
        budget_id=budget_id,
        task_id=task_id,
    )
    return json.dumps(decision.model_dump(), default=str, indent=2)


# === v0.9.0 Concurrency-safe reserve/settle protocol ===

@mcp.tool()
def reserve_and_check(
    estimated_cost_usd: float = 0.0,
    agent_id: str | None = None,
    model_id: str | None = None,
    budget_id: str | None = None,
    task_id: str | None = None,
    ttl_minutes: int = 5,
) -> str:
    """Atomically check guardrails AND reserve budget for an in-flight LLM call.

    This is the CONCURRENCY-SAFE replacement for check_cost_guardrail when
    multiple agents run in parallel. It prevents the race condition where N
    concurrent agents all read the same under-limit spend and all fire past
    the ceiling.

    Workflow:
      1. Call this BEFORE your LLM call — it checks limits AND reserves
         the estimated cost atomically.
      2. If allowed, make your LLM call.
      3. After the call, call settle_reservation with the actual cost.
      4. If you decide NOT to make the call, call release_reservation to
         return the budget.

    Args:
        estimated_cost_usd: Best-guess cost of the upcoming call
        agent_id: Agent making the call
        model_id: Model being called
        budget_id: Associated budget
        task_id: Task/session ID
        ttl_minutes: Reservation auto-expires after this many minutes

    Returns:
        JSON with the guardrail decision and reservation details.
        If the call was blocked, reservation_id will be null.
    """
    svc = get_service()
    decision, reservation = svc.reserve_and_check(
        estimated_cost_usd=estimated_cost_usd,
        agent_id=agent_id,
        model_id=model_id,
        budget_id=budget_id,
        task_id=task_id,
        ttl_minutes=ttl_minutes,
    )
    result = {
        "decision": decision.model_dump(),
        "reservation_id": reservation.id if reservation else None,
        "reservation": reservation.model_dump() if reservation else None,
    }
    return json.dumps(result, default=str, indent=2)


@mcp.tool()
def settle_reservation(
    reservation_id: str,
    actual_cost_usd: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_id: str | None = None,
) -> str:
    """Settle a reservation with actual usage data after an LLM call completes.

    Records the true cost and closes the reservation. The reserved budget
    is replaced by the actual cost in spend calculations.

    Args:
        reservation_id: ID returned by reserve_and_check
        actual_cost_usd: Real cost of the completed call
        input_tokens: Prompt tokens used
        output_tokens: Completion tokens used
        model_id: Override the model ID if different from reservation
    """
    svc = get_service()
    try:
        settled = svc.settle_reservation(
            reservation_id=reservation_id,
            actual_cost_usd=actual_cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=model_id,
        )
        return json.dumps(settled.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
def release_reservation(
    reservation_id: str,
    reason: str = "released",
) -> str:
    """Release a reservation without settling (call never made or failed).

    Returns the reserved budget to the pool immediately.

    Args:
        reservation_id: ID returned by reserve_and_check
        reason: Why the reservation is being released
    """
    svc = get_service()
    try:
        released = svc.release_reservation(
            reservation_id=reservation_id,
            reason=reason,
        )
        return json.dumps(released.model_dump(), default=str, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
def list_reservations(
    status: str | None = None,
    agent_id: str | None = None,
    active_only: bool = False,
) -> str:
    """List spend reservations.

    Args:
        status: Filter by status (active, settled, released, expired)
        agent_id: Filter by agent
        active_only: Only show reservations that count against budget
    """
    from agent_budget.models import ReservationStatus
    svc = get_service()
    status_enum = ReservationStatus(status) if status else None
    reservations = svc.list_reservations(
        status=status_enum, agent_id=agent_id, active_only=active_only,
    )
    return json.dumps(
        [r.model_dump() for r in reservations], default=str, indent=2,
    )


# --- v0.10.0: Spend Anomaly Detection Tools ---

@mcp.tool()
def create_anomaly_rule(
    name: str,
    anomaly_type: str,
    method: str = "zscore",
    threshold: float = 3.0,
    baseline_window_hours: int = 24,
    min_samples: int = 5,
    scope: str = "global",
    scope_id: str | None = None,
    action: str = "log",
    cooldown_minutes: int = 30,
) -> str:
    """Create a spend anomaly detection rule.

    Anomaly detection monitors LLM usage patterns and flags unusual spending
    that guardrails (which check absolute limits) would miss — e.g., a sudden
    cost spike, a new agent, a burst of calls, or cost drift over time.

    Args:
        name: Human-readable rule name (e.g., 'Global cost spike detector')
        anomaly_type: Type of anomaly (spike, sustained_drift, rate_burst,
                      cost_per_call, new_agent, new_model, after_hours)
        method: Detection method (zscore, multiplier, absolute, rate)
        threshold: Stddevs (zscore), × multiplier, absolute USD, or calls/min
        baseline_window_hours: Hours of history for baseline computation
        min_samples: Minimum historical data points before activation
        scope: global, agent, model, budget, task
        scope_id: ID for scoped rules (agent_id, model_id, etc.)
        action: What to do on detection (log, notify, throttle, block, kill_switch)
        cooldown_minutes: Minutes between re-firing same rule
    """
    from agent_budget.models import AnomalyType, AnomalyAction
    svc = get_service()
    rule = svc.create_anomaly_rule(
        name=name,
        anomaly_type=AnomalyType(anomaly_type),
        method=method,
        threshold=threshold,
        baseline_window_hours=baseline_window_hours,
        min_samples=min_samples,
        scope=GuardrailScope(scope),
        scope_id=scope_id,
        action=AnomalyAction(action),
        cooldown_minutes=cooldown_minutes,
    )
    return json.dumps(rule.model_dump(), default=str, indent=2)


@mcp.tool()
def list_anomaly_rules(enabled_only: bool = False) -> str:
    """List all anomaly detection rules.

    Args:
        enabled_only: Only show enabled rules
    """
    svc = get_service()
    rules = svc.list_anomaly_rules(enabled_only=enabled_only)
    return json.dumps([r.model_dump() for r in rules], default=str, indent=2)


@mcp.tool()
def detect_anomalies(
    agent_id: str | None = None,
    model_id: str | None = None,
    cost_usd: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> str:
    """Run anomaly detection against all enabled rules.

    Optionally pass context about a just-occurred call to enable per-call
    anomaly types (cost_per_call, new_agent, new_model).

    Args:
        agent_id: Agent that just made a call (for per-call detection)
        model_id: Model that was called
        cost_usd: Cost of the just-completed call
        input_tokens: Prompt tokens used
        output_tokens: Completion tokens used
    """
    svc = get_service()
    check_record = {}
    if agent_id is not None:
        check_record["agent_id"] = agent_id
    if model_id is not None:
        check_record["model_id"] = model_id
    if cost_usd is not None:
        check_record["cost_usd"] = cost_usd
    if input_tokens is not None:
        check_record["input_tokens"] = input_tokens
    if output_tokens is not None:
        check_record["output_tokens"] = output_tokens
    events = svc.detect_anomalies(check_record=check_record or None)
    return json.dumps(
        [e.model_dump() for e in events], default=str, indent=2,
    )


@mcp.tool()
def list_anomaly_events(
    acknowledged: bool | None = None,
    resolved: bool | None = None,
    limit: int = 50,
) -> str:
    """List detected anomaly events.

    Args:
        acknowledged: Filter by acknowledged status
        resolved: Filter by resolved status
        limit: Maximum events to return
    """
    svc = get_service()
    events = svc.list_anomaly_events(
        acknowledged=acknowledged, resolved=resolved, limit=limit,
    )
    return json.dumps([e.model_dump() for e in events], default=str, indent=2)


@mcp.tool()
def acknowledge_anomaly(event_id: str, acknowledged_by: str | None = None) -> str:
    """Acknowledge an anomaly event.

    Args:
        event_id: ID of the anomaly event
        acknowledged_by: Who acknowledged it
    """
    svc = get_service()
    event = svc.acknowledge_anomaly_event(event_id, acknowledged_by)
    if not event:
        return json.dumps({"error": f"Anomaly event {event_id} not found"}, indent=2)
    return json.dumps(event.model_dump(), default=str, indent=2)


@mcp.tool()
def resolve_anomaly(event_id: str) -> str:
    """Mark an anomaly event as resolved.

    Args:
        event_id: ID of the anomaly event
    """
    svc = get_service()
    event = svc.resolve_anomaly_event(event_id)
    if not event:
        return json.dumps({"error": f"Anomaly event {event_id} not found"}, indent=2)
    return json.dumps(event.model_dump(), default=str, indent=2)


@mcp.tool()
def get_anomaly_summary() -> str:
    """Get a summary of anomaly detection status — rules, events, severity breakdown."""
    svc = get_service()
    summary = svc.get_anomaly_summary()
    return json.dumps(summary.model_dump(), default=str, indent=2)


@mcp.tool()
def delete_anomaly_rule(rule_id: str) -> str:
    """Delete an anomaly detection rule.

    Args:
        rule_id: ID of the rule to delete
    """
    svc = get_service()
    deleted = svc.delete_anomaly_rule(rule_id)
    return json.dumps({"deleted": deleted, "rule_id": rule_id}, indent=2)


# ── Model Cost Optimizer tools (v0.11.0) ──────────────────────────────

_optimizer: Optional[ModelOptimizer] = None


def get_optimizer() -> ModelOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = ModelOptimizer()
    return _optimizer


@mcp.tool()
def compare_model_costs(
    current_model: str,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    min_tier: str | None = None,
    provider: str | None = None,
) -> str:
    """Compare the cost of *current_model* against all alternatives.

    Returns a ranked list of alternatives sorted by savings (highest first).
    Each entry includes cost per call, savings, and savings percentage.

    Args:
        current_model: Model ID to compare against (e.g. 'gpt-4o')
        input_tokens: Typical input/prompt tokens per call
        output_tokens: Typical output/completion tokens per call
        min_tier: Minimum capability tier to consider ('economy', 'medium', 'high')
        provider: Filter alternatives by provider ('openai', 'anthropic', etc.)
    """
    opt = get_optimizer()
    prov = None
    if provider:
        try:
            prov = ModelProvider(provider)
        except ValueError:
            pass
    results = opt.compare_models(
        current_model, input_tokens, output_tokens,
        min_tier=min_tier, provider=prov,
    )
    return json.dumps({
        "current_model": current_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "comparisons": [
            {
                "model": r.alternative_model,
                "cost_per_call": r.alternative_cost_per_call,
                "savings_per_call": r.savings_per_call,
                "savings_percent": r.savings_percent,
                "tier": capability_tier(r.alternative_model),
            }
            for r in results[:10]
        ],
    }, indent=2)


@mcp.tool()
def recommend_cheaper_model(
    current_model: str,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    monthly_calls: int = 0,
    min_tier: str | None = None,
) -> str:
    """Recommend the cheapest alternative model for a usage profile.

    Returns the single best alternative with rationale, projected monthly
    savings, and up to 5 runner-up alternatives.  Returns 'no recommendation'
    if the current model is already the cheapest.

    Args:
        current_model: Current model ID (e.g. 'gpt-4o')
        input_tokens: Typical input tokens per call
        output_tokens: Typical output tokens per call
        monthly_calls: Estimated calls per month (for savings projection)
        min_tier: Minimum capability tier ('economy', 'medium', 'high')
    """
    opt = get_optimizer()
    rec = opt.recommend(
        current_model, input_tokens, output_tokens,
        monthly_calls=monthly_calls, min_tier=min_tier,
    )
    if rec is None:
        return json.dumps({
            "recommendation": None,
            "message": f"{current_model} is already the cheapest option"
                       + (f" at tier >= {min_tier}" if min_tier else ""),
        }, indent=2)
    return json.dumps({
        "current_model": rec.current_model,
        "recommended_model": rec.recommended_model,
        "current_tier": rec.current_tier,
        "recommended_tier": rec.recommended_tier,
        "savings_per_call": rec.savings_per_call,
        "savings_percent": rec.savings_percent,
        "projected_monthly_savings": rec.projected_monthly_savings,
        "monthly_calls": rec.monthly_calls,
        "rationale": rec.rationale,
        "top_alternatives": [
            {
                "model": a.alternative_model,
                "savings_per_call": a.savings_per_call,
                "savings_percent": a.savings_percent,
            }
            for a in rec.alternatives
        ],
    }, indent=2)


@mcp.tool()
def project_model_switch(
    from_model: str,
    to_model: str,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    monthly_calls: int = 1000,
) -> str:
    """Project monthly cost savings from switching from one model to another.

    Args:
        from_model: Current model ID
        to_model: Target model ID
        input_tokens: Typical input tokens per call
        output_tokens: Typical output tokens per call
        monthly_calls: Estimated calls per month
    """
    opt = get_optimizer()
    result = opt.project_switch(
        from_model, to_model, input_tokens, output_tokens, monthly_calls,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def find_cheapest_model(
    min_tier: str = "economy",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    provider: str | None = None,
) -> str:
    """Find the cheapest model at or above a capability tier.

    Useful for selecting a default model that balances cost and quality.

    Args:
        min_tier: Minimum capability tier ('economy', 'medium', 'high')
        input_tokens: Typical input tokens per call
        output_tokens: Typical output tokens per call
        provider: Optional provider filter ('openai', 'anthropic', etc.)
    """
    opt = get_optimizer()
    prov = None
    if provider:
        try:
            prov = ModelProvider(provider)
        except ValueError:
            pass
    result = opt.cheapest_for_tier(min_tier, input_tokens, output_tokens, prov)
    if result is None:
        return json.dumps({
            "cheapest_model": None,
            "message": "No models found matching criteria",
        }, indent=2)
    return json.dumps({
        "cheapest_model": result.model_id,
        "provider": result.provider,
        "tier": result.tier,
        "input_price_per_mtok": result.input_price_per_mtok,
        "output_price_per_mtok": result.output_price_per_mtok,
        "cost_per_call": result.cost_per_call,
    }, indent=2)


# --- v0.12.0: Session Cost Importer ---

@mcp.tool()
def discover_session_sources() -> str:
    """Auto-discover AI agent session data sources on this machine.

    Finds Hermes state.db databases, request dump directories, and JSONL
    transcript files that can be imported for cost tracking.
    """
    svc = get_service()
    sources = svc.discover_session_sources()
    return json.dumps({
        "sources_found": len(sources),
        "sources": sources,
    }, indent=2)


@mcp.tool()
def import_hermes_sessions(
    db_path: str,
    agent_id: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    sync_to_budget: bool = False,
    budget_id: str | None = None,
) -> str:
    """Import session cost data from a Hermes state.db SQLite database.

    Reads token usage and cost data directly from the Hermes agent's session
    database. This bridges observability (seeing what agents spent) with
    enforcement (budget tracking, guardrails, loop detection).

    Args:
        db_path: Path to the Hermes state.db file
        agent_id: Override agent_id label for all imported records
        since: ISO date string; only import sessions after this date
        dry_run: If True, count without persisting records
        sync_to_budget: If True, also create expenses linked to budget_id
        budget_id: Budget to sync expenses to
    """
    svc = get_service()
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            pass
    result = svc.import_hermes_sessions(
        db_path=db_path,
        agent_id=agent_id,
        since=since_dt,
        dry_run=dry_run,
        sync_to_budget=sync_to_budget,
        budget_id=budget_id,
    )
    return json.dumps(result.to_dict(), indent=2)


@mcp.tool()
def import_jsonl_transcript(
    file_path: str,
    agent_id: str | None = None,
    dry_run: bool = False,
    sync_to_budget: bool = False,
    budget_id: str | None = None,
) -> str:
    """Import session cost data from a JSONL transcript file.

    Parses OpenAI/Anthropic-style JSONL transcripts where each line contains
    model, usage (input_tokens, output_tokens), and optional timestamp fields.

    Args:
        file_path: Path to the JSONL transcript file
        agent_id: Override agent_id label for all imported records
        dry_run: If True, count without persisting records
        sync_to_budget: If True, also create expenses linked to budget_id
        budget_id: Budget to sync expenses to
    """
    svc = get_service()
    result = svc.import_jsonl_transcript(
        file_path=file_path,
        agent_id=agent_id,
        dry_run=dry_run,
        sync_to_budget=sync_to_budget,
        budget_id=budget_id,
    )
    return json.dumps(result.to_dict(), indent=2)


@mcp.tool()
def import_request_dumps(
    dir_path: str,
    agent_id: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
    sync_to_budget: bool = False,
    budget_id: str | None = None,
) -> str:
    """Import cost data from Hermes API request dump JSON files.

    Reads all JSON files from a dump directory. Extracts model, token counts
    from request bodies, and estimates costs when usage data is missing.

    Args:
        dir_path: Directory containing request dump JSON files
        agent_id: Override agent_id label for all imported records
        since: ISO date string; only import dumps after this date
        dry_run: If True, count without persisting records
        sync_to_budget: If True, also create expenses linked to budget_id
        budget_id: Budget to sync expenses to
    """
    svc = get_service()
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            pass
    result = svc.import_request_dumps(
        dir_path=dir_path,
        agent_id=agent_id,
        since=since_dt,
        dry_run=dry_run,
        sync_to_budget=sync_to_budget,
        budget_id=budget_id,
    )
    return json.dumps(result.to_dict(), indent=2)


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
