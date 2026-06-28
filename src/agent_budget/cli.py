"""Click CLI for agent-budget."""

from __future__ import annotations

import json
from datetime import datetime, date
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from .models import BudgetPeriod, BudgetStatus, CostCategory, AlertSeverity, AlertAction
from .engine import BudgetEngine
from .store import BudgetStore


console = Console()
engine = BudgetEngine()


def _period_arg(value: str) -> BudgetPeriod:
    try:
        return BudgetPeriod(value)
    except ValueError:
        raise click.BadParameter(f"Invalid period. Choose from: {', '.join(p.value for p in BudgetPeriod)}")


def _category_arg(value: str) -> CostCategory:
    try:
        return CostCategory(value)
    except ValueError:
        raise click.BadParameter(f"Invalid category. Choose from: {', '.join(c.value for c in CostCategory)}")


def _status_arg(value: str) -> BudgetStatus:
    try:
        return BudgetStatus(value)
    except ValueError:
        raise click.BadParameter(f"Invalid status. Choose from: {', '.join(s.value for s in BudgetStatus)}")


# ── Budget Commands ────────────────────────────────────────────────────────

@click.group()
def cli():
    """Agent Budget — Budget management and cost tracking for autonomous agents."""
    pass


@cli.command("create")
@click.option("--name", "-n", required=True, help="Budget name")
@click.option("--limit", "-l", required=True, type=float, help="Budget limit")
@click.option("--period", "-p", default="monthly", help="Budget period (daily/weekly/monthly/quarterly/yearly/one_time)")
@click.option("--category", "-c", default="misc", help="Cost category")
@click.option("--currency", default="USD", help="Currency code (3 letters)")
@click.option("--description", "-d", default=None, help="Description")
@click.option("--start-date", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end-date", default=None, help="End date (YYYY-MM-DD)")
@click.option("--parent", default=None, help="Parent budget ID")
@click.option("--tags", default=None, help="Comma-separated tags")
def create_budget(name, limit, period, category, currency, description, start_date, end_date, parent, tags):
    """Create a new budget."""
    budget = engine.create_budget(
        name=name,
        limit=limit,
        period=_period_arg(period),
        category=_category_arg(category),
        currency=currency,
        description=description,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        parent_budget_id=parent,
        tags=[t.strip() for t in tags.split(",")] if tags else [],
    )
    console.print(Panel(f"[green]Budget created:[/green] {budget.name}", subtitle=f"ID: {budget.id}"))
    _print_budget_table([budget])


@cli.command("list")
@click.option("--status", "-s", default=None, help="Filter by status")
@click.option("--category", "-c", default=None, help="Filter by category")
def list_budgets(status, category):
    """List all budgets."""
    budgets = engine.list_budgets(
        status=_status_arg(status) if status else None,
        category=_category_arg(category) if category else None,
    )
    if not budgets:
        console.print("[yellow]No budgets found.[/yellow]")
        return
    _print_budget_table(budgets)


@cli.command("show")
@click.argument("budget_id")
def show_budget(budget_id):
    """Show budget details."""
    budget = engine.get_budget(budget_id)
    if not budget:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")
        return
    summary = engine.get_budget_summary(budget_id)
    _print_budget_detail(budget, summary)


@cli.command("update")
@click.argument("budget_id")
@click.option("--name", "-n", default=None, help="New name")
@click.option("--limit", "-l", default=None, type=float, help="New limit")
@click.option("--description", "-d", default=None, help="New description")
@click.option("--status", "-s", default=None, help="New status")
def update_budget(budget_id, name, limit, description, status):
    """Update a budget."""
    kwargs = {}
    if name:
        kwargs["name"] = name
    if limit is not None:
        kwargs["limit"] = limit
    if description is not None:
        kwargs["description"] = description
    if status:
        kwargs["status"] = _status_arg(status)
    if not kwargs:
        console.print("[yellow]Nothing to update.[/yellow]")
        return
    budget = engine.update_budget(budget_id, **kwargs)
    if not budget:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")
        return
    console.print(f"[green]Updated budget:[/green] {budget.name}")


@cli.command("delete")
@click.argument("budget_id")
@click.confirmation_option(prompt="Delete this budget and all its cost entries?")
def delete_budget(budget_id):
    """Delete a budget."""
    if engine.delete_budget(budget_id):
        console.print(f"[green]Budget '{budget_id}' deleted.[/green]")
    else:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")


@cli.command("pause")
@click.argument("budget_id")
def pause_budget(budget_id):
    """Pause a budget (stop tracking costs)."""
    budget = engine.pause_budget(budget_id)
    if budget:
        console.print(f"[yellow]Budget '{budget.name}' paused.[/yellow]")
    else:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")


@cli.command("resume")
@click.argument("budget_id")
def resume_budget(budget_id):
    """Resume a paused budget."""
    budget = engine.resume_budget(budget_id)
    if budget:
        console.print(f"[green]Budget '{budget.name}' resumed (status: {budget.status.value}).[/green]")
    else:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")


@cli.command("reset")
@click.argument("budget_id")
def reset_budget(budget_id):
    """Reset budget for a new period."""
    budget = engine.reset_budget_period(budget_id)
    if budget:
        console.print(f"[green]Budget '{budget.name}' reset for new period.[/green]")
    else:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")


# ── Cost Commands ──────────────────────────────────────────────────────────

@cli.group("cost")
def cost_group():
    """Manage cost entries."""
    pass


@cost_group.command("record")
@click.argument("budget_id")
@click.option("--amount", "-a", required=True, type=float, help="Cost amount")
@click.option("--category", "-c", default=None, help="Cost category")
@click.option("--source", "-s", default=None, help="Cost source (e.g., 'openai-gpt4')")
@click.option("--description", "-d", default=None, help="Description")
@click.option("--tags", default=None, help="Comma-separated tags")
def record_cost(budget_id, amount, category, source, description, tags):
    """Record a cost against a budget."""
    try:
        entry, alerts = engine.record_cost(
            budget_id=budget_id,
            amount=amount,
            category=_category_arg(category) if category else None,
            source=source,
            description=description,
            tags=[t.strip() for t in tags.split(",")] if tags else [],
        )
        console.print(f"[green]Cost recorded:[/green] {entry.amount} {entry.currency} → {entry.budget_id}")
        if alerts:
            for alert in alerts:
                color = {"info": "blue", "warning": "yellow", "critical": "red"}[alert["severity"]]
                console.print(f"  [{color}]ALERT ({alert['severity']}):[/{color}] {alert['message']} ({alert['current_pct']}% used)")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")


@cost_group.command("list")
@click.argument("budget_id")
@click.option("--category", "-c", default=None, help="Filter by category")
@click.option("--source", "-s", default=None, help="Filter by source")
@click.option("--start-date", default=None, help="Start date filter (YYYY-MM-DD)")
@click.option("--end-date", default=None, help="End date filter (YYYY-MM-DD)")
def list_costs(budget_id, category, source, start_date, end_date):
    """List cost entries for a budget."""
    entries = engine.get_cost_entries(
        budget_id=budget_id,
        category=_category_arg(category) if category else None,
        source=source,
        start_date=datetime.fromisoformat(start_date) if start_date else None,
        end_date=datetime.fromisoformat(end_date) if end_date else None,
    )
    if not entries:
        console.print("[yellow]No cost entries found.[/yellow]")
        return
    table = Table(title="Cost Entries", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Category", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Description")
    table.add_column("Timestamp", style="dim")
    for e in entries:
        table.add_row(e.id, f"{e.amount:.2f} {e.currency}", e.category.value, e.source or "-", e.description or "-", e.timestamp.strftime("%Y-%m-%d %H:%M"))
    console.print(table)


@cost_group.command("delete")
@click.argument("budget_id")
@click.argument("entry_id")
def delete_cost(budget_id, entry_id):
    """Delete a cost entry."""
    if engine.delete_cost_entry(budget_id, entry_id):
        console.print(f"[green]Cost entry '{entry_id}' deleted.[/green]")
    else:
        console.print(f"[red]Cost entry '{entry_id}' not found.[/red]")


# ── Analytics Commands ────────────────────────────────────────────────────

@cli.group("analyze")
def analyze_group():
    """Analyze budgets and spending."""
    pass


@analyze_group.command("summary")
@click.argument("budget_id", required=False)
def analyze_summary(budget_id):
    """Show budget summary. If no budget_id, show all."""
    if budget_id:
        summary = engine.get_budget_summary(budget_id)
        if not summary:
            console.print(f"[red]Budget '{budget_id}' not found.[/red]")
            return
        _print_summary_table([summary])
    else:
        summaries = engine.get_all_summaries()
        if not summaries:
            console.print("[yellow]No budgets found.[/yellow]")
            return
        _print_summary_table(summaries)


@analyze_group.command("project")
@click.argument("budget_id")
def analyze_project(budget_id):
    """Project spending for a budget."""
    proj = engine.project_spending(budget_id)
    if not proj:
        console.print(f"[red]Cannot project spending for budget '{budget_id}'.[/red]")
        return
    color = "green" if proj.on_track else "red"
    console.print(Panel(
        f"[bold]Projected Total:[/bold] {proj.projected_total:.2f} | "
        f"[bold]Burn Rate:[/bold] {proj.daily_burn_rate:.2f}/day | "
        f"[bold]On Track:[/bold] [{color}]{proj.on_track}[/{color}]",
        title=f"Spending Projection — {proj.budget_name}",
    ))


@analyze_group.command("by-category")
@click.argument("budget_id")
def analyze_by_category(budget_id):
    """Breakdown costs by category."""
    by_cat = engine.get_costs_by_category(budget_id)
    if not by_cat:
        console.print("[yellow]No cost entries found.[/yellow]")
        return
    table = Table(title="Costs by Category", box=box.ROUNDED)
    table.add_column("Category", style="cyan")
    table.add_column("Total", justify="right", style="green")
    for cat, total in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
        table.add_row(cat, f"{total:.2f}")
    console.print(table)


@analyze_group.command("by-source")
@click.argument("budget_id")
def analyze_by_source(budget_id):
    """Breakdown costs by source."""
    by_src = engine.get_costs_by_source(budget_id)
    if not by_src:
        console.print("[yellow]No cost entries found.[/yellow]")
        return
    table = Table(title="Costs by Source", box=box.ROUNDED)
    table.add_column("Source", style="magenta")
    table.add_column("Total", justify="right", style="green")
    for src, total in sorted(by_src.items(), key=lambda x: x[1], reverse=True):
        table.add_row(src, f"{total:.2f}")
    console.print(table)


@analyze_group.command("daily")
@click.argument("budget_id")
@click.option("--days", "-d", default=30, help="Number of days to show")
def analyze_daily(budget_id, days):
    """Show daily spending totals."""
    daily = engine.get_daily_spending(budget_id, days)
    if not daily:
        console.print("[yellow]No spending data found.[/yellow]")
        return
    table = Table(title=f"Daily Spending (Last {days} days)", box=box.ROUNDED)
    table.add_column("Date", style="cyan")
    table.add_column("Spent", justify="right", style="green")
    for d, total in sorted(daily.items()):
        table.add_row(d, f"{total:.2f}")
    console.print(table)


# ── Alert Commands ─────────────────────────────────────────────────────────

@cli.group("alert")
def alert_group():
    """Manage budget alerts."""
    pass


@alert_group.command("add")
@click.argument("budget_id")
@click.option("--threshold", "-t", required=True, type=float, help="Threshold percentage (0-100)")
@click.option("--severity", "-s", default="warning", help="Severity (info/warning/critical)")
@click.option("--action", "-a", default="notify", help="Action (notify/throttle/halt)")
@click.option("--message", "-m", default=None, help="Custom alert message")
def add_alert(budget_id, threshold, severity, action, message):
    """Add an alert rule to a budget."""
    budget = engine.add_alert_rule(
        budget_id=budget_id,
        threshold_pct=threshold,
        severity=AlertSeverity(severity),
        action=AlertAction(action),
        message=message,
    )
    if budget:
        console.print(f"[green]Alert rule added to budget '{budget.name}'[/green]")
    else:
        console.print(f"[red]Budget '{budget_id}' not found.[/red]")


@alert_group.command("check")
@click.argument("budget_id", required=False)
def check_alerts(budget_id):
    """Check alerts for a budget or all budgets."""
    if budget_id:
        alerts = engine.check_budget_alerts(budget_id)
    else:
        alerts = engine.check_all_alerts()
    if not alerts:
        console.print("[green]No alerts triggered.[/green]")
        return
    table = Table(title="Triggered Alerts", box=box.ROUNDED)
    table.add_column("Budget", style="cyan")
    table.add_column("Threshold", justify="right")
    table.add_column("Current %", justify="right")
    table.add_column("Severity", style="yellow")
    table.add_column("Action", style="magenta")
    table.add_column("Message")
    for a in alerts:
        table.add_row(
            a["budget_name"], f"{a['threshold_pct']}%", f"{a['current_pct']}%",
            a["severity"], a["action"], a["message"],
        )
    console.print(table)


# ── Hierarchy Commands ─────────────────────────────────────────────────────

@cli.group("hierarchy")
def hierarchy_group():
    """Manage budget hierarchies."""
    pass


@hierarchy_group.command("children")
@click.argument("parent_budget_id")
def list_children(parent_budget_id):
    """List child budgets of a parent."""
    children = engine.get_sub_budgets(parent_budget_id)
    if not children:
        console.print("[yellow]No child budgets found.[/yellow]")
        return
    _print_budget_table(children)


@hierarchy_group.command("rollup")
@click.argument("parent_budget_id")
def rollup(parent_budget_id):
    """Roll up spending across parent and children."""
    result = engine.get_rollup_summary(parent_budget_id)
    if not result:
        console.print(f"[red]Budget '{parent_budget_id}' not found.[/red]")
        return
    console.print(Panel(
        f"[bold]Total Limit:[/bold] {result['total_limit']:.2f} | "
        f"[bold]Total Spent:[/bold] {result['total_spent']:.2f} | "
        f"[bold]Remaining:[/bold] {result['total_remaining']:.2f} | "
        f"[bold]Utilization:[/bold] {result['utilization_pct']}% | "
        f"[bold]Children:[/bold] {result['child_count']}",
        title=f"Rollup — {result['parent_name']}",
    ))


# ── Display Helpers ────────────────────────────────────────────────────────

def _print_budget_table(budgets):
    table = Table(title="Budgets", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Limit", justify="right", style="green")
    table.add_column("Spent", justify="right", style="yellow")
    table.add_column("Remaining", justify="right", style="cyan")
    table.add_column("Used %", justify="right")
    table.add_column("Status")
    table.add_column("Period")
    for b in budgets:
        status_color = {
            "active": "green", "paused": "yellow", "exceeded": "red",
            "expired": "dim", "closed": "dim",
        }.get(b.status.value, "white")
        table.add_row(
            b.id, b.name, f"{b.limit:.2f} {b.currency}",
            f"{b.spent:.2f}", f"{b.remaining:.2f}",
            f"{b.utilization_pct}%", f"[{status_color}]{b.status.value}[/{status_color}]",
            b.period.value,
        )
    console.print(table)


def _print_budget_detail(budget, summary):
    _print_budget_table([budget])
    if budget.alert_rules:
        table = Table(title="Alert Rules", box=box.ROUNDED)
        table.add_column("ID", style="dim")
        table.add_column("Threshold", justify="right")
        table.add_column("Severity")
        table.add_column("Action")
        table.add_column("Message")
        for r in budget.alert_rules:
            table.add_row(r.id, f"{r.threshold_pct}%", r.severity.value, r.action.value, r.message or "-")
        console.print(table)


def _print_summary_table(summaries):
    table = Table(title="Budget Summaries", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Category")
    table.add_column("Limit", justify="right", style="green")
    table.add_column("Spent", justify="right", style="yellow")
    table.add_column("Remaining", justify="right", style="cyan")
    table.add_column("Used %", justify="right")
    table.add_column("Status")
    table.add_column("Alerts", justify="right")
    for s in summaries:
        status_color = {
            "active": "green", "paused": "yellow", "exceeded": "red",
            "expired": "dim", "closed": "dim",
        }.get(s.status, "white")
        table.add_row(
            s.budget_id, s.name, s.category,
            f"{s.limit:.2f} {s.currency}", f"{s.spent:.2f}", f"{s.remaining:.2f}",
            f"{s.utilization_pct}%", f"[{status_color}]{s.status}[/{status_color}]",
            str(s.active_alerts),
        )
    console.print(table)


if __name__ == "__main__":
    cli()
