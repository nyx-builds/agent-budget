# Agent Budget

MCP server + CLI for autonomous agents to manage budgets, track expenses, control spending, set savings goals, and enforce spending rules.

## Features

### v0.2.0
- **Savings Goals** — Track progress toward savings targets with auto-completion
- **Budget Rollover** — Carry unspent budget forward to the next period
- **Spending Rules** — Block, warn, or require approval for expenses
- **Expense Updates** — Modify existing expenses (amount, vendor, receipt, status)
- **Expense Receipts** — Attach receipt URLs, mark expenses as reimbursable
- **Alert Threshold Updates** — Customize budget alert thresholds
- **Deprecation Fixes** — `datetime.utcnow()` → `datetime.now(timezone.utc)`

### v0.1.0
- **Budget Management** — Create, update, delete budgets with spending limits
- **Expense Tracking** — Log expenses with categories, tags, and vendor info
- **Recurring Expenses** — Schedule recurring payments (daily/weekly/monthly/quarterly/yearly)
- **Budget vs. Actual** — Compare spending against budget limits
- **Spending Forecasts** — Project future spending based on history
- **Alert System** — Automatic alerts at configurable thresholds
- **Multi-Currency** — Support for 15+ currencies
- **Data Export** — Export to JSON, CSV, or Markdown

## Installation

```bash
pip install agent-budget
```

Or with uv:

```bash
uv pip install agent-budget
```

## Quick Start

### CLI

```bash
# Create a monthly budget
agent-budget budget create "API Costs" --limit 500 --period monthly --category api

# Create a budget with rollover
agent-budget budget create "Infrastructure" --limit 1000 --period monthly --rollover --rollover-cap 200

# Log an expense
agent-budget expense add 25.50 --category api --description "OpenAI GPT-4 call" --vendor "OpenAI"

# Log a reimbursable expense with receipt
agent-budget expense add 99.00 --category saas --vendor "GitHub" --reimbursable --receipt-url "https://receipts.example.com/gh-001"

# Update an expense
agent-budget expense update EXP-ABC12345 --amount 125.00 --vendor "AWS"

# Check budget status
agent-budget budget status

# Process budget rollovers
agent-budget budget rollover

# Create a savings goal
agent-budget savings create "Emergency Fund" --target 10000 --target-date 2027-01-01

# Contribute to a savings goal
agent-budget savings contribute SAV-ABC12345 --amount 500 --note "Monthly deposit"

# Withdraw from a savings goal
agent-budget savings withdraw SAV-ABC12345 --amount 200 --note "Emergency repair"

# Add a spending rule
agent-budget rule add "API Cap" --category api --action block --threshold-amount 500

# Add an approval rule
agent-budget rule add "Large Expenses" --category infra --action block --approval-above 100

# Check if an expense would violate rules
agent-budget rule check --amount 150 --category infra

# Set up a recurring expense
agent-budget recurring add "AWS Hosting" --amount 99 --category infra --frequency monthly

# Get spending summary
agent-budget summary --this-month

# Get spending forecast
agent-budget forecast --months 3

# Check alerts
agent-budget alerts

# Export data
agent-budget export --format json
```

### MCP Server

Start the MCP server for integration with AI agents:

```bash
agent-budget serve
```

Or use it programmatically:

```python
from agent_budget.mcp_server import mcp
mcp.run()
```

## MCP Tools

### Budget Tools
- `create_budget` — Create a new budget
- `list_budgets` — List all budgets
- `get_budget` — Get budget details
- `update_budget` — Update a budget's settings
- `delete_budget` — Delete a budget
- `process_budget_rollover` — Carry unspent budget forward
- `get_budget_status` — Check actual vs. budgeted spending
- `compare_budget_actual` — Detailed budget comparison
- `update_alert_thresholds` — Customize alert thresholds

### Expense Tools
- `add_expense` — Log a new expense
- `update_expense` — Update an existing expense
- `list_expenses` — List expenses with filters (category, vendor, reimbursable, etc.)
- `get_expense` — Get expense details
- `delete_expense` — Delete an expense

### Savings Goal Tools
- `create_savings_goal` — Create a savings target
- `list_savings_goals` — List savings goals
- `get_savings_goal` — Get goal details with progress
- `contribute_to_savings` — Add a contribution
- `withdraw_from_savings` — Withdraw from a goal
- `update_savings_goal` — Update a goal
- `delete_savings_goal` — Delete a goal

### Spending Rule Tools
- `create_spending_rule` — Create a spending control rule
- `list_spending_rules` — List spending rules
- `check_expense_rules` — Check if an expense would violate rules
- `update_spending_rule` — Update a rule
- `delete_spending_rule` — Delete a rule

### Recurring Expense Tools
- `add_recurring_expense` — Set up a recurring expense
- `list_recurring_expenses` — List recurring templates
- `process_recurring_expenses` — Generate expenses from due templates

### Analysis Tools
- `get_spending_forecast` — Project future spending
- `get_spending_summary` — Spending by category
- `get_alerts` — Check budget alerts
- `clear_alerts` — Clear alerts
- `export_data` — Export all data
- `list_currencies` — List supported currencies

## Python API

```python
from agent_budget.service import BudgetService
from agent_budget.store import BudgetStore
from agent_budget.models import BudgetPeriod, RecurringFrequency, SpendingRuleAction

# Initialize
svc = BudgetService(BudgetStore())

# Create a budget with rollover
budget = svc.create_budget(
    name="API Costs",
    limit=500,
    period=BudgetPeriod.MONTHLY,
    category="api",
    rollover_enabled=True,
    rollover_cap=100,
)

# Add an expense
expense = svc.add_expense(
    amount=25.50,
    category="api",
    description="OpenAI GPT-4 call",
    vendor="OpenAI",
    budget_id=budget.id,
)

# Update an expense
svc.update_expense(expense.id, amount=30.00, receipt_url="https://receipt.example.com/123")

# Create a savings goal
goal = svc.create_savings_goal(
    name="Emergency Fund",
    target_amount=10000,
    target_date=date(2027, 1, 1),
)

# Contribute to the goal
goal = svc.contribute_to_savings(goal.id, amount=500, note="Monthly deposit")

# Create a spending rule
rule = svc.create_spending_rule(
    name="API Cap",
    category="api",
    action=SpendingRuleAction.BLOCK,
    threshold_amount=500,
)

# Check budget status
status = svc.get_budget_status(budget.id)
print(f"Used {status.percent_used}% of budget")

# Process budget rollovers
results = svc.process_all_rollovers()

# Get spending forecast
forecasts = svc.get_spending_forecast(months=3)
```

## Data Storage

All data is stored in JSON files under `~/.agent-budget/` (or the directory specified by the `AGENT_BUDGET_DIR` environment variable). No external database required.

## Supported Currencies

USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR, BRL, KRW, MXN, SGD, SEK, NZD

## License

MIT
