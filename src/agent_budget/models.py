"""Pydantic models for Agent Budget."""
from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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
            month = d.month + 1
            year = d.year
            if month > 12:
                month = 1
                year += 1
            return d.replace(month=month, year=year)
        elif self.frequency == RecurringFrequency.QUARTERLY:
            month = d.month + 3
            year = d.year
            if month > 12:
                month -= 12
                year += 1
            return d.replace(month=month, year=year)
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
