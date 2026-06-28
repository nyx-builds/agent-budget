"""MCP server for agent-budget — exposes budget management tools over the Model Context Protocol."""

from __future__ import annotations

import json
from datetime import datetime, date
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .models import BudgetPeriod, BudgetStatus, CostCategory, AlertSeverity, AlertAction
from .engine import BudgetEngine
from .store import BudgetStore


mcp = FastMCP("agent-budget")
engine = BudgetEngine()


# ── Budget Management Tools ───────────────────────────────────────────────

@mcp.tool()
def budget_create(
    name: str,
    limit: float,
    period: str = "monthly",
    category: str = "misc",
    currency: str = "USD",
    description: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    parent_budget_id: str | None = None,
    tags: str | None = None,
) -> str:
    """Create a new budget. Period options: daily, weekly, monthly, quarterly, yearly, one_time.
    Category options: compute, api_calls, storage, network, licensing, labor, infrastructure, misc.
    Dates in YYYY-MM-DD format. Tags as comma-separated string."""
    try:
        budget = engine.create_budget(
            name=name,
            limit=limit,
            period=BudgetPeriod(period),
            category=CostCategory(category),
            currency=currency,
            description=description,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            parent_budget_id=parent_budget_id,
            tags=[t.strip() for t in tags.split(",")] if tags else [],
        )
        return json.dumps({
            "id": budget.id,
            "name": budget.name,
            "limit": budget.limit,
            "currency": budget.currency,
            "period": budget.period.value,
            "status": budget.status.value,
            "alert_rules": len(budget.alert_rules),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def budget_get(budget_id: str) -> str:
    """Get full details of a budget by ID."""
    budget = engine.get_budget(budget_id)
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({
        "id": budget.id,
        "name": budget.name,
        "description": budget.description,
        "category": budget.category.value,
        "limit": budget.limit,
        "spent": budget.spent,
        "remaining": budget.remaining,
        "utilization_pct": budget.utilization_pct,
        "currency": budget.currency,
        "period": budget.period.value,
        "status": budget.status.value,
        "start_date": budget.start_date.isoformat(),
        "period_end": budget.period_end.isoformat() if budget.period_end else None,
        "parent_budget_id": budget.parent_budget_id,
        "tags": budget.tags,
        "alert_rules": [{"id": r.id, "threshold_pct": r.threshold_pct, "severity": r.severity.value, "action": r.action.value} for r in budget.alert_rules],
        "created_at": budget.created_at.isoformat(),
    }, indent=2)


@mcp.tool()
def budget_list(
    status: str | None = None,
    category: str | None = None,
) -> str:
    """List budgets with optional filters. Status options: active, paused, exceeded, expired, closed.
    Category options: compute, api_calls, storage, network, licensing, labor, infrastructure, misc."""
    budgets = engine.list_budgets(
        status=BudgetStatus(status) if status else None,
        category=CostCategory(category) if category else None,
    )
    return json.dumps([{
        "id": b.id,
        "name": b.name,
        "limit": b.limit,
        "spent": b.spent,
        "remaining": b.remaining,
        "utilization_pct": b.utilization_pct,
        "status": b.status.value,
        "period": b.period.value,
        "currency": b.currency,
    } for b in budgets], indent=2)


@mcp.tool()
def budget_update(budget_id: str, **kwargs) -> str:
    """Update a budget. Provide budget_id and any fields to update (name, limit, description, status)."""
    # Map string status to enum
    if "status" in kwargs and isinstance(kwargs["status"], str):
        kwargs["status"] = BudgetStatus(kwargs["status"])
    budget = engine.update_budget(budget_id, **kwargs)
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({"id": budget.id, "name": budget.name, "status": budget.status.value, "spent": budget.spent, "limit": budget.limit})


@mcp.tool()
def budget_delete(budget_id: str) -> str:
    """Delete a budget and all its cost entries."""
    if engine.delete_budget(budget_id):
        return json.dumps({"deleted": budget_id})
    return json.dumps({"error": f"Budget '{budget_id}' not found"})


@mcp.tool()
def budget_pause(budget_id: str) -> str:
    """Pause a budget (stop tracking costs against it)."""
    budget = engine.pause_budget(budget_id)
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({"id": budget.id, "status": budget.status.value})


@mcp.tool()
def budget_resume(budget_id: str) -> str:
    """Resume a paused budget."""
    budget = engine.resume_budget(budget_id)
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({"id": budget.id, "status": budget.status.value})


@mcp.tool()
def budget_reset(budget_id: str) -> str:
    """Reset a budget for a new period (clears spent amount)."""
    budget = engine.reset_budget_period(budget_id)
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({"id": budget.id, "name": budget.name, "spent": budget.spent, "status": budget.status.value})


# ── Cost Tracking Tools ───────────────────────────────────────────────────

@mcp.tool()
def cost_record(
    budget_id: str,
    amount: float,
    category: str | None = None,
    source: str | None = None,
    description: str | None = None,
    currency: str = "USD",
    tags: str | None = None,
) -> str:
    """Record a cost against a budget. Returns the entry and any triggered alerts.
    Category options: compute, api_calls, storage, network, licensing, labor, infrastructure, misc.
    Tags as comma-separated string."""
    try:
        entry, alerts = engine.record_cost(
            budget_id=budget_id,
            amount=amount,
            category=CostCategory(category) if category else None,
            source=source,
            description=description,
            currency=currency,
            tags=[t.strip() for t in tags.split(",")] if tags else [],
        )
        result = {
            "entry": {
                "id": entry.id,
                "budget_id": entry.budget_id,
                "amount": entry.amount,
                "currency": entry.currency,
                "category": entry.category.value,
                "source": entry.source,
                "timestamp": entry.timestamp.isoformat(),
            },
            "budget_spent": engine.get_budget(budget_id).spent if engine.get_budget(budget_id) else None,
            "alerts_triggered": alerts,
        }
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def cost_list(
    budget_id: str,
    category: str | None = None,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """List cost entries for a budget with optional filters. Dates in YYYY-MM-DD format."""
    entries = engine.get_cost_entries(
        budget_id=budget_id,
        category=CostCategory(category) if category else None,
        source=source,
        start_date=datetime.fromisoformat(start_date) if start_date else None,
        end_date=datetime.fromisoformat(end_date) if end_date else None,
    )
    return json.dumps([{
        "id": e.id,
        "amount": e.amount,
        "currency": e.currency,
        "category": e.category.value,
        "source": e.source,
        "description": e.description,
        "timestamp": e.timestamp.isoformat(),
        "tags": e.tags,
    } for e in entries], indent=2)


@mcp.tool()
def cost_delete(budget_id: str, entry_id: str) -> str:
    """Delete a cost entry and adjust the budget's spent amount."""
    if engine.delete_cost_entry(budget_id, entry_id):
        return json.dumps({"deleted": entry_id, "budget_id": budget_id})
    return json.dumps({"error": f"Cost entry '{entry_id}' not found in budget '{budget_id}'"})


# ── Analytics Tools ────────────────────────────────────────────────────────

@mcp.tool()
def analyze_summary(budget_id: str | None = None) -> str:
    """Get budget summary. If budget_id provided, shows that budget's summary; otherwise shows all."""
    if budget_id:
        summary = engine.get_budget_summary(budget_id)
        if not summary:
            return json.dumps({"error": f"Budget '{budget_id}' not found"})
        return json.dumps(summary.model_dump(mode="json"), indent=2)
    else:
        summaries = engine.get_all_summaries()
        return json.dumps([s.model_dump(mode="json") for s in summaries], indent=2)


@mcp.tool()
def analyze_project(budget_id: str) -> str:
    """Project spending for the rest of the budget period based on current burn rate."""
    proj = engine.project_spending(budget_id)
    if not proj:
        return json.dumps({"error": f"Cannot project spending for budget '{budget_id}'"})
    return json.dumps(proj.model_dump(mode="json"), indent=2)


@mcp.tool()
def analyze_by_category(budget_id: str) -> str:
    """Get cost breakdown by category for a budget."""
    result = engine.get_costs_by_category(budget_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def analyze_by_source(budget_id: str) -> str:
    """Get cost breakdown by source for a budget."""
    result = engine.get_costs_by_source(budget_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def analyze_daily(budget_id: str, days: int = 30) -> str:
    """Get daily spending totals for the last N days."""
    result = engine.get_daily_spending(budget_id, days)
    return json.dumps(result, indent=2)


# ── Alert Tools ────────────────────────────────────────────────────────────

@mcp.tool()
def alert_add(
    budget_id: str,
    threshold_pct: float,
    severity: str = "warning",
    action: str = "notify",
    message: str | None = None,
    cooldown_minutes: int = 60,
) -> str:
    """Add an alert rule to a budget. Severity: info/warning/critical. Action: notify/throttle/halt.
    threshold_pct: 0-100, the percentage that triggers this alert."""
    budget = engine.add_alert_rule(
        budget_id=budget_id,
        threshold_pct=threshold_pct,
        severity=AlertSeverity(severity),
        action=AlertAction(action),
        message=message,
        cooldown_minutes=cooldown_minutes,
    )
    if not budget:
        return json.dumps({"error": f"Budget '{budget_id}' not found"})
    return json.dumps({"budget_id": budget.id, "alert_rules_count": len(budget.alert_rules)})


@mcp.tool()
def alert_check(budget_id: str | None = None) -> str:
    """Check alerts for a specific budget or all budgets. Returns triggered alerts."""
    if budget_id:
        alerts = engine.check_budget_alerts(budget_id)
    else:
        alerts = engine.check_all_alerts()
    return json.dumps(alerts, indent=2)


# ── Hierarchy Tools ────────────────────────────────────────────────────────

@mcp.tool()
def hierarchy_children(parent_budget_id: str) -> str:
    """List all child budgets of a parent budget."""
    children = engine.get_sub_budgets(parent_budget_id)
    return json.dumps([{
        "id": c.id, "name": c.name, "limit": c.limit,
        "spent": c.spent, "status": c.status.value,
    } for c in children], indent=2)


@mcp.tool()
def hierarchy_rollup(parent_budget_id: str) -> str:
    """Roll up spending across a parent budget and all its children."""
    result = engine.get_rollup_summary(parent_budget_id)
    if not result:
        return json.dumps({"error": f"Budget '{parent_budget_id}' not found"})
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
