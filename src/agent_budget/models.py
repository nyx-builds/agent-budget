"""Pydantic models for Agent Budget."""
from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _advance_months(d: date, n: int) -> date:
    """Advance a date by ``n`` months, clamping the day to the end of the
    target month (e.g. Jan 31 + 1 month → Feb 28)."""
    import calendar

    idx = d.month - 1 + n
    year = d.year + idx // 12
    month = idx % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    return d.replace(year=year, month=month, day=min(d.day, max_day))


# --- Enums ---

class BudgetPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecurringFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class ExpenseStatus(str, Enum):
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class SavingsGoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"


class SpendingRuleAction(str, Enum):
    WARN = "warn"
    BLOCK = "block"
    APPROVE = "approve"


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


# --- Models ---

class AlertThreshold(BaseModel):
    """A threshold that triggers a budget alert."""
    percent: float = Field(ge=0, le=100, description="Percentage of budget spent (0-100)")
    level: AlertLevel = Field(description="Alert level when threshold is crossed")

    @field_validator("percent")
    @classmethod
    def percent_must_be_valid(cls, v: float) -> float:
        if v < 0 or v > 100:
            raise ValueError("Percent must be between 0 and 100")
        return v


class BudgetRollover(BaseModel):
    """Tracks budget rollover from one period to the next."""
    budget_id: str = Field(description="Budget ID")
    from_period_start: date = Field(description="Start of the source period")
    from_period_end: date = Field(description="End of the source period")
    to_period_start: date = Field(description="Start of the target period")
    to_period_end: date = Field(description="End of the target period")
    unspent_amount: float = Field(ge=0, description="Amount rolled over (unspent from previous period)")
    previous_limit: float = Field(gt=0, description="Original budget limit for the source period")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Budget(BaseModel):
    """A spending budget with a limit, period, and optional categories."""
    id: str = Field(default_factory=lambda: f"BUD-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Budget name")
    limit: float = Field(gt=0, description="Spending limit for the period")
    period: BudgetPeriod = Field(description="Budget period")
    category: Optional[str] = Field(default=None, description="Optional category this budget applies to")
    currency: str = Field(default="USD", description="Currency code")
    alert_thresholds: list[AlertThreshold] = Field(
        default_factory=lambda: [
            AlertThreshold(percent=50, level=AlertLevel.INFO),
            AlertThreshold(percent=75, level=AlertLevel.WARNING),
            AlertThreshold(percent=90, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ]
    )
    active: bool = Field(default=True)
    rollover_enabled: bool = Field(default=False, description="Whether unspent budget rolls over to next period")
    rollover_cap: Optional[float] = Field(default=None, description="Max amount that can roll over (None = no cap)")
    current_rollover: float = Field(default=0.0, description="Amount rolled over from previous period")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_period_start(self, ref_date: Optional[date] = None) -> date:
        """Get the start date of the current budget period."""
        d = ref_date or date.today()
        if self.period == BudgetPeriod.DAILY:
            return d
        elif self.period == BudgetPeriod.WEEKLY:
            return d - timedelta(days=d.weekday())
        elif self.period == BudgetPeriod.MONTHLY:
            return d.replace(day=1)
        elif self.period == BudgetPeriod.QUARTERLY:
            quarter_start_month = ((d.month - 1) // 3) * 3 + 1
            return d.replace(month=quarter_start_month, day=1)
        elif self.period == BudgetPeriod.YEARLY:
            return d.replace(month=1, day=1)
        return d

    def get_period_end(self, ref_date: Optional[date] = None) -> date:
        """Get the end date of the current budget period."""
        start = self.get_period_start(ref_date)
        if self.period == BudgetPeriod.DAILY:
            return start
        elif self.period == BudgetPeriod.WEEKLY:
            return start + timedelta(days=6)
        elif self.period == BudgetPeriod.MONTHLY:
            if start.month == 12:
                return start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                return start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        elif self.period == BudgetPeriod.QUARTERLY:
            quarter_end_month = ((start.month - 1) // 3) * 3 + 3
            if quarter_end_month == 12:
                return start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                return start.replace(month=quarter_end_month + 1, day=1) - timedelta(days=1)
        elif self.period == BudgetPeriod.YEARLY:
            return start.replace(month=12, day=31)
        return start

    @property
    def effective_limit(self) -> float:
        """Budget limit including any rollover from previous period."""
        return self.limit + self.current_rollover


class Expense(BaseModel):
    """A single expense entry."""
    id: str = Field(default_factory=lambda: f"EXP-{uuid.uuid4().hex[:8].upper()}")
    amount: float = Field(gt=0, description="Expense amount")
    category: str = Field(min_length=1, description="Expense category")
    description: str = Field(default="", description="Description of the expense")
    expense_date: date = Field(default_factory=date.today, description="Date of the expense")
    tags: list[str] = Field(default_factory=list, description="Tags for grouping/filtering")
    currency: str = Field(default="USD", description="Currency code")
    status: ExpenseStatus = Field(default=ExpenseStatus.CONFIRMED)
    budget_id: Optional[str] = Field(default=None, description="ID of the budget this expense counts against")
    metadata: dict = Field(default_factory=dict, description="Extra metadata (e.g., vendor, receipt URL)")
    vendor: Optional[str] = Field(default=None, description="Vendor or merchant name")
    receipt_url: Optional[str] = None
    reimbursable: bool = Field(default=False, description="Whether this expense is reimbursable")
    approved_by: Optional[str] = Field(default=None, description="Who approved this expense (for spending rules)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


class RecurringExpense(BaseModel):
    """A recurring expense template that generates expenses on a schedule."""
    id: str = Field(default_factory=lambda: f"REC-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Name of the recurring expense")
    amount: float = Field(gt=0, description="Amount per occurrence")
    category: str = Field(min_length=1, description="Expense category")
    frequency: RecurringFrequency = Field(description="How often the expense recurs")
    description: str = Field(default="")
    currency: str = Field(default="USD")
    tags: list[str] = Field(default_factory=list)
    budget_id: Optional[str] = Field(default=None)
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = Field(default=None, description="Optional end date")
    next_due: date = Field(default_factory=date.today)
    active: bool = Field(default=True)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    def advance_next_due(self) -> date:
        """Calculate the next due date after the current one."""
        d = self.next_due
        if self.frequency == RecurringFrequency.DAILY:
            return d + timedelta(days=1)
        elif self.frequency == RecurringFrequency.WEEKLY:
            return d + timedelta(weeks=1)
        elif self.frequency == RecurringFrequency.BIWEEKLY:
            return d + timedelta(weeks=2)
        elif self.frequency == RecurringFrequency.MONTHLY:
            return _advance_months(d, 1)
        elif self.frequency == RecurringFrequency.QUARTERLY:
            return _advance_months(d, 3)
        elif self.frequency == RecurringFrequency.YEARLY:
            return d.replace(year=d.year + 1)
        return d + timedelta(days=30)  # fallback


class BudgetAlert(BaseModel):
    """An alert triggered when a budget threshold is crossed."""
    id: str = Field(default_factory=lambda: f"ALR-{uuid.uuid4().hex[:8].upper()}")
    budget_id: str = Field(description="Budget that triggered the alert")
    budget_name: str = Field(description="Budget name for display")
    level: AlertLevel = Field(description="Alert severity")
    percent_spent: float = Field(description="Percentage of budget spent")
    amount_spent: float = Field(description="Amount spent in the period")
    budget_limit: float = Field(description="Budget limit")
    remaining: float = Field(description="Amount remaining in budget")
    period: BudgetPeriod = Field(description="Budget period")
    message: str = Field(default="", description="Human-readable alert message")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SpendingForecast(BaseModel):
    """A spending forecast for a future period."""
    budget_id: Optional[str] = Field(default=None)
    category: Optional[str] = Field(default=None)
    period: str = Field(description="Forecast period description")
    projected_spending: float = Field(description="Projected amount to be spent")
    budget_limit: Optional[float] = Field(default=None)
    confidence: float = Field(default=0.0, description="Confidence level 0-1")
    based_on_periods: int = Field(default=0, description="Number of historical periods used")


class BudgetComparison(BaseModel):
    """Budget vs. actual comparison for a category or overall."""
    budget_id: str
    budget_name: str
    category: Optional[str]
    budget_limit: float
    actual_spent: float
    remaining: float
    percent_used: float
    period: BudgetPeriod
    period_start: date
    period_end: date
    status: str = Field(description="under, on_track, over, critical")
    rollover_amount: float = Field(default=0.0, description="Amount rolled over from previous period")
    effective_limit: float = Field(default=0.0, description="Limit including rollover")


class SavingsGoal(BaseModel):
    """A savings goal that tracks progress toward a target amount."""
    id: str = Field(default_factory=lambda: f"SAV-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Goal name (e.g., 'Emergency Fund')")
    target_amount: float = Field(gt=0, description="Target amount to save")
    current_amount: float = Field(default=0.0, ge=0, description="Amount saved so far")
    currency: str = Field(default="USD", description="Currency code")
    target_date: Optional[date] = Field(default=None, description="Target date to reach goal")
    category: Optional[str] = Field(default=None, description="Associated budget category")
    status: SavingsGoalStatus = Field(default=SavingsGoalStatus.ACTIVE)
    description: str = Field(default="", description="Goal description")
    contributions: list[SavingsContribution] = Field(default_factory=list, description="List of contributions")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def progress_percent(self) -> float:
        """Percentage progress toward the goal."""
        if self.target_amount <= 0:
            return 0.0
        return min(100.0, (self.current_amount / self.target_amount) * 100)

    @property
    def remaining(self) -> float:
        """Amount remaining to reach the goal."""
        return max(0.0, self.target_amount - self.current_amount)

    @property
    def is_complete(self) -> bool:
        """Whether the goal has been reached."""
        return self.current_amount >= self.target_amount

    @property
    def monthly_contribution_needed(self) -> Optional[float]:
        """Monthly contribution needed to reach goal by target date."""
        if not self.target_date or self.is_complete:
            return None
        today = date.today()
        if self.target_date <= today:
            return self.remaining
        months_remaining = max(1, (self.target_date.year - today.year) * 12 + (self.target_date.month - today.month))
        return self.remaining / months_remaining


class SavingsContribution(BaseModel):
    """A contribution to a savings goal."""
    id: str = Field(default_factory=lambda: f"CON-{uuid.uuid4().hex[:8].upper()}")
    amount: float = Field(description="Contribution amount (negative for withdrawals)")
    note: str = Field(default="", description="Optional note")
    contribution_date: date = Field(default_factory=date.today)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SpendingRule(BaseModel):
    """A spending rule that controls expense behavior for a category."""
    id: str = Field(default_factory=lambda: f"RUL-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Rule name (e.g., 'API spending cap')")
    category: str = Field(min_length=1, description="Category this rule applies to")
    action: SpendingRuleAction = Field(description="What happens when the rule is triggered")
    threshold_amount: Optional[float] = Field(default=None, description="Max amount before rule triggers")
    threshold_percent: Optional[float] = Field(default=None, description="Max percent of budget before rule triggers")
    budget_id: Optional[str] = Field(default=None, description="Associated budget ID")
    enabled: bool = Field(default=True)
    requires_approval_above: Optional[float] = Field(default=None, description="Single expenses above this need approval")
    description: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def check_expense(self, expense: Expense, budget_spent: float = 0.0, budget_limit: float = 0.0) -> Optional[str]:
        """Check if an expense violates this rule.

        Returns None if the expense is allowed, or a reason string if blocked/warned.
        """
        if not self.enabled:
            return None

        # Only check expenses in the same category
        if self.category.lower() != expense.category.lower():
            return None

        # Check single expense amount approval
        if self.requires_approval_above and expense.amount > self.requires_approval_above:
            if self.action == SpendingRuleAction.BLOCK:
                if not expense.approved_by:
                    return f"Expense ${expense.amount:.2f} exceeds approval threshold ${self.requires_approval_above:.2f} for category '{self.category}'"
            elif self.action == SpendingRuleAction.WARN:
                if not expense.approved_by:
                    return f"WARNING: Expense ${expense.amount:.2f} exceeds approval threshold ${self.requires_approval_above:.2f} for category '{self.category}'"

        # Check total spending threshold (amount)
        if self.threshold_amount and budget_spent + expense.amount > self.threshold_amount:
            if self.action == SpendingRuleAction.BLOCK:
                return f"Total spending ${budget_spent + expense.amount:.2f} would exceed cap ${self.threshold_amount:.2f} for category '{self.category}'"
            elif self.action == SpendingRuleAction.WARN:
                return f"WARNING: Total spending approaching cap for category '{self.category}'"

        # Check total spending threshold (percent)
        if self.threshold_percent and budget_limit > 0:
            new_percent = ((budget_spent + expense.amount) / budget_limit) * 100
            if new_percent > self.threshold_percent:
                if self.action == SpendingRuleAction.BLOCK:
                    return f"Total spending would reach {new_percent:.1f}% of budget, exceeding {self.threshold_percent:.0f}% cap for category '{self.category}'"
                elif self.action == SpendingRuleAction.WARN:
                    return f"WARNING: Spending would reach {new_percent:.1f}% of budget for category '{self.category}'"

        return None


# --- v0.5.0 Cost Guardrail Models ---


class GuardrailScope(str, Enum):
    """Scope at which a guardrail applies."""
    GLOBAL = "global"
    AGENT = "agent"
    MODEL = "model"
    BUDGET = "budget"
    TASK = "task"


class GuardrailAction(str, Enum):
    """What happens when a guardrail is breached."""
    ALLOW = "allow"           # spending within limits, all clear
    WARN = "warn"             # approaching limit, warn but allow
    THROTTLE = "throttle"     # slow down — deny until cooldown passes
    BLOCK = "block"           # hard block this call
    KILL = "kill"             # kill switch active, all calls denied


class ThrottleTier(BaseModel):
    """A spending tier that triggers progressive cost throttling.

    When the agent's spend reaches ``threshold_percent`` of the limit, the
    guardrail recommends ``max_cost_usd`` — the largest per-call cost still
    allowed.  This enables graceful degradation (e.g., switch from GPT-4o
    to GPT-4o-mini at 70% spend, or cap context at 95%).

    Throttling is advisory: the agent SHOULD respect ``max_cost_usd``, but
    the guardrail does not hard-block unless ``block_if_exceeded`` is True.
    """
    threshold_percent: float = Field(ge=0, le=100, description="Spend percentage that triggers this tier")
    max_cost_usd: Optional[float] = Field(default=None, ge=0, description="Max per-call cost in this tier (None = advisory only)")
    recommended_model: Optional[str] = Field(default=None, description="Cheaper model to switch to (advisory)")
    block_if_exceeded: bool = Field(default=False, description="If True, calls above max_cost_usd are blocked (not just warned)")
    message: str = Field(default="", description="Human-readable throttle instruction")


# Default throttle tiers: graceful degradation as budget depletes
DEFAULT_THROTTLE_TIERS: list[ThrottleTier] = [
    ThrottleTier(
        threshold_percent=60.0,
        max_cost_usd=0.50,
        recommended_model=None,
        message="Spend at 60% — consider reducing context size",
    ),
    ThrottleTier(
        threshold_percent=75.0,
        max_cost_usd=0.20,
        recommended_model="gpt-4o-mini",
        message="Spend at 75% — switch to cheaper model, reduce token count",
    ),
    ThrottleTier(
        threshold_percent=90.0,
        max_cost_usd=0.05,
        recommended_model="gpt-4o-mini",
        block_if_exceeded=True,
        message="Spend at 90% — emergency throttle: only very cheap calls allowed",
    ),
]


class CostGuardrail(BaseModel):
    """A cost guardrail that enforces spending limits for agents in real time.

    Unlike spending rules (which check at expense-add time), guardrails are
    checked *before* an LLM call is made — a pre-flight check that returns
    allow/deny + reason, enabling agents to self-regulate.
    """
    id: str = Field(default_factory=lambda: f"GDR-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Guardrail name (e.g., 'Daily LLM cap')")
    scope: GuardrailScope = Field(description="Scope: global, agent, model, budget, task")
    scope_id: Optional[str] = Field(default=None, description="ID for scoped guardrails (agent_id, model_id, budget_id, task_id)")
    daily_limit_usd: Optional[float] = Field(default=None, ge=0, description="Hard daily spend limit in USD")
    hourly_limit_usd: Optional[float] = Field(default=None, ge=0, description="Hard hourly spend limit in USD")
    per_call_limit_usd: Optional[float] = Field(default=None, ge=0, description="Max cost per single LLM call")
    monthly_limit_usd: Optional[float] = Field(default=None, ge=0, description="Hard monthly spend limit")
    warn_at_percent: float = Field(default=80.0, ge=0, le=100, description="Percent of limit to warn at")
    block_at_percent: float = Field(default=100.0, ge=0, le=100, description="Percent of limit to block at")
    cooldown_minutes: int = Field(default=0, ge=0, description="If breached, block calls for N minutes")
    # v0.8.0: Progressive throttling tiers
    throttle_enabled: bool = Field(default=False, description="Enable progressive cost throttling between warn and block")
    throttle_tiers: list[ThrottleTier] = Field(
        default_factory=lambda: list(DEFAULT_THROTTLE_TIERS),
        description="Spend tiers that trigger graduated cost throttling",
    )
    enabled: bool = Field(default=True)
    priority: int = Field(default=0, ge=0, description="Higher priority checked first")
    description: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def matches(self, scope: GuardrailScope, scope_id: Optional[str] = None) -> bool:
        """Check if this guardrail matches the given scope."""
        if not self.enabled:
            return False
        if self.scope != scope:
            return False
        if self.scope == GuardrailScope.GLOBAL:
            return True
        if scope_id and self.scope_id:
            return self.scope_id.lower() == scope_id.lower()
        return self.scope_id is None

    def get_active_throttle_tier(self, percent_used: float) -> Optional[ThrottleTier]:
        """Find the highest throttle tier whose threshold has been reached.

        Returns the most restrictive applicable tier, or None if throttling
        is disabled or no tier threshold has been crossed yet.
        """
        if not self.throttle_enabled or not self.throttle_tiers:
            return None
        active = None
        for tier in sorted(self.throttle_tiers, key=lambda t: t.threshold_percent):
            if percent_used >= tier.threshold_percent:
                active = tier
            else:
                break
        return active


class GuardrailDecision(BaseModel):
    """Result of a guardrail pre-flight check."""
    allowed: bool = Field(description="Whether the LLM call is allowed")
    action: GuardrailAction = Field(description="Recommended action")
    reason: str = Field(default="", description="Human-readable reason")
    guardrail_id: Optional[str] = Field(default=None, description="ID of the guardrail that triggered")
    current_spend_usd: float = Field(default=0.0, description="Current spend in the relevant period")
    limit_usd: Optional[float] = Field(default=None, description="The limit that was checked")
    percent_used: float = Field(default=0.0, description="Percent of limit used")
    cooldown_until: Optional[datetime] = Field(default=None, description="If in cooldown, when it expires")
    suggestions: list[str] = Field(default_factory=list, description="Cost-saving suggestions")
    projection: Optional[ProjectionIntegration] = Field(default=None, description="Spend projection data if used in check")
    webhooks_fired: int = Field(default=0, description="Number of webhooks notified by this check")
    # v0.8.0: Throttle info
    throttle_tier: Optional[str] = Field(default=None, description="Active throttle tier name/percent if throttled")
    max_recommended_cost_usd: Optional[float] = Field(default=None, description="Max per-call cost recommended by active throttle tier")
    recommended_model: Optional[str] = Field(default=None, description="Cheaper model recommended by throttle tier")


class KillSwitch(BaseModel):
    """Emergency kill switch state — when active, all LLM calls are blocked."""
    active: bool = Field(default=False, description="Whether the kill switch is currently active")
    reason: str = Field(default="", description="Why the kill switch was triggered")
    triggered_at: Optional[datetime] = Field(default=None, description="When it was activated")
    triggered_by: Optional[str] = Field(default=None, description="Who/what triggered it")
    expires_at: Optional[datetime] = Field(default=None, description="When it auto-resets (None = manual reset only)")
    override_token: Optional[str] = Field(default=None, description="Token required to reset (for safety)")
    breach_count: int = Field(default=0, description="Total times auto-triggered")

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """Check if kill switch is currently active (and not expired)."""
        if not self.active:
            return False
        now = now or datetime.now(timezone.utc)
        if self.expires_at and now > self.expires_at:
            return False
        return True


class CostAlertEvent(BaseModel):
    """A cost alert event triggered by guardrails (separate from budget alerts)."""
    id: str = Field(default_factory=lambda: f"CST-{uuid.uuid4().hex[:8].upper()}")
    guardrail_id: Optional[str] = Field(default=None, description="Guardrail that triggered this")
    scope: GuardrailScope = Field(default=GuardrailScope.GLOBAL)
    scope_id: Optional[str] = Field(default=None)
    level: AlertLevel = Field(default=AlertLevel.WARNING)
    message: str = Field(default="")
    current_spend_usd: float = Field(default=0.0)
    limit_usd: Optional[float] = Field(default=None)
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = Field(default=False)


# --- v0.6.0 Spend Projection & Loop Detection Models ---


class SpendProjection(BaseModel):
    """Projected spend and ETA-to-limit for a guardrail scope.

    Predicts when limits will be hit based on recent spend velocity.
    This is the 'burn forecast' — agents can use it to decide whether
    to switch models, reduce context, or proactively throttle before
    a guardrail hard-blocks them.
    """
    scope: GuardrailScope = Field(description="Guardrail scope")
    scope_id: Optional[str] = Field(default=None, description="Scope entity ID")
    period: str = Field(description="Period being projected: daily, hourly, monthly")
    current_spend_usd: float = Field(default=0.0, ge=0, description="Spend so far this period")
    projected_spend_usd: float = Field(default=0.0, ge=0, description="Projected total spend by period end")
    spend_rate_per_hour: float = Field(default=0.0, description="Current spend rate USD/hour")
    limit_usd: Optional[float] = Field(default=None, description="The limit for this period (if a guardrail applies)")
    projected_exceeds_limit: bool = Field(default=False, description="Whether projected spend will exceed the limit")
    eta_minutes_to_limit: Optional[float] = Field(default=None, description="Minutes until limit is hit at current rate (None if no limit or already over)")
    will_breach_guardrail: bool = Field(default=False, description="Whether a guardrail will trigger before period end")
    guardrail_id: Optional[str] = Field(default=None, description="Guardrail that will be breached")
    call_count_in_period: int = Field(default=0, description="Number of LLM calls so far this period")
    avg_cost_per_call: float = Field(default=0.0, description="Average cost per call this period")
    confidence: float = Field(default=0.0, ge=0, le=1, description="Confidence in projection (based on data points)")
    recommendation: str = Field(default="", description="Human-readable recommendation")
    projected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopDetectionConfig(BaseModel):
    """Configuration for detecting runaway agent loops.

    Loop detection identifies when an agent is making repeated identical
    or near-identical LLM calls — a common failure mode where an agent
    burns budget in an infinite retry loop. Unlike guardrails (which
    check spend amounts), loop detection checks *call patterns*.
    """
    id: str = Field(default_factory=lambda: f"LDC-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Config name (e.g., 'Global loop guard')")
    enabled: bool = Field(default=True)
    # Detection window: how far back to look for repeated calls
    window_minutes: int = Field(default=10, ge=1, description="Time window to detect loops in")
    # Threshold: number of identical/similar calls in window to flag a loop
    repeat_threshold: int = Field(default=5, ge=2, description="Number of similar calls to flag a loop")
    # Similarity: how similar calls must be to count as 'the same'
    # 1.0 = exact match, 0.9 = very similar, 0.7 = loosely similar
    similarity_threshold: float = Field(default=0.9, ge=0.0, le=1.0, description="Jaccard similarity threshold for 'similar' calls")
    # Scope: apply to all agents or specific ones
    agent_id: Optional[str] = Field(default=None, description="Only apply to this agent (None = all)")
    model_id: Optional[str] = Field(default=None, description="Only apply to this model (None = all)")
    # Auto-action when loop detected
    auto_block_minutes: int = Field(default=0, ge=0, description="Auto-block agent for N minutes when loop detected (0 = just alert)")
    # Cost threshold: only flag loops where cumulative cost exceeds this
    min_cost_usd: float = Field(default=0.0, ge=0, description="Minimum cumulative cost in window to flag (0 = always flag)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LoopDetectionResult(BaseModel):
    """Result of a loop detection check."""
    detected: bool = Field(description="Whether a loop was detected")
    config_id: Optional[str] = Field(default=None, description="Config that triggered")
    agent_id: Optional[str] = Field(default=None)
    model_id: Optional[str] = Field(default=None)
    call_count: int = Field(default=0, description="Number of similar calls in window")
    window_minutes: int = Field(default=0, description="Detection window used")
    cumulative_cost_usd: float = Field(default=0.0, description="Total cost of the repeated calls")
    avg_similarity: float = Field(default=0.0, description="Average similarity between calls")
    sample_signature: Optional[str] = Field(default=None, description="A sample of the repeated call signature")
    recommendation: str = Field(default="", description="Human-readable recommendation")
    blocked_until: Optional[datetime] = Field(default=None, description="If auto-blocked, when the block expires")
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CurrencyInfo(BaseModel):
    """Currency metadata."""
    code: str
    name: str
    symbol: str
    decimal_places: int = 2


# --- v0.3.0 Analytics Models ---

class SpendingTrend(BaseModel):
    """Spending trend analysis for a category or overall."""
    category: str = Field(description="Category name (or 'total' for overall)")
    current_period_spending: float = Field(description="Spending in the current period")
    previous_period_spending: float = Field(description="Spending in the previous period")
    change_amount: float = Field(description="Absolute change in spending")
    change_percent: float = Field(description="Percentage change in spending (-100 to +inf)")
    direction: TrendDirection = Field(description="Trend direction (up, down, flat)")
    period_type: str = Field(description="Period type (e.g., 'monthly', 'weekly')")
    current_period: str = Field(description="Current period description")
    previous_period: str = Field(description="Previous period description")


class CategoryBreakdown(BaseModel):
    """Detailed breakdown of spending by category for a period."""
    category: str = Field(description="Category name")
    total: float = Field(ge=0, description="Total spending in category")
    count: int = Field(ge=0, description="Number of expenses")
    average: float = Field(ge=0, description="Average expense amount")
    percentage: float = Field(ge=0, description="Percentage of total spending")
    largest_expense: Optional[float] = Field(default=None, description="Largest single expense")
    vendors: list[str] = Field(default_factory=list, description="Top vendors in this category")


class PeriodComparison(BaseModel):
    """Comparison of spending between two time periods."""
    period_a_start: date = Field(description="Start of period A")
    period_a_end: date = Field(description="End of period A")
    period_b_start: date = Field(description="Start of period B")
    period_b_end: date = Field(description="End of period B")
    period_a_total: float = Field(description="Total spending in period A")
    period_b_total: float = Field(description="Total spending in period B")
    change_amount: float = Field(description="Absolute change")
    change_percent: float = Field(description="Percentage change")
    direction: TrendDirection = Field(description="Trend direction")
    category_trends: list[SpendingTrend] = Field(default_factory=list, description="Per-category trends")


class BudgetTemplate(BaseModel):
    """A pre-built budget template for common agent scenarios."""
    id: str = Field(default_factory=lambda: f"TPL-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Template name")
    description: str = Field(default="", description="Template description")
    category: str = Field(description="Budget category")
    default_limit: float = Field(gt=0, description="Default spending limit")
    period: BudgetPeriod = Field(description="Budget period")
    currency: str = Field(default="USD", description="Default currency")
    suggested_alerts: list[AlertThreshold] = Field(default_factory=list, description="Suggested alert thresholds")
    suggested_rules: list[dict] = Field(default_factory=list, description="Suggested spending rules config")
    tags: list[str] = Field(default_factory=list, description="Tags for this template")
    is_builtin: bool = Field(default=False, description="Whether this is a built-in template")


class CSVImportResult(BaseModel):
    """Result of a CSV import operation."""
    total_rows: int = Field(description="Total rows in the CSV file")
    imported: int = Field(description="Successfully imported rows")
    skipped: int = Field(description="Skipped rows (empty or invalid)")
    errors: list[str] = Field(default_factory=list, description="Import errors")
    expense_ids: list[str] = Field(default_factory=list, description="IDs of created expenses")
    total_amount: float = Field(default=0.0, description="Total amount of imported expenses")


# --- v0.4.0 Income & Cash Flow Models ---

class IncomeStatus(str, Enum):
    PENDING = "pending"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class RecurringIncome(BaseModel):
    """A recurring income source that generates income entries on a schedule."""
    id: str = Field(default_factory=lambda: f"RIC-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Name of the recurring income (e.g., 'Monthly consulting')")
    amount: float = Field(gt=0, description="Amount per occurrence")
    source: str = Field(min_length=1, description="Income source/counterparty")
    frequency: RecurringFrequency = Field(description="How often income recurs")
    description: str = Field(default="")
    currency: str = Field(default="USD")
    tags: list[str] = Field(default_factory=list)
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = Field(default=None, description="Optional end date")
    next_due: date = Field(default_factory=date.today)
    active: bool = Field(default=True)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    def advance_next_due(self) -> date:
        """Calculate the next due date after the current one."""
        d = self.next_due
        if self.frequency == RecurringFrequency.DAILY:
            return d + timedelta(days=1)
        elif self.frequency == RecurringFrequency.WEEKLY:
            return d + timedelta(weeks=1)
        elif self.frequency == RecurringFrequency.BIWEEKLY:
            return d + timedelta(weeks=2)
        elif self.frequency == RecurringFrequency.MONTHLY:
            return _advance_months(d, 1)
        elif self.frequency == RecurringFrequency.QUARTERLY:
            return _advance_months(d, 3)
        elif self.frequency == RecurringFrequency.YEARLY:
            return d.replace(year=d.year + 1)
        return d + timedelta(days=30)  # fallback


class Income(BaseModel):
    """A single income/revenue entry."""
    id: str = Field(default_factory=lambda: f"INC-{uuid.uuid4().hex[:8].upper()}")
    amount: float = Field(gt=0, description="Income amount")
    source: str = Field(min_length=1, description="Income source (e.g., 'client-A', 'API-sales')")
    description: str = Field(default="", description="Description of the income")
    income_date: date = Field(default_factory=date.today, description="Date of the income")
    tags: list[str] = Field(default_factory=list, description="Tags for grouping/filtering")
    currency: str = Field(default="USD", description="Currency code")
    status: IncomeStatus = Field(default=IncomeStatus.RECEIVED)
    metadata: dict = Field(default_factory=dict, description="Extra metadata")
    recurring_id: Optional[str] = Field(default=None, description="ID of recurring template if auto-generated")
    invoice_ref: Optional[str] = Field(default=None, description="Optional invoice reference")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


class CashFlowSummary(BaseModel):
    """Cash flow analysis for a period."""
    start_date: date = Field(description="Period start date")
    end_date: date = Field(description="Period end date")
    total_income: float = Field(default=0.0, description="Total income received")
    total_expenses: float = Field(default=0.0, description="Total expenses incurred")
    net_cash_flow: float = Field(default=0.0, description="Income minus expenses")
    savings_rate: float = Field(default=0.0, description="Percentage of income saved (net/income * 100)")
    expense_ratio: float = Field(default=0.0, description="Percentage of income spent (expenses/income * 100)")
    income_count: int = Field(default=0, description="Number of income entries")
    expense_count: int = Field(default=0, description="Number of expense entries")
    largest_income_source: Optional[str] = Field(default=None, description="Top income source by amount")
    largest_expense_category: Optional[str] = Field(default=None, description="Top expense category by amount")
    currency: str = Field(default="USD")
    is_profitable: bool = Field(default=False, description="Whether income exceeds expenses")


class BurnRate(BaseModel):
    """Burn rate and runway analysis."""
    avg_monthly_burn: float = Field(description="Average monthly spending")
    avg_monthly_income: float = Field(description="Average monthly income")
    net_burn: float = Field(description="Net monthly burn (expenses - income), positive = burning")
    runway_months: Optional[float] = Field(default=None, description="Months of runway at current net burn (None if profitable)")
    total_savings: float = Field(default=0.0, description="Total savings across all goals")
    analysis_period_months: int = Field(description="Number of months analyzed")
    is_sustainable: bool = Field(default=False, description="Whether income covers expenses")
    currency: str = Field(default="USD")
    burn_trend: TrendDirection = Field(default=TrendDirection.FLAT, description="Is burn increasing, decreasing, or flat")
    projected_depletion: Optional[date] = Field(default=None, description="Projected date savings run out (None if sustainable)")


class FinancialDashboard(BaseModel):
    """Comprehensive financial health summary."""
    as_of: date = Field(description="Dashboard date")
    total_budget_remaining: float = Field(default=0.0, description="Total remaining across all active budgets")
    total_budget_limit: float = Field(default=0.0, description="Total budget limit across all active budgets")
    total_savings: float = Field(default=0.0, description="Total saved across savings goals")
    total_savings_targets: float = Field(default=0.0, description="Total savings targets")
    savings_progress_pct: float = Field(default=0.0, description="Overall savings progress percentage")
    active_budgets: int = Field(default=0, description="Number of active budgets")
    budgets_over_limit: int = Field(default=0, description="Number of budgets over their limit")
    active_alerts: int = Field(default=0, description="Number of active alerts")
    monthly_cash_flow: Optional[CashFlowSummary] = Field(default=None, description="Current month cash flow")
    burn_rate: Optional[BurnRate] = Field(default=None, description="Burn rate analysis")
    health_score: float = Field(default=0.0, ge=0, le=100, description="Overall financial health score 0-100")
    health_status: str = Field(default="unknown", description="Health status: excellent, good, fair, poor, critical")
    currency: str = Field(default="USD")
    top_categories: list[str] = Field(default_factory=list, description="Top spending categories")


# --- Currency registry ---

SUPPORTED_CURRENCIES: dict[str, CurrencyInfo] = {
    "USD": CurrencyInfo(code="USD", name="US Dollar", symbol="$", decimal_places=2),
    "EUR": CurrencyInfo(code="EUR", name="Euro", symbol="€", decimal_places=2),
    "GBP": CurrencyInfo(code="GBP", name="British Pound", symbol="£", decimal_places=2),
    "JPY": CurrencyInfo(code="JPY", name="Japanese Yen", symbol="¥", decimal_places=0),
    "CAD": CurrencyInfo(code="CAD", name="Canadian Dollar", symbol="CA$", decimal_places=2),
    "AUD": CurrencyInfo(code="AUD", name="Australian Dollar", symbol="A$", decimal_places=2),
    "CHF": CurrencyInfo(code="CHF", name="Swiss Franc", symbol="CHF", decimal_places=2),
    "CNY": CurrencyInfo(code="CNY", name="Chinese Yuan", symbol="¥", decimal_places=2),
    "INR": CurrencyInfo(code="INR", name="Indian Rupee", symbol="₹", decimal_places=2),
    "BRL": CurrencyInfo(code="BRL", name="Brazilian Real", symbol="R$", decimal_places=2),
    "KRW": CurrencyInfo(code="KRW", name="South Korean Won", symbol="₩", decimal_places=0),
    "MXN": CurrencyInfo(code="MXN", name="Mexican Peso", symbol="MX$", decimal_places=2),
    "SGD": CurrencyInfo(code="SGD", name="Singapore Dollar", symbol="S$", decimal_places=2),
    "SEK": CurrencyInfo(code="SEK", name="Swedish Krona", symbol="kr", decimal_places=2),
    "NZD": CurrencyInfo(code="NZD", name="New Zealand Dollar", symbol="NZ$", decimal_places=2),
}


def format_currency(amount: float, currency: str = "USD") -> str:
    """Format an amount with the currency symbol."""
    info = SUPPORTED_CURRENCIES.get(currency, CurrencyInfo(code=currency, name=currency, symbol=currency, decimal_places=2))
    formatted = f"{amount:,.{info.decimal_places}f}"
    return f"{info.symbol}{formatted}"


# --- v0.7.0 Guardrail Webhook Models ---


class WebhookEvent(str, Enum):
    """Events that can trigger a webhook notification."""
    GUARDRAIL_WARN = "guardrail_warn"
    GUARDRAIL_BLOCK = "guardrail_block"
    GUARDRAIL_KILL = "guardrail_kill"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    KILL_SWITCH_RESET = "kill_switch_reset"
    PROJECTION_BREACH = "projection_breach"
    LOOP_DETECTED = "loop_detected"
    BUDGET_THRESHOLD = "budget_threshold"


class WebhookConfig(BaseModel):
    """A webhook endpoint registered to receive guardrail/budget notifications.

    When a guardrail triggers (warn/block/kill) or the kill switch activates,
    all matching webhooks receive a POST with the event details. This enables
    integration with Slack, Discord, PagerDuty, custom dashboards, etc.
    """
    id: str = Field(default_factory=lambda: f"WHK-{uuid.uuid4().hex[:8].upper()}")
    name: str = Field(min_length=1, description="Webhook name (e.g., 'Slack alerts')")
    url: str = Field(min_length=1, description="Webhook URL to POST to")
    events: list[WebhookEvent] = Field(
        default_factory=lambda: list(WebhookEvent),
        description="Events that trigger this webhook (default: all)",
    )
    # Optional secret for HMAC signing (X-Webhook-Signature header)
    secret: Optional[str] = Field(default=None, description="Secret for HMAC-SHA256 signing")
    # Filter by scope (None = all scopes)
    scope: Optional[GuardrailScope] = Field(default=None, description="Only fire for this scope (None = all)")
    scope_id: Optional[str] = Field(default=None, description="Only fire for this scope ID")
    enabled: bool = Field(default=True)
    # Retry config
    max_retries: int = Field(default=3, ge=0, le=10, description="Max delivery retries on failure")
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0, description="Request timeout")
    # Custom headers
    headers: dict[str, str] = Field(default_factory=dict, description="Custom headers to send")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WebhookDelivery(BaseModel):
    """Record of a single webhook delivery attempt."""
    id: str = Field(default_factory=lambda: f"WHD-{uuid.uuid4().hex[:8].upper()}")
    webhook_id: str = Field(description="Webhook config ID")
    event: WebhookEvent = Field(description="Event that triggered delivery")
    payload: dict = Field(default_factory=dict, description="Data sent to the webhook")
    success: bool = Field(default=False, description="Whether delivery succeeded (2xx response)")
    status_code: Optional[int] = Field(default=None, description="HTTP status code from response")
    response_body: Optional[str] = Field(default=None, description="Response body (truncated)")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    attempt: int = Field(default=1, ge=1, description="Attempt number (1 = first try)")
    delivered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = Field(default=0.0, description="Request duration in milliseconds")


class ProjectionIntegration(BaseModel):
    """Result of integrating spend projection into guardrail check.

    When check_guardrails uses projection, it doesn't just check current
    spend — it projects where spend will be at the end of the period and
    can proactively warn/block before the actual limit is reached.
    """
    enabled: bool = Field(default=False, description="Whether projection was used in this check")
    projected_spend_usd: Optional[float] = Field(default=None, description="Projected period-end spend")
    projected_percent: Optional[float] = Field(default=None, description="Projected percent of limit")
    projected_exceeds: bool = Field(default=False, description="Whether projection exceeds the limit")
    eta_minutes: Optional[float] = Field(default=None, description="ETA to limit at current rate")
    will_breach: bool = Field(default=False, description="Whether a guardrail will breach before period end")
    projection_confidence: float = Field(default=0.0, ge=0, le=1, description="Confidence in projection")


# --- Built-in Budget Templates ---


BUILTIN_BUDGET_TEMPLATES: list[BudgetTemplate] = [
    BudgetTemplate(
        id="TPL-API001",
        name="API Costs",
        description="Budget for API usage costs (LLM, cloud APIs, etc.)",
        category="api",
        default_limit=500.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=50, level=AlertLevel.INFO),
            AlertThreshold(percent=80, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[
            {"name": "API Daily Cap", "action": "block", "threshold_amount": 100.0},
        ],
        tags=["api", "cloud", "llm"],
        is_builtin=True,
    ),
    BudgetTemplate(
        id="TPL-COMPUTE",
        name="Compute Costs",
        description="Budget for compute infrastructure (servers, containers, serverless)",
        category="compute",
        default_limit=1000.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=60, level=AlertLevel.INFO),
            AlertThreshold(percent=85, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[
            {"name": "Compute Approval", "action": "block", "requires_approval_above": 200.0},
        ],
        tags=["compute", "infrastructure", "servers"],
        is_builtin=True,
    ),
    BudgetTemplate(
        id="TPL-SAAS",
        name="SaaS Subscriptions",
        description="Budget for SaaS tools and subscriptions",
        category="saas",
        default_limit=300.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=75, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[
            {"name": "SaaS Cap", "action": "warn", "threshold_amount": 250.0},
        ],
        tags=["saas", "subscriptions", "tools"],
        is_builtin=True,
    ),
    BudgetTemplate(
        id="TPL-STORAGE",
        name="Storage & Data",
        description="Budget for cloud storage, databases, and data transfer",
        category="storage",
        default_limit=200.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=70, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[],
        tags=["storage", "database", "data"],
        is_builtin=True,
    ),
    BudgetTemplate(
        id="TPL-AGENT",
        name="Full Agent Stack",
        description="Complete budget for an autonomous agent covering all categories",
        category="all",
        default_limit=2000.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=50, level=AlertLevel.INFO),
            AlertThreshold(percent=75, level=AlertLevel.WARNING),
            AlertThreshold(percent=90, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[
            {"name": "Large Expense Approval", "action": "approve", "requires_approval_above": 500.0},
        ],
        tags=["agent", "full-stack", "comprehensive"],
        is_builtin=True,
    ),
    BudgetTemplate(
        id="TPL-DATAPROC",
        name="Data Processing",
        description="Budget for data pipelines, ETL, and batch processing",
        category="data-processing",
        default_limit=800.0,
        period=BudgetPeriod.MONTHLY,
        currency="USD",
        suggested_alerts=[
            AlertThreshold(percent=60, level=AlertLevel.INFO),
            AlertThreshold(percent=85, level=AlertLevel.WARNING),
            AlertThreshold(percent=100, level=AlertLevel.CRITICAL),
        ],
        suggested_rules=[
            {"name": "Data Processing Cap", "action": "block", "threshold_amount": 750.0},
        ],
        tags=["data", "etl", "pipelines", "processing"],
        is_builtin=True,
    ),
]

# Rebuild models to resolve forward references
GuardrailDecision.model_rebuild()
