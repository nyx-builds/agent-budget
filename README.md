# Agent Budget

MCP server + CLI for autonomous agents to manage budgets, track expenses, control spending, set savings goals, enforce spending rules, **enforce real-time cost guardrails with kill switch**, **predict spend breaches with burn forecasting**, and **detect runaway loops** to prevent runaway LLM costs.

## Features

### v0.6.0 — Spend Projection & Loop Detection 🔮
- **Burn Forecast** — `project_spend()` predicts when limits will be hit based on spend velocity; returns ETA-to-limit, projected spend, and recommendations
- **Multi-period Projections** — Daily, hourly, and monthly spend projections with confidence scoring
- **Guardrail Breach Prediction** — Know if a guardrail will trigger *before* period ends, with time-to-limit estimates
- **Loop Detection** — Detect runaway agents making repeated identical/similar LLM calls
- **Call Pattern Analysis** — Jaccard similarity on call signatures (model + token buckets) groups repeated operations
- **Auto-Block** — Automatically block looping agents for N minutes when detected
- **Configurable Detection Windows** — Set time window, repeat threshold, similarity threshold, and minimum cost
- **Agent/Model Scoping** — Apply loop detection globally or to specific agents/models

### v0.5.0 — Cost Guardrails & Kill Switch 🚨
- **Real-time Pre-flight Checks** — `check_guardrails()` before LLM calls: allow / warn / block decisions
- **Multi-scope Guardrails** — Set limits per global, agent, model, budget, or task scope
- **Multiple Limit Types** — Daily, hourly, per-call, and monthly spend caps
- **Emergency Kill Switch** — Instantly block ALL LLM calls; optional auto-expire and override tokens
- **Cost Alert Events** — Track guardrail breaches separately from budget alerts; acknowledge & clear
- **Cost-saving Suggestions** — When blocked, agents get actionable recommendations
- **Cooldown Periods** — After a breach, block subsequent calls for N minutes
- **Priority Ordering** — Higher-priority guardrails checked first; most restrictive decision wins

### v0.4.0
- **Income Tracking** — Log income from multiple sources, recurring income templates
- **Cash Flow Analysis** — Income vs expenses, savings rate, expense ratio
- **Burn Rate** — Monthly burn, net burn, runway months, sustainability scoring
- **Financial Dashboard** — Health score (0-100), budget status, savings, cash flow, burn rate
- **REST API** — Full HTTP API with OpenAPI docs
- **CSV Import** — Bulk import expenses from CSV files

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

### Cost Guardrail Tools (v0.5.0)
- `check_cost_guardrail` — **Pre-flight check before an LLM call** — returns allow/warn/block
- `create_cost_guardrail` — Create a spending limit guardrail (global/agent/model/budget/task scope)
- `list_cost_guardrails` — List all guardrails
- `delete_cost_guardrail` — Delete a guardrail
- `trigger_kill_switch` — **Emergency stop** — blocks ALL LLM calls immediately
- `reset_kill_switch` — Reset the kill switch (requires override token if set)
- `get_kill_switch_status` — Check if kill switch is active
- `list_cost_alerts` — List cost alert events from guardrails

### Spend Projection & Loop Detection Tools (v0.6.0)
- `project_spend` — **Burn forecast** — projects spend and predicts ETA-to-limit for any scope/period
- `check_loop` — **Loop detection** — checks if an agent is making repeated similar calls
- `create_loop_config` — Configure loop detection (window, threshold, similarity, auto-block)
- `list_loop_configs` — List loop detection configurations
- `delete_loop_config` — Delete a loop detection configuration

## Cost Guardrails & Kill Switch (v0.5.0)

Cost guardrails are the key safety feature for autonomous agents. Unlike spending rules (which check after an expense is added), guardrails are checked **before** an LLM call is made.

### How it works

```
Agent wants to call GPT-4o
    ↓
Calls check_cost_guardrail(cost=$0.05, agent_id="worker-1", model_id="gpt-4o")
    ↓
Guardrail engine checks:
  1. Kill switch active? → If yes, BLOCK
  2. Per-call limit? → If exceeded, BLOCK
  3. Daily/hourly/monthly limits? → If exceeded, BLOCK; if approaching, WARN
    ↓
Returns decision: ALLOW / WARN / BLOCK + reason + suggestions
    ↓
Agent proceeds or adjusts behavior
```

