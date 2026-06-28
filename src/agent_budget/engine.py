"""Core business logic for budget management and cost tracking."""

from __future__ import annotations

from datetime import datetime, date, timedelta, timezone
from typing import Optional

from .models import (
    Budget, CostEntry, BudgetAlertRule, BudgetPeriod, BudgetStatus,
    CostCategory, AlertSeverity, AlertAction, BudgetSummary, SpendingProjection,
)
from .store import BudgetStore


class BudgetEngine:
    """High-level operations for budget management."""

    def __init__(self, store: Optional[BudgetStore] = None):
        self.store = store or BudgetStore()

    # ── Budget CRUD ────────────────────────────────────────────────────────

    def create_budget(
        self,
        name: str,
        limit: float,
        period: BudgetPeriod = BudgetPeriod.MONTHLY,
        category: CostCategory = CostCategory.MISC,
        currency: str = "USD",
        description: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        parent_budget_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        alert_rules: Optional[list[BudgetAlertRule]] = None,
    ) -> Budget:
        """Create a new budget."""
        budget = Budget(
            name=name,
            limit=limit,
            period=period,
            category=category,
            currency=currency,
            description=description,
            start_date=start_date or date.today(),
            end_date=end_date,
            parent_budget_id=parent_budget_id,
            tags=tags or [],
            alert_rules=alert_rules or [],
        )
        # Add default alert rules if none provided
        if not budget.alert_rules:
            budget.alert_rules = self._default_alert_rules()
        self.store.save_budget(budget)
        return budget

    def get_budget(self, budget_id: str) -> Optional[Budget]:
        return self.store.load_budget(budget_id)

    def update_budget(self, budget_id: str, **kwargs) -> Optional[Budget]:
        """Update budget fields. Returns updated budget or None if not found."""
        budget = self.store.load_budget(budget_id)
        if not budget:
            return None
        for key, value in kwargs.items():
            if hasattr(budget, key):
                setattr(budget, key, value)
        budget.updated_at = datetime.now(timezone.utc)
        self.store.save_budget(budget)
        return budget

    def delete_budget(self, budget_id: str) -> bool:
        return self.store.delete_budget(budget_id)

    def list_budgets(
        self,
        status: Optional[BudgetStatus] = None,
        category: Optional[CostCategory] = None,
    ) -> list[Budget]:
        budgets = self.store.list_budgets()
        if status:
            budgets = [b for b in budgets if b.status == status]
        if category:
            budgets = [b for b in budgets if b.category == category]
        return budgets

    def pause_budget(self, budget_id: str) -> Optional[Budget]:
        return self.update_budget(budget_id, status=BudgetStatus.PAUSED)

    def resume_budget(self, budget_id: str) -> Optional[Budget]:
        budget = self.get_budget(budget_id)
        if not budget:
            return None
        new_status = BudgetStatus.EXCEEDED if budget.is_exceeded else BudgetStatus.ACTIVE
        return self.update_budget(budget_id, status=new_status)

    def close_budget(self, budget_id: str) -> Optional[Budget]:
        return self.update_budget(budget_id, status=BudgetStatus.CLOSED)

    def reset_budget_period(self, budget_id: str) -> Optional[Budget]:
        """Reset a budget for a new period."""
        budget = self.get_budget(budget_id)
        if not budget:
            return None
        budget.reset_period()
        budget.status = BudgetStatus.ACTIVE
        self.store.save_budget(budget)
        return budget

    # ── Cost Tracking ──────────────────────────────────────────────────────

    def record_cost(
        self,
        budget_id: str,
        amount: float,
        category: Optional[CostCategory] = None,
        description: Optional[str] = None,
        source: Optional[str] = None,
        currency: str = "USD",
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> tuple[CostEntry, list[dict]]:
        """Record a cost against a budget. Returns (entry, triggered_alerts)."""
        budget = self.store.load_budget(budget_id)
        if not budget:
            raise ValueError(f"Budget '{budget_id}' not found")
        if budget.status == BudgetStatus.CLOSED:
            raise ValueError(f"Budget '{budget_id}' is closed")

        entry = CostEntry(
            budget_id=budget_id,
            amount=amount,
            currency=currency,
            category=category or budget.category,
            description=description,
            source=source,
            tags=tags or [],
            metadata=metadata or {},
        )

        # Update budget spent
        budget.spent += amount
        budget.updated_at = datetime.now(timezone.utc)

        # Check if exceeded
        if budget.is_exceeded and budget.status != BudgetStatus.PAUSED:
            budget.status = BudgetStatus.EXCEEDED

        # Check alerts
        alerts = budget.check_alerts()

        # Save
        self.store.save_cost_entry(entry)
        self.store.save_budget(budget)

        return entry, alerts

    def get_cost_entries(
        self,
        budget_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        category: Optional[CostCategory] = None,
        source: Optional[str] = None,
    ) -> list[CostEntry]:
        """Get cost entries for a budget with optional filters."""
        if start_date and end_date:
            entries = self.store.load_cost_entries_by_date_range(budget_id, start_date, end_date)
        else:
            entries = self.store.load_cost_entries(budget_id)

        if category:
            entries = [e for e in entries if e.category == category]
        if source:
            entries = [e for e in entries if e.source == source]
        return sorted(entries, key=lambda e: e.timestamp, reverse=True)

    def delete_cost_entry(self, budget_id: str, entry_id: str) -> bool:
        """Delete a cost entry and adjust budget spent."""
        budget = self.get_budget(budget_id)
        if not budget:
            return False
        entries = self.store.load_cost_entries(budget_id)
        target = next((e for e in entries if e.id == entry_id), None)
        if not target:
            return False

        # Adjust budget
        budget.spent = max(0.0, budget.spent - target.amount)
        if not budget.is_exceeded and budget.status == BudgetStatus.EXCEEDED:
            budget.status = BudgetStatus.ACTIVE
        budget.updated_at = datetime.now(timezone.utc)
        self.store.save_budget(budget)
        return self.store.delete_cost_entry(budget_id, entry_id)

    # ── Analytics ──────────────────────────────────────────────────────────

    def get_budget_summary(self, budget_id: str) -> Optional[BudgetSummary]:
        budget = self.get_budget(budget_id)
        if not budget:
            return None
        return BudgetSummary(
            budget_id=budget.id,
            name=budget.name,
            category=budget.category.value,
            limit=budget.limit,
            spent=budget.spent,
            remaining=budget.remaining,
            utilization_pct=budget.utilization_pct,
            status=budget.status.value,
            period=budget.period.value,
            period_start=budget.start_date,
            period_end=budget.period_end,
            currency=budget.currency,
            active_alerts=len(budget.check_alerts()),
        )

    def get_all_summaries(self) -> list[BudgetSummary]:
        budgets = self.list_budgets()
        return [self.get_budget_summary(b.id) for b in budgets if b is not None]

    def project_spending(self, budget_id: str) -> Optional[SpendingProjection]:
        """Project spending for the rest of the budget period based on current burn rate."""
        budget = self.get_budget(budget_id)
        if not budget:
            return None

        period_end = budget.period_end
        if not period_end:
            # One-time or no end date — can't project
            return None

        today = date.today()
        total_days = max(1, (period_end - budget.start_date).days + 1)
        days_elapsed = max(0, (today - budget.start_date).days + 1)
        days_elapsed = min(days_elapsed, total_days)

        daily_burn_rate = budget.spent / max(1, days_elapsed)
        projected_total = daily_burn_rate * total_days
        projected_overage = max(0.0, projected_total - budget.limit)
        projected_remaining = max(0.0, budget.limit - projected_total)
        on_track = projected_total <= budget.limit

        return SpendingProjection(
            budget_id=budget.id,
            budget_name=budget.name,
            total_budget=budget.limit,
            spent_so_far=budget.spent,
            days_elapsed=days_elapsed,
            total_days=total_days,
            daily_burn_rate=round(daily_burn_rate, 4),
            projected_total=round(projected_total, 2),
            projected_overage=round(projected_overage, 2),
            projected_remaining=round(projected_remaining, 2),
            on_track=on_track,
        )

    def get_costs_by_category(self, budget_id: str) -> dict[str, float]:
        """Aggregate costs by category for a budget."""
        entries = self.store.load_cost_entries(budget_id)
        by_category: dict[str, float] = {}
        for entry in entries:
            key = entry.category.value
            by_category[key] = by_category.get(key, 0.0) + entry.amount
        return by_category

    def get_costs_by_source(self, budget_id: str) -> dict[str, float]:
        """Aggregate costs by source for a budget."""
        entries = self.store.load_cost_entries(budget_id)
        by_source: dict[str, float] = {}
        for entry in entries:
            key = entry.source or "unknown"
            by_source[key] = by_source.get(key, 0.0) + entry.amount
        return by_source

    def get_daily_spending(self, budget_id: str, days: int = 30) -> dict[str, float]:
        """Get daily spending totals for the last N days."""
        entries = self.store.load_cost_entries(budget_id)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        daily: dict[str, float] = {}
        for entry in entries:
            if entry.timestamp >= cutoff:
                key = entry.timestamp.strftime("%Y-%m-%d")
                daily[key] = daily.get(key, 0.0) + entry.amount
        return daily

    # ── Alert Rules ────────────────────────────────────────────────────────

    def add_alert_rule(
        self,
        budget_id: str,
        threshold_pct: float,
        severity: AlertSeverity = AlertSeverity.WARNING,
        action: AlertAction = AlertAction.NOTIFY,
        message: Optional[str] = None,
        cooldown_minutes: int = 60,
    ) -> Optional[Budget]:
        """Add an alert rule to a budget."""
        budget = self.get_budget(budget_id)
        if not budget:
            return None
        rule = BudgetAlertRule(
            threshold_pct=threshold_pct,
            severity=severity,
            action=action,
            message=message,
            cooldown_minutes=cooldown_minutes,
        )
        budget.alert_rules.append(rule)
        budget.updated_at = datetime.now(timezone.utc)
        self.store.save_budget(budget)
        return budget

    def remove_alert_rule(self, budget_id: str, rule_id: str) -> Optional[Budget]:
        budget = self.get_budget(budget_id)
        if not budget:
            return None
        budget.alert_rules = [r for r in budget.alert_rules if r.id != rule_id]
        budget.updated_at = datetime.now(timezone.utc)
        self.store.save_budget(budget)
        return budget

    def check_budget_alerts(self, budget_id: str) -> list[dict]:
        budget = self.get_budget(budget_id)
        if not budget:
            return []
        return budget.check_alerts()

    def check_all_alerts(self) -> list[dict]:
        """Check alerts across all active budgets."""
        all_alerts = []
        for budget in self.list_budgets(status=BudgetStatus.ACTIVE):
            all_alerts.extend(budget.check_alerts())
        return sorted(all_alerts, key=lambda a: a["current_pct"], reverse=True)

    # ── Hierarchy ──────────────────────────────────────────────────────────

    def get_sub_budgets(self, parent_budget_id: str) -> list[Budget]:
        """Get all child budgets of a parent."""
        all_budgets = self.list_budgets()
        return [b for b in all_budgets if b.parent_budget_id == parent_budget_id]

    def get_rollup_summary(self, parent_budget_id: str) -> Optional[dict]:
        """Roll up spending across parent and all child budgets."""
        parent = self.get_budget(parent_budget_id)
        if not parent:
            return None

        children = self.get_sub_budgets(parent_budget_id)
        total_limit = parent.limit + sum(c.limit for c in children)
        total_spent = parent.spent + sum(c.spent for c in children)

        return {
            "parent_budget_id": parent_budget_id,
            "parent_name": parent.name,
            "total_limit": round(total_limit, 2),
            "total_spent": round(total_spent, 2),
            "total_remaining": round(max(0.0, total_limit - total_spent), 2),
            "utilization_pct": round((total_spent / total_limit * 100) if total_limit > 0 else 0, 2),
            "child_count": len(children),
            "children": [
                {"id": c.id, "name": c.name, "limit": c.limit, "spent": c.spent}
                for c in children
            ],
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _default_alert_rules() -> list[BudgetAlertRule]:
        return [
            BudgetAlertRule(threshold_pct=50, severity=AlertSeverity.INFO, action=AlertAction.NOTIFY),
            BudgetAlertRule(threshold_pct=80, severity=AlertSeverity.WARNING, action=AlertAction.NOTIFY),
            BudgetAlertRule(threshold_pct=95, severity=AlertSeverity.CRITICAL, action=AlertAction.THROTTLE),
            BudgetAlertRule(threshold_pct=100, severity=AlertSeverity.CRITICAL, action=AlertAction.HALT),
        ]
