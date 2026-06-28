"""MCP server for Agent Budget."""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .models import (
    BudgetPeriod, RecurringFrequency, SpendingRuleAction,
    SavingsGoalStatus, SUPPORTED_CURRENCIES,
)
from .service import BudgetService
from .store import BudgetStore

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


def run_server():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    run_server()