### CLI Examples

```bash
# Create a global daily cap
agent-budget guardrail create "Daily LLM Cap" global --daily-limit 50.0

# Create per-agent limit with early warning
agent-budget guardrail create "Agent A Cap" agent --scope-id agent-A --daily-limit 10.0 --warn-at 70

# Create per-model limit (block expensive models over budget)
agent-budget guardrail create "GPT-4o Cap" model --scope-id gpt-4o --daily-limit 20.0

# Per-call limit to prevent expensive single calls
agent-budget guardrail create "Per-call Cap" global --per-call-limit 0.50

# Check before an LLM call
agent-budget guardrail check --cost 0.05 --agent agent-A --model gpt-4o

# Emergency stop
agent-budget kill-switch trigger "Budget blown — investigating"

# Reset (with token if set)
agent-budget kill-switch reset --token my-secret
```

### Python Usage

```python
from agent_budget.service import BudgetService

svc = BudgetService()

# Set up guardrails
svc.create_guardrail(
    name="Daily agent cap",
    scope=GuardrailScope.AGENT,
    scope_id="worker-bot",
    daily_limit_usd=20.0,
    warn_at_percent=75,
)

# Before each LLM call — check guardrails
decision = svc.check_guardrails(
    estimated_cost_usd=0.05,
    agent_id="worker-bot",
    model_id="gpt-4o",
)

if decision.allowed:
    # Proceed with the call
    if decision.action == GuardrailAction.WARN:
        print(f"Warning: {decision.reason}")
    make_llm_call()
else:
    # Blocked — respect the decision
    print(f"Blocked: {decision.reason}")
    for suggestion in decision.suggestions:
        print(f"  → {suggestion}")

# Emergency kill switch
svc.trigger_kill_switch(reason="Security incident", override_token="admin-only")
# All check_guardrails() calls now return action=KILL

# Reset when safe
svc.reset_kill_switch(override_token="admin-only")
```

## Spend Projection & Loop Detection (v0.6.0)

### Burn Forecast

Predicts when you'll hit guardrail limits based on current spend velocity. Agents
can use this to proactively slow down *before* a guardrail hard-blocks them.

```bash
# Check daily burn forecast (global scope)
agent-budget projection check

# Check for a specific agent
agent-budget projection check --scope agent --scope-id worker-bot --period hourly

# Monthly projection
agent-budget projection check --period monthly
```

```python
from agent_budget.service import BudgetService
from agent_budget.models import GuardrailScope

svc = BudgetService()

# Get burn forecast before it's too late
proj = svc.project_spend(scope=GuardrailScope.AGENT, scope_id="worker-bot", period="daily")
print(f"Current: ${proj.current_spend_usd:.2f}")
print(f"Projected: ${proj.projected_spend_usd:.2f}")
if proj.will_breach_guardrail:
    print(f"⚠️ Will breach! ETA: {proj.eta_minutes_to_limit:.0f} min")
    print(f"Recommendation: {proj.recommendation}")
```

### Loop Detection

Detects runaway agents making repeated identical/similar LLM calls — the most
common way agents burn budget in infinite retry loops.

```bash
# Create a loop detection config
agent-budget loop create --name "Global Loop Guard" --window 10 --threshold 5

# With auto-block (blocks agent for 30 min when detected)
agent-budget loop create --name "Aggressive Guard" --threshold 3 --auto-block 30

# Check for loops
agent-budget loop check --agent-id worker-bot

# List configs
agent-budget loop list
```

```python
svc = BudgetService()

# Configure loop detection
svc.create_loop_config(
    name="Global Loop Guard",
    window_minutes=10,
    repeat_threshold=5,
    similarity_threshold=0.9,
    auto_block_minutes=30,
)

# Check if an agent is looping
result = svc.check_loop(agent_id="worker-bot")
if result.detected:
    print(f"LOOP DETECTED: {result.call_count} similar calls")
    print(f"Cost burned: ${result.cumulative_cost_usd:.4f}")
    print(f"Recommendation: {result.recommendation}")
```

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
