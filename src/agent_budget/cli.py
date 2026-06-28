"""Click CLI for Agent Budget."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn

from .models import (
    BudgetPeriod, RecurringFrequency, AlertLevel, AlertThreshold,
    SpendingRuleAction, SavingsGoalStatus,
    SUPPORTED_CURRENCIES, format_currency,
)
from .service import BudgetService
from .store import BudgetStore

console = Console()


def get_service() -> BudgetService:
    return BudgetService(BudgetStore())


# --- Budget Commands ---

@click.group()
def main():
    """Agent Budget — Budget tracking & expense management for autonomous agents."""
    pass


@main.group("budget")
def budget_group():
    """Manage budgets."""
    pass


@budget_group.command("create")
@click.argument("name")
@click.option("--limit", required=True, type=float, help="Spending limit for the period")
@click.option("--period", required=True, type=click.Choice([p.value for p in BudgetPeriod]), help="Budget period")
@click.option("--category", default=None, help="Category this budget applies to")
@click.option("--currency", default="USD", help="Currency code")
@click.option("--rollover/--no-rollover", default=False, help="Enable budget rollover")
@click.option("--rollover-cap", default=None, type=float, help="Max amount that can roll over")
def budget_create(name: str, limit: float, period: str, category: Optional[str], currency: str, rollover: bool, rollover_cap: Optional[float]):
    """Create a new budget."""
    svc = get_service()
    budget = svc.create_budget(
        name=name,
        limit=limit,
        period=BudgetPeriod(period),
        category=category,
        currency=currency,
        rollover_enabled=rollover,
        rollover_cap=rollover_cap,
    )
    ro_text = ""
    if rollover:
        ro_text = f"\n  Rollover: [green]Enabled[/green]" + (f" (cap: {format_currency(rollover_cap, currency)})" if rollover_cap else "")
    console.print(Panel(
        f"[green]✓ Budget created:[/green] {budget.id}\n"
        f"  Name: {budget.name}\n"
        f"  Limit: {format_currency(budget.limit, budget.currency)}\n"
        f"  Period: {budget.period.value}\n"
        f"  Category: {budget.category or 'All'}{ro_text}",
        title="Budget Created",
    ))


@budget_group.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show inactive budgets too")
def budget_list(show_all: bool):
    """List all budgets."""
    svc = get_service()
    budgets = svc.list_budgets(active_only=not show_all)
    if not budgets:
        console.print("[yellow]No budgets found.[/yellow]")
        return
    table = Table(title="Budgets")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Limit", justify="right")
    table.add_column("Rollover", justify="right")
    table.add_column("Period")
    table.add_column("Category")
    table.add_column("Currency")
    table.add_column("Status")
    for b in budgets:
        status = "[green]Active[/green]" if b.active else "[dim]Inactive[/dim]"
        ro = f"+{format_currency(b.current_rollover, b.currency)}" if b.rollover_enabled and b.current_rollover > 0 else ("✓" if b.rollover_enabled else "-")
        table.add_row(b.id, b.name, format_currency(b.limit, b.currency), ro, b.period.value, b.category or "-", b.currency, status)
    console.print(table)


@budget_group.command("status")
@click.option("--budget-id", default=None, help="Specific budget ID")
def budget_status(budget_id: Optional[str]):
    """Check budget status (actual vs. budgeted)."""
    svc = get_service()
    if budget_id:
        comparisons = [svc.get_budget_status(budget_id)]
    else:
        comparisons = svc.get_all_budget_status()
    if not comparisons:
        console.print("[yellow]No active budgets.[/yellow]")
        return
    table = Table(title="Budget Status")
    table.add_column("Budget", style="bold")
    table.add_column("Limit", justify="right")
    table.add_column("Rollover", justify="right")
    table.add_column("Effective", justify="right")
    table.add_column("Spent", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("% Used", justify="right")
    table.add_column("Status")
    for c in comparisons:
        if c.status == "critical":
            status = f"[red]{c.status.upper()}[/red]"
        elif c.status == "over":
            status = f"[yellow]{c.status}[/yellow]"
        elif c.status == "on_track":
            status = f"[blue]{c.status}[/blue]"
        else:
            status = f"[green]{c.status}[/green]"
        ro = format_currency(c.rollover_amount) if c.rollover_amount > 0 else "-"
        table.add_row(
            c.budget_name,
            format_currency(c.budget_limit),
            ro,
            format_currency(c.effective_limit),
            format_currency(c.actual_spent),
            format_currency(c.remaining),
            f"{c.percent_used}%",
            status,
        )
    console.print(table)


@budget_group.command("update")
@click.argument("budget_id")
@click.option("--name", default=None, help="New name")
@click.option("--limit", default=None, type=float, help="New limit")
@click.option("--period", default=None, type=click.Choice([p.value for p in BudgetPeriod]), help="New period")
@click.option("--category", default=None, help="New category")
@click.option("--active/--inactive", default=None, help="Activate/deactivate")
@click.option("--rollover/--no-rollover", default=None, help="Enable/disable rollover")
@click.option("--rollover-cap", default=None, type=float, help="Rollover cap")
def budget_update(budget_id: str, name: Optional[str], limit: Optional[float], period: Optional[str], category: Optional[str], active: Optional[bool], rollover: Optional[bool], rollover_cap: Optional[float]):
    """Update a budget."""
    svc = get_service()
    budget = svc.update_budget(
        budget_id=budget_id,
        name=name,
        limit=limit,
        period=BudgetPeriod(period) if period else None,
        category=category,
        active=active,
        rollover_enabled=rollover,
        rollover_cap=rollover_cap,
    )
    console.print(f"[green]✓ Budget {budget.id} updated.[/green]")


@budget_group.command("delete")
@click.argument("budget_id")
@click.confirmation_option(prompt="Delete this budget?")
def budget_delete(budget_id: str):
    """Delete a budget."""
    svc = get_service()
    if svc.delete_budget(budget_id):
        console.print(f"[green]✓ Budget {budget_id} deleted.[/green]")
    else:
        console.print(f"[red]Budget {budget_id} not found.[/red]")


@budget_group.command("rollover")
@click.option("--budget-id", default=None, help="Specific budget ID (default: all)")
def budget_rollover(budget_id: Optional[str]):
    """Process budget rollovers (carry unspent budget forward)."""
    svc = get_service()
    if budget_id:
        result = svc.process_budget_rollover(budget_id)
        if result:
            console.print(Panel(
                f"[green]✓ Rollover processed:[/green]\n"
                f"  Budget: {result.budget_id}\n"
                f"  From: {result.from_period_start} → {result.from_period_end}\n"
                f"  To: {result.to_period_start} → {result.to_period_end}\n"
                f"  Amount rolled over: {format_currency(result.unspent_amount)}",
                title="Budget Rollover",
            ))
        else:
            console.print("[yellow]No rollover processed (disabled or already processed).[/yellow]")
    else:
        results = svc.process_all_rollovers()
        if results:
            console.print(f"[green]✓ Processed {len(results)} rollover(s):[/green]")
            for r in results:
                console.print(f"  {r.budget_id}: +{format_currency(r.unspent_amount)} from previous period")
        else:
            console.print("[yellow]No rollovers to process.[/yellow]")


# --- Expense Commands ---

@main.group("expense")
def expense_group():
    """Manage expenses."""
    pass


@expense_group.command("add")
@click.argument("amount", type=float)
@click.option("--category", required=True, help="Expense category")
@click.option("--description", default="", help="Description")
@click.option("--date", "expense_date", default=None, help="Date (YYYY-MM-DD)")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--currency", default="USD", help="Currency code")
@click.option("--budget-id", default=None, help="Budget to count against")
@click.option("--vendor", default=None, help="Vendor or merchant name")
@click.option("--receipt-url", default=None, help="URL to receipt")
@click.option("--reimbursable", is_flag=True, help="Mark as reimbursable")
@click.option("--approved-by", default=None, help="Approver name")
def expense_add(amount: float, category: str, description: str, expense_date: Optional[str], tags: str, currency: str, budget_id: Optional[str], vendor: Optional[str], receipt_url: Optional[str], reimbursable: bool, approved_by: Optional[str]):
    """Log a new expense."""
    svc = get_service()
    parsed_date = date.fromisoformat(expense_date) if expense_date else None
    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    try:
        expense = svc.add_expense(
            amount=amount,
            category=category,
            description=description,
            expense_date=parsed_date,
            tags=parsed_tags,
            currency=currency,
            budget_id=budget_id,
            vendor=vendor,
            receipt_url=receipt_url,
            reimbursable=reimbursable,
            approved_by=approved_by,
        )
        extras = []
        if expense.vendor:
            extras.append(f"  Vendor: {expense.vendor}")
        if expense.reimbursable:
            extras.append("  [blue]Reimbursable: Yes[/blue]")
        if expense.approved_by:
            extras.append(f"  Approved by: {expense.approved_by}")
        extra_text = "\n".join(extras)
        if extra_text:
            extra_text = "\n" + extra_text
        console.print(Panel(
            f"[green]✓ Expense logged:[/green] {expense.id}\n"
            f"  Amount: {format_currency(expense.amount, expense.currency)}\n"
            f"  Category: {expense.category}\n"
            f"  Date: {expense.expense_date}\n"
            f"  Tags: {', '.join(expense.tags) or '-'}\n"
            f"  Budget: {expense.budget_id or 'None'}{extra_text}",
            title="Expense Added",
        ))
    except ValueError as e:
        console.print(f"[red]✗ Expense blocked: {e}[/red]")
        sys.exit(1)


@expense_group.command("update")
@click.argument("expense_id")
@click.option("--amount", default=None, type=float, help="New amount")
@click.option("--category", default=None, help="New category")
@click.option("--description", default=None, help="New description")
@click.option("--tags", default=None, help="New tags (comma-separated)")
@click.option("--status", default=None, type=click.Choice(["planned", "confirmed", "cancelled"]), help="New status")
@click.option("--vendor", default=None, help="Vendor name")
@click.option("--receipt-url", default=None, help="Receipt URL")
@click.option("--reimbursable/--not-reimbursable", default=None, help="Mark as reimbursable")
@click.option("--approved-by", default=None, help="Approver name")
def expense_update(expense_id: str, amount: Optional[float], category: Optional[str], description: Optional[str], tags: Optional[str], status: Optional[str], vendor: Optional[str], receipt_url: Optional[str], reimbursable: Optional[bool], approved_by: Optional[str]):
    """Update an existing expense."""
    svc = get_service()
    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags is not None else None
    try:
        expense = svc.update_expense(
            expense_id=expense_id,
            amount=amount,
            category=category,
            description=description,
            tags=parsed_tags,
            status=status,
            vendor=vendor,
            receipt_url=receipt_url,
            reimbursable=reimbursable,
            approved_by=approved_by,
        )
        console.print(f"[green]✓ Expense {expense.id} updated.[/green]")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)


@expense_group.command("list")
@click.option("--category", default=None, help="Filter by category")
@click.option("--budget-id", default=None, help="Filter by budget")
@click.option("--start-date", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end-date", default=None, help="End date (YYYY-MM-DD)")
@click.option("--tags", default="", help="Filter by tags (comma-separated)")
@click.option("--this-week", is_flag=True, help="Show this week's expenses")
@click.option("--this-month", is_flag=True, help="Show this month's expenses")
@click.option("--vendor", default=None, help="Filter by vendor")
@click.option("--reimbursable", is_flag=True, help="Show only reimbursable expenses")
def expense_list(category: Optional[str], budget_id: Optional[str], start_date: Optional[str], end_date: Optional[str], tags: str, this_week: bool, this_month: bool, vendor: Optional[str], reimbursable: bool):
    """List expenses."""
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    today = date.today()
    if this_week:
        parsed_start = today - timedelta(days=today.weekday())
        parsed_end = today
    elif this_month:
        parsed_start = today.replace(day=1)
        parsed_end = today

    expenses = svc.list_expenses(
        category=category,
        budget_id=budget_id,
        start_date=parsed_start,
        end_date=parsed_end,
        tags=parsed_tags,
        vendor=vendor,
        reimbursable=reimbursable if reimbursable else None,
    )
    if not expenses:
        console.print("[yellow]No expenses found.[/yellow]")
        return
    table = Table(title="Expenses")
    table.add_column("ID", style="cyan")
    table.add_column("Date", style="bold")
    table.add_column("Category")
    table.add_column("Amount", justify="right")
    table.add_column("Vendor")
    table.add_column("Description")
    table.add_column("Tags")
    total = 0
    for e in expenses:
        table.add_row(
            e.id,
            str(e.expense_date),
            e.category,
            format_currency(e.amount, e.currency),
            e.vendor or "-",
            e.description[:40],
            ", ".join(e.tags) or "-",
        )
        total += e.amount
    console.print(table)
    console.print(f"\n[bold]Total: {format_currency(total)}[/bold] ({len(expenses)} expenses)")


@expense_group.command("delete")
@click.argument("expense_id")
@click.confirmation_option(prompt="Delete this expense?")
def expense_delete(expense_id: str):
    """Delete an expense."""
    svc = get_service()
    if svc.delete_expense(expense_id):
        console.print(f"[green]✓ Expense {expense_id} deleted.[/green]")
    else:
        console.print(f"[red]Expense {expense_id} not found.[/red]")


# --- Savings Commands ---

@main.group("savings")
def savings_group():
    """Manage savings goals."""
    pass


@savings_group.command("create")
@click.argument("name")
@click.option("--target", required=True, type=float, help="Target amount to save")
@click.option("--currency", default="USD", help="Currency code")
@click.option("--target-date", default=None, help="Target date (YYYY-MM-DD)")
@click.option("--category", default=None, help="Associated budget category")
@click.option("--description", default="", help="Goal description")
def savings_create(name: str, target: float, currency: str, target_date: Optional[str], category: Optional[str], description: str):
    """Create a savings goal."""
    svc = get_service()
    parsed_date = date.fromisoformat(target_date) if target_date else None
    goal = svc.create_savings_goal(
        name=name,
        target_amount=target,
        currency=currency,
        target_date=parsed_date,
        category=category,
        description=description,
    )
    console.print(Panel(
        f"[green]✓ Savings goal created:[/green] {goal.id}\n"
        f"  Name: {goal.name}\n"
        f"  Target: {format_currency(goal.target_amount, goal.currency)}\n"
        f"  Target Date: {goal.target_date or 'No deadline'}\n"
        f"  Category: {goal.category or '-'}",
        title="Savings Goal Created",
    ))


@savings_group.command("list")
@click.option("--status", default=None, type=click.Choice(["active", "completed", "paused"]), help="Filter by status")
def savings_list(status: Optional[str]):
    """List savings goals."""
    svc = get_service()
    goals = svc.list_savings_goals(status=status)
    if not goals:
        console.print("[yellow]No savings goals found.[/yellow]")
        return
    table = Table(title="Savings Goals")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Progress", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Target Date")
    table.add_column("Status")
    for g in goals:
        progress = f"{g.progress_percent:.0f}%"
        status_color = {"active": "green", "completed": "bold green", "paused": "yellow"}.get(g.status.value, "white")
        table.add_row(
            g.id,
            g.name,
            progress,
            format_currency(g.current_amount, g.currency),
            format_currency(g.target_amount, g.currency),
            format_currency(g.remaining, g.currency),
            str(g.target_date) if g.target_date else "-",
            f"[{status_color}]{g.status.value}[/{status_color}]",
        )
    console.print(table)


@savings_group.command("contribute")
@click.argument("goal_id")
@click.option("--amount", required=True, type=float, help="Amount to contribute")
@click.option("--note", default="", help="Note about this contribution")
def savings_contribute(goal_id: str, amount: float, note: str):
    """Contribute to a savings goal."""
    svc = get_service()
    try:
        goal = svc.contribute_to_savings(goal_id, amount=amount, note=note)
        bar_filled = int(goal.progress_percent / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        console.print(Panel(
            f"[green]✓ Contribution added:[/green] +{format_currency(amount, goal.currency)}\n"
            f"  Goal: {goal.name}\n"
            f"  Progress: {bar} {goal.progress_percent:.0f}%\n"
            f"  Current: {format_currency(goal.current_amount, goal.currency)} / {format_currency(goal.target_amount, goal.currency)}\n"
            f"  Remaining: {format_currency(goal.remaining, goal.currency)}",
            title="Savings Contribution",
        ))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)


@savings_group.command("withdraw")
@click.argument("goal_id")
@click.option("--amount", required=True, type=float, help="Amount to withdraw")
@click.option("--note", default="", help="Note about this withdrawal")
def savings_withdraw(goal_id: str, amount: float, note: str):
    """Withdraw from a savings goal."""
    svc = get_service()
    try:
        goal = svc.withdraw_from_savings(goal_id, amount=amount, note=note)
        console.print(f"[yellow]↩ Withdrew {format_currency(amount, goal.currency)} from {goal.name}.[/yellow]")
        console.print(f"  Remaining in goal: {format_currency(goal.current_amount, goal.currency)}")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)


@savings_group.command("pause")
@click.argument("goal_id")
def savings_pause(goal_id: str):
    """Pause a savings goal."""
    svc = get_service()
    try:
        goal = svc.pause_savings_goal(goal_id)
        console.print(f"[yellow]⏸ Savings goal {goal.name} paused.[/yellow]")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")


@savings_group.command("resume")
@click.argument("goal_id")
def savings_resume(goal_id: str):
    """Resume a paused savings goal."""
    svc = get_service()
    try:
        goal = svc.resume_savings_goal(goal_id)
        console.print(f"[green]▶ Savings goal {goal.name} resumed.[/green]")
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")


@savings_group.command("delete")
@click.argument("goal_id")
@click.confirmation_option(prompt="Delete this savings goal?")
def savings_delete(goal_id: str):
    """Delete a savings goal."""
    svc = get_service()
    if svc.delete_savings_goal(goal_id):
        console.print(f"[green]✓ Savings goal {goal_id} deleted.[/green]")
    else:
        console.print(f"[red]Savings goal {goal_id} not found.[/red]")


# --- Spending Rules Commands ---

@main.group("rule")
def rule_group():
    """Manage spending rules."""
    pass


@rule_group.command("add")
@click.argument("name")
@click.option("--category", required=True, help="Category this rule applies to")
@click.option("--action", required=True, type=click.Choice([a.value for a in SpendingRuleAction]), help="Action when triggered")
@click.option("--threshold-amount", default=None, type=float, help="Max total spending amount")
@click.option("--threshold-percent", default=None, type=float, help="Max percent of budget")
@click.option("--budget-id", default=None, help="Associated budget ID")
@click.option("--approval-above", default=None, type=float, help="Single expenses above this need approval")
@click.option("--description", default="", help="Rule description")
def rule_add(name: str, category: str, action: str, threshold_amount: Optional[float], threshold_percent: Optional[float], budget_id: Optional[str], approval_above: Optional[float], description: str):
    """Add a spending rule."""
    svc = get_service()
    rule = svc.create_spending_rule(
        name=name,
        category=category,
        action=SpendingRuleAction(action),
        threshold_amount=threshold_amount,
        threshold_percent=threshold_percent,
        budget_id=budget_id,
        requires_approval_above=approval_above,
        description=description,
    )
    console.print(Panel(
        f"[green]✓ Spending rule created:[/green] {rule.id}\n"
        f"  Name: {rule.name}\n"
        f"  Category: {rule.category}\n"
        f"  Action: {rule.action.value}\n"
        f"  Threshold: {format_currency(rule.threshold_amount) if rule.threshold_amount else (f'{rule.threshold_percent}%' if rule.threshold_percent else 'N/A')}\n"
        f"  Approval above: {format_currency(rule.requires_approval_above) if rule.requires_approval_above else 'N/A'}",
        title="Spending Rule Created",
    ))


@rule_group.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show disabled rules too")
def rule_list(show_all: bool):
    """List spending rules."""
    svc = get_service()
    rules = svc.list_spending_rules(enabled_only=not show_all)
    if not rules:
        console.print("[yellow]No spending rules found.[/yellow]")
        return
    table = Table(title="Spending Rules")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Category")
    table.add_column("Action")
    table.add_column("Threshold")
    table.add_column("Approval Above")
    table.add_column("Status")
    for r in rules:
        thresh = format_currency(r.threshold_amount) if r.threshold_amount else (f"{r.threshold_percent}%" if r.threshold_percent else "-")
        approval = format_currency(r.requires_approval_above) if r.requires_approval_above else "-"
        status = "[green]Enabled[/green]" if r.enabled else "[dim]Disabled[/dim]"
        table.add_row(r.id, r.name, r.category, r.action.value, thresh, approval, status)
    console.print(table)


@rule_group.command("check")
@click.option("--amount", required=True, type=float, help="Expense amount to check")
@click.option("--category", required=True, help="Expense category to check")
def rule_check(amount: float, category: str):
    """Check if an expense would violate any spending rules."""
    svc = get_service()
    from .models import Expense
    test_expense = Expense(amount=amount, category=category)
    violations = svc.check_expense_rules(test_expense)
    if not violations:
        console.print("[green]✓ No spending rule violations.[/green]")
    else:
        console.print("[red]✗ Spending rule violations:[/red]")
        for v in violations:
            console.print(f"  • {v}")


@rule_group.command("delete")
@click.argument("rule_id")
@click.confirmation_option(prompt="Delete this spending rule?")
def rule_delete(rule_id: str):
    """Delete a spending rule."""
    svc = get_service()
    if svc.delete_spending_rule(rule_id):
        console.print(f"[green]✓ Spending rule {rule_id} deleted.[/green]")
    else:
        console.print(f"[red]Spending rule {rule_id} not found.[/red]")


# --- Recurring Commands ---

@main.group("recurring")
def recurring_group():
    """Manage recurring expenses."""
    pass


@recurring_group.command("add")
@click.argument("name")
@click.option("--amount", required=True, type=float, help="Amount per occurrence")
@click.option("--category", required=True, help="Expense category")
@click.option("--frequency", required=True, type=click.Choice([f.value for f in RecurringFrequency]), help="Frequency")
@click.option("--description", default="", help="Description")
@click.option("--currency", default="USD", help="Currency code")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--budget-id", default=None, help="Budget to count against")
@click.option("--start-date", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end-date", default=None, help="End date (YYYY-MM-DD)")
def recurring_add(name: str, amount: float, category: str, frequency: str, description: str, currency: str, tags: str, budget_id: Optional[str], start_date: Optional[str], end_date: Optional[str]):
    """Add a recurring expense."""
    svc = get_service()
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    parsed_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    recurring = svc.add_recurring_expense(
        name=name,
        amount=amount,
        category=category,
        frequency=RecurringFrequency(frequency),
        description=description,
        currency=currency,
        tags=parsed_tags,
        budget_id=budget_id,
        start_date=parsed_start,
        end_date=parsed_end,
    )
    console.print(Panel(
        f"[green]✓ Recurring expense created:[/green] {recurring.id}\n"
        f"  Name: {recurring.name}\n"
        f"  Amount: {format_currency(recurring.amount, recurring.currency)}\n"
        f"  Frequency: {recurring.frequency.value}\n"
        f"  Category: {recurring.category}\n"
        f"  Next Due: {recurring.next_due}",
        title="Recurring Expense Added",
    ))


@recurring_group.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show paused recurring expenses")
def recurring_list(show_all: bool):
    """List recurring expenses."""
    svc = get_service()
    recurrings = svc.list_recurring_expenses(active_only=not show_all)
    if not recurrings:
        console.print("[yellow]No recurring expenses found.[/yellow]")
        return
    table = Table(title="Recurring Expenses")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Amount", justify="right")
    table.add_column("Frequency")
    table.add_column("Category")
    table.add_column("Next Due")
    table.add_column("Status")
    for r in recurrings:
        status = "[green]Active[/green]" if r.active else "[dim]Paused[/dim]"
        table.add_row(r.id, r.name, format_currency(r.amount, r.currency), r.frequency.value, r.category, str(r.next_due), status)
    console.print(table)


@recurring_group.command("process")
def recurring_process():
    """Generate expenses for all due recurring templates."""
    svc = get_service()
    generated = svc.process_recurring_expenses()
    if not generated:
        console.print("[yellow]No recurring expenses due.[/yellow]")
        return
    console.print(f"[green]✓ Generated {len(generated)} expense(s) from recurring templates:[/green]")
    for e in generated:
        console.print(f"  {e.id}: {format_currency(e.amount, e.currency)} — {e.description}")


@recurring_group.command("pause")
@click.argument("recurring_id")
def recurring_pause(recurring_id: str):
    """Pause a recurring expense."""
    svc = get_service()
    recurring = svc.pause_recurring(recurring_id)
    console.print(f"[yellow]⏸ Recurring expense {recurring.id} paused.[/yellow]")


@recurring_group.command("resume")
@click.argument("recurring_id")
def recurring_resume(recurring_id: str):
    """Resume a paused recurring expense."""
    svc = get_service()
    recurring = svc.resume_recurring(recurring_id)
    console.print(f"[green]▶ Recurring expense {recurring.id} resumed.[/green]")


@recurring_group.command("delete")
@click.argument("recurring_id")
@click.confirmation_option(prompt="Delete this recurring expense?")
def recurring_delete(recurring_id: str):
    """Delete a recurring expense."""
    svc = get_service()
    if svc.delete_recurring_expense(recurring_id):
        console.print(f"[green]✓ Recurring expense {recurring_id} deleted.[/green]")
    else:
        console.print(f"[red]Recurring expense {recurring_id} not found.[/red]")


# --- Analysis Commands ---

@main.command("compare")
@click.option("--budget-id", default=None, help="Specific budget to compare")
def compare_budget(budget_id: Optional[str]):
    """Compare budget vs. actual spending."""
    svc = get_service()
    if budget_id:
        comparisons = [svc.get_budget_status(budget_id)]
    else:
        comparisons = svc.get_all_budget_status()
    if not comparisons:
        console.print("[yellow]No active budgets to compare.[/yellow]")
        return
    table = Table(title="Budget vs. Actual")
    table.add_column("Budget", style="bold")
    table.add_column("Category")
    table.add_column("Limit", justify="right")
    table.add_column("Rollover", justify="right")
    table.add_column("Spent", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("% Used", justify="right")
    table.add_column("Status")
    for c in comparisons:
        status_color = {"critical": "red", "over": "yellow", "on_track": "blue", "under": "green"}.get(c.status, "white")
        ro = f"+{format_currency(c.rollover_amount)}" if c.rollover_amount > 0 else "-"
        table.add_row(
            c.budget_name,
            c.category or "-",
            format_currency(c.budget_limit),
            ro,
            format_currency(c.actual_spent),
            format_currency(c.remaining),
            f"{c.percent_used}%",
            f"[{status_color}]{c.status}[/{status_color}]",
        )
    console.print(table)


@main.command("forecast")
@click.option("--months", default=3, type=int, help="Number of months to forecast")
@click.option("--category", default=None, help="Filter by category")
def forecast_cmd(months: int, category: Optional[str]):
    """Forecast future spending."""
    svc = get_service()
    forecasts = svc.get_spending_forecast(months=months, category=category)
    if not forecasts:
        console.print("[yellow]No data for forecasting.[/yellow]")
        return
    table = Table(title="Spending Forecast")
    table.add_column("Period", style="bold")
    table.add_column("Category")
    table.add_column("Projected", justify="right")
    table.add_column("Budget Limit", justify="right")
    table.add_column("Confidence", justify="right")
    for f in forecasts:
        over = f.budget_limit and f.projected_spending > f.budget_limit
        proj = f"[red]{format_currency(f.projected_spending)}[/red]" if over else format_currency(f.projected_spending)
        table.add_row(
            f.period,
            f.category or "-",
            proj,
            format_currency(f.budget_limit) if f.budget_limit else "-",
            f"{f.confidence:.0%}",
        )
    console.print(table)


@main.command("alerts")
@click.option("--budget-id", default=None, help="Filter by budget")
def alerts_cmd(budget_id: Optional[str]):
    """Check budget alerts."""
    svc = get_service()
    alerts = svc.get_alerts(budget_id=budget_id)
    if not alerts:
        console.print("[green]No alerts.[/green]")
        return
    for a in alerts:
        level_style = {"info": "blue", "warning": "yellow", "critical": "bold red"}.get(a.level.value, "white")
        console.print(f"[{level_style}]● {a.level.value.upper()}[/{level_style}] {a.message}")


@main.command("summary")
@click.option("--this-week", is_flag=True, help="This week's summary")
@click.option("--this-month", is_flag=True, help="This month's summary")
def summary_cmd(this_week: bool, this_month: bool):
    """Get spending summary by category."""
    svc = get_service()
    today = date.today()
    start = None
    end = None
    if this_week:
        start = today - timedelta(days=today.weekday())
        end = today
    elif this_month:
        start = today.replace(day=1)
        end = today

    category_summary = svc.get_category_summary(start_date=start, end_date=end)
    if not category_summary:
        console.print("[yellow]No spending data.[/yellow]")
        return
    table = Table(title="Spending Summary" + (f" ({start} to {end})" if start else ""))
    table.add_column("Category", style="bold")
    table.add_column("Total Spent", justify="right")
    for cat, total in category_summary.items():
        table.add_row(cat, format_currency(total))
    grand = sum(category_summary.values())
    console.print(table)
    console.print(f"\n[bold]Grand Total: {format_currency(grand)}[/bold]")


# --- Export Command ---

@main.command("export")
@click.option("--format", "fmt", type=click.Choice(["json", "csv", "markdown"]), default="json", help="Export format")
def export_cmd(fmt: str):
    """Export budget and expense data."""
    svc = get_service()
    output = svc.export_data(format=fmt)
    console.print(output)


# --- Utility Commands ---

@main.command("currencies")
def currencies_cmd():
    """List supported currencies."""
    table = Table(title="Supported Currencies")
    table.add_column("Code", style="bold")
    table.add_column("Name")
    table.add_column("Symbol")
    table.add_column("Decimals", justify="right")
    for c in SUPPORTED_CURRENCIES.values():
        table.add_row(c.code, c.name, c.symbol, str(c.decimal_places))
    console.print(table)


@main.command("serve")
def serve_cmd():
    """Start the MCP server."""
    from .mcp_server import run_server
    console.print("[bold green]Starting Agent Budget MCP server...[/bold green]")
    run_server()


if __name__ == "__main__":
    main()
