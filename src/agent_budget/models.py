"""Core domain models for agent-budget."""

from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class BudgetPeriod(str, Enum):
    """Recurrence period for a budget."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    ONE_TIME = "one_time"


class BudgetStatus(str, Enum):
    """Current status of a budget."""
    ACTIVE = "active"
    PAUSED = "paused"
    EXCEEDED = "exceeded"
    EXPIRED = "expired"
    CLOSED = "closed"


class CostCategory(str, Enum):
    """Standard cost categories for agents."""
    COMPUTE = "compute"
    API_CALLS = "api_calls"
    STORAGE = "storage"
    NETWORK = "network"
    LICENSING = "licensing"
    LABOR = "labor"
    INFRASTRUCTURE = "infrastructure"
    MISC = "misc"


class AlertSeverity(str, Enum):
    """Severity level for budget alerts."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertAction(str, Enum):
    """Action to take when alert fires."""
    NOTIFY = "notify"
    THROTTLE = "throttle"
    HALT = "halt"


# ── Models ─────────────────────────────────────────────────────────────────

class BudgetAlertRule(BaseModel):
    """A rule that triggers an alert when a budget threshold is reached."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    threshold_pct: float = Field(
        ..., ge=0, le=100,
        description="Percentage of budget (0-100) that triggers this alert",
    )
    severity: AlertSeverity = AlertSeverity.WARNING
    action: AlertAction = AlertAction.NOTIFY
    message: Optional[str] = None
    cooldown_minutes: int = Field(
        default=60, ge=0,
        description="Minimum minutes between repeated alerts for this rule",
    )

    @field_validator("threshold_pct")
    @classmethod
    def threshold_range(cls, v: float) -> float:
        if not 0 <= v <= 100:
            raise ValueError("threshold_pct must be between 0 and 100")
        return v


class Budget(BaseModel):
    """A budget that tracks spending against a limit for a given period."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = Field(..., min_length=1, description="Human-readable budget name")
    description: Optional[str] = None
    category: CostCategory = CostCategory.MISC

    # Financial
    limit: float = Field(..., gt=0, description="Budget limit in the given currency")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    spent: float = Field(default=0.0, ge=0)

    # Period
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = None

    # Hierarchy
    parent_budget_id: Optional[str] = None

    # State
    status: BudgetStatus = BudgetStatus.ACTIVE
    alert_rules: list[BudgetAlertRule] = Field(default_factory=list)

    # Metadata
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)

    @property
    def utilization_pct(self) -> float:
        """Percentage of budget consumed."""
        if self.limit == 0:
            return 0.0
        return round((self.spent / self.limit) * 100, 2)

    @property
    def is_exceeded(self) -> bool:
        return self.spent > self.limit

    @property
    def period_end(self) -> Optional[date]:
        """Calculate the end date of the current budget period."""
        if self.end_date:
            return self.end_date
        if self.period == BudgetPeriod.ONE_TIME:
            return None
        if self.period == BudgetPeriod.DAILY:
            return self.start_date
        if self.period == BudgetPeriod.WEEKLY:
            return self.start_date + timedelta(days=6)
        if self.period == BudgetPeriod.MONTHLY:
            if self.start_date.month == 12:
                return date(self.start_date.year + 1, 1, self.start_date.day)
            return date(self.start_date.year, self.start_date.month + 1, self.start_date.day)
        if self.period == BudgetPeriod.QUARTERLY:
            m = self.start_date.month
            q_end_month = ((m - 1) // 3 + 1) * 3
            if q_end_month > 12:
                return date(self.start_date.year + 1, q_end_month - 12, self.start_date.day)
            return date(self.start_date.year, q_end_month, self.start_date.day)
        if self.period == BudgetPeriod.YEARLY:
            return date(self.start_date.year + 1, self.start_date.month, self.start_date.day)
        return None

    def reset_period(self) -> None:
        """Reset spending for a new budget period."""
        self.spent = 0.0
        if self.period != BudgetPeriod.ONE_TIME:
            self.start_date = date.today()
        self.updated_at = datetime.now(timezone.utc)

    def check_alerts(self) -> list[dict]:
        """Check all alert rules against current utilization. Returns triggered alerts."""
        triggered = []
        for rule in self.alert_rules:
            if self.utilization_pct >= rule.threshold_pct:
                triggered.append({
                    "budget_id": self.id,
                    "budget_name": self.name,
                    "rule_id": rule.id,
                    "threshold_pct": rule.threshold_pct,
                    "current_pct": self.utilization_pct,
                    "severity": rule.severity.value,
                    "action": rule.action.value,
                    "message": rule.message or f"Budget '{self.name}' at {self.utilization_pct}% of limit",
                })
        return triggered


class CostEntry(BaseModel):
    """A single cost/expense record."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    budget_id: str
    amount: float = Field(..., gt=0, description="Cost amount")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    category: CostCategory = CostCategory.MISC
    description: Optional[str] = None
    source: Optional[str] = Field(None, description="What generated this cost (e.g., 'openai-gpt4', 'aws-ec2')")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SpendingProjection(BaseModel):
    """Projected spending for the remainder of a budget period."""
    budget_id: str
    budget_name: str
    total_budget: float
    spent_so_far: float
    days_elapsed: int
    total_days: int
    daily_burn_rate: float
    projected_total: float
    projected_overage: float
    projected_remaining: float
    on_track: bool


class BudgetSummary(BaseModel):
    """Summary view of a budget's current state."""
    budget_id: str
    name: str
    category: str
    limit: float
    spent: float
    remaining: float
    utilization_pct: float
    status: str
    period: str
    period_start: date
    period_end: Optional[date]
    currency: str
    active_alerts: int
