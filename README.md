<div align="center">

# Agent Budget

**Cost guardrails, spend optimization, and budget management for autonomous AI agents**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/nyx-builds/agent-budget/actions/workflows/ci.yml/badge.svg)](https://github.com/nyx-builds/agent-budget/actions/workflows/ci.yml)
[![Tests: 809](https://img.shields.io/badge/tests-809%20passing-brightgreen.svg)](#testing)
[![MCP](https://img.shields.io/badge/MCP-server-7c3aed)](https://modelcontextprotocol.io)
[![Version: 0.13.0](https://img.shields.io/badge/version-0.13.0-blue.svg)](#changelog)

[Features](#features) · [Install](#installation) · [Quick Start](#quick-start) · [MCP Setup](#mcp-server-setup) · [Python API](#python-api) · [Changelog](#changelog)

</div>

---

Stop runaway LLM costs before they happen. Agent Budget is an MCP server + CLI that gives autonomous AI agents **real-time cost guardrails**, **progressive spend throttling**, **anomaly detection**, **model cost optimization**, and full budget management — all local, no account required.

```
┌───────────┐     stdio      ┌───────────────┐     JSON      ┌──────────────┐
│   Agent   │ ─────────────▶ │ agent-budget  │ ────────────▶ │  Guardrail   │
│ (Claude,  │ ◀───────────── │  MCP server   │ ◀──────────── │   Engine     │
│  Cursor…) │  tool result   └───────────────┘  decision     └──────────────┘
└───────────┘                                                    │
                                                                 ▼
                                                    ALLOW / WARN / THROTTLE / BLOCK
```

## Why Agent Budget?

| Problem | Without Agent Budget | With Agent Budget |
|---------|---------------------|-------------------|
| **Runaway costs** | Agent loops burn $$ until you notice | Kill switch + loop detection blocks instantly |
| **Cliff-edge blocking** | Agent goes full-speed → hard stop | Progressive throttling degrades gracefully |
| **No spend visibility** | You find out at end of month | Real-time dashboards + burn forecasts |
| **Overpaying for models** | Using GPT-4 for everything | Model optimizer recommends cheaper alternatives |
| **No anomaly detection** | Slow leaks go unnoticed | Statistical anomaly detection flags outliers |

### How it compares

| Feature | Agent Budget | Floe-Labs/floe-guard | @three-ws/billing-mcp |
|---------|:---:|:---:|:---:|
| Cost guardrails (pre-flight) | ✅ | ✅ | ❌ |
| Progressive throttling (tiers) | ✅ | ❌ | ❌ |
| Kill switch | ✅ | ✅ | ❌ |
| Spend projection / burn forecast | ✅ | ❌ | ❌ |
| Loop detection | ✅ | ❌ | ❌ |
| Anomaly detection | ✅ | ❌ | ❌ |
| Multi-currency FX engine | ✅ | ❌ | ❌ |
| FX rate drift detection | ✅ | ❌ | ❌ |
| Model cost optimizer | ✅ | ❌ | ❌ |
| Budget management | ✅ | ❌ | ✅ |
| Reserve & settle (hold funds) | ✅ | ❌ | ❌ |
| Webhooks (Slack/Discord/PagerDuty) | ✅ | ❌ | ❌ |
| MCP server | ✅ | ✅ (CLI) | ✅ |
| REST API | ✅ | ❌ | ❌ |
| Tests | 809 | — | — |

## Features

### 🚨 Cost Guardrails & Kill Switch (v0.5.0)
- **Real-time pre-flight checks** — `check_guardrails()` before LLM calls: ALLOW / WARN / BLOCK decisions
- **Multi-scope guardrails** — Set limits per global, agent, model, budget, or task scope
- **Multiple limit types** — Daily, hourly, per-call, and monthly spend caps
- **Emergency kill switch** — Instantly block ALL LLM calls with auto-expire and override tokens
- **Cost alert events** — Track breaches separately; acknowledge & clear
- **Cooldown periods** — Block subsequent calls for N minutes after a breach
- **Priority ordering** — Higher-priority guardrails checked first; most restrictive wins

### 📉 Progressive Cost Throttling (v0.8.0)
- **Tiered spend control** — Graduated thresholds (60%, 75%, 90%) instead of binary cutoff
- **Cost-per-call limits** — Recommends max spend per call at each tier
- **Model downgrade suggestions** — "Switch to gpt-4o-mini" at 75% spend
- **Custom tiers** — Define your own threshold percentages and limits
- **Advisory → Hard cap** — 60% = advisory, 90% = blocks expensive calls

### 💰 Reserve & Settle Protocol (v0.9.0)
- **Concurrency-safe holds** — Reserve funds before an operation, settle or release after
- **Double-spend prevention** — Atomic reserve/settle ensures funds can't be spent twice
- **TTL-based expiry** — Reserves auto-expire and release funds if not settled
- **Partial settlement** — Settle for less than reserved; release the remainder

### 🔮 Spend Projection & Loop Detection (v0.6.0)
- **Burn forecast** — `project_spend()` predicts ETA-to-limit based on spend velocity
- **Multi-period projections** — Daily, hourly, and monthly with confidence scoring
- **Guardrail breach prediction** — Know if a guardrail will trigger *before* period ends
- **Loop detection** — Detect runaway agents making repeated similar LLM calls
- **Jaccard similarity** — Groups repeated operations by call signature
- **Auto-block** — Automatically block looping agents for N minutes

### 🔍 Spend Anomaly Detection (v0.10.0)
- **Statistical outlier detection** — Flag costs that deviate from historical patterns
- **Z-score based** — Configurable sensitivity thresholds
- **Per-agent / per-model baselines** — Learn normal spend patterns
- **Real-time alerts** — Fire webhook events when anomalies detected

### 🧮 Model Cost Optimizer (v0.11.0)
- **Compare models** — Cost-compare your current model against all alternatives
- **Smart recommendations** — Cheapest viable alternative with rationale + tier analysis
- **Savings projection** — Monthly savings estimate for model switches
- **Capability tiers** — High / medium / economy classification with fuzzy matching
- **Cheapest-for-tier** — Find the cheapest model at or above a capability level
- **30+ models** — Built-in pricing for OpenAI, Anthropic, Google, Meta, Mistral, Cohere

### 🔔 Guardrail Webhooks (v0.7.0)
- **Webhook notifications** — Fire on guardrail triggers, kill switch, projection breaches
- **Event filtering** — Subscribe to specific events (warn/block/kill/threshold/loop)
- **HMAC-SHA256 signing** — Optional secret for request signature verification
- **Retry with backoff** — Automatic retries on 5xx errors with exponential backoff
- **Slack/Discord/PagerDuty ready** — Standard JSON POST payloads work out-of-the-box

### 💱 Multi-Currency FX Engine (v0.13.0)
- **Exchange rate management** — Set custom rates for any currency pair; overrides take priority
- **Automatic inverse derivation** — Set EUR→USD, get USD→EUR for free
- **USD triangulation** — Derive cross-rates (EUR→GBP) from USD-anchored pairs
- **Static reference table** — 15 major currencies with mid-2025 estimates (zero dependencies)
- **Multi-currency summary** — Unify budgets, spending, income, and savings into one base currency
- **FX rate drift detection** — Snapshot rates, update them, then detect which pairs moved beyond a threshold
- **Financial impact computation** — Quantify the budget impact of FX drift on a specific exposure amount
- **Rate change history** — Automatic snapshots on rate overwrite; manual snapshots for periodic monitoring
- **CLI support** — `agent-budget fx set|get|list|delete|convert|summary|snapshot|history|drift|clear-history`

### 📊 Budget Management (v0.1.0–v0.4.0)
- **Budget tracking** — Create budgets with limits, periods, categories, rollover
- **Expense tracking** — Log expenses with categories, tags, vendor info, receipts
- **Recurring expenses** — Schedule payments (daily/weekly/monthly/quarterly/yearly)
- **Savings goals** — Track progress toward targets with auto-completion
- **Spending rules** — Block, warn, or require approval for expenses
- **Income tracking** — Multiple sources, recurring income templates
- **Cash flow analysis** — Income vs expenses, savings rate, burn rate, runway
- **Financial dashboard** — Health score (0-100), budget status, sustainability
- **Multi-currency** — 15+ currencies
- **Data export** — JSON, CSV, Markdown

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

# Log an expense
agent-budget expense add 25.50 --category api --description "OpenAI GPT-4 call" --vendor "OpenAI"

# Check budget status
agent-budget budget status

# Create a cost guardrail (block at $50/day globally)
agent-budget guardrail create "Daily Cap" global --daily-limit 50.0

# Check before an LLM call
agent-budget guardrail check --cost 0.05 --agent worker-bot --model gpt-4o

# Emergency stop
agent-budget kill-switch trigger "Budget blown — investigating"

# Optimize: find cheaper model
agent-budget optimize recommend --model gpt-4o --input-tokens 1000 --output-tokens 500
```

## MCP Server Setup

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "agent-budget": {
      "command": "uvx",
      "args": ["agent-budget", "serve"]
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agent-budget": {
      "command": "uvx",
      "args": ["agent-budget", "serve"]
    }
  }
}
```

### Continue

`~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: agent-budget
    command: uvx
    args: ["agent-budget", "serve"]
```

### Any MCP host

`command: uvx`, `args: ["agent-budget", "serve"]`

### Programmatic

```python
from agent_budget.mcp_server import mcp
mcp.run()
```

## MCP Tools

### Cost Guardrail Tools
| Tool | Description |
|------|-------------|
| `check_cost_guardrail` | **Pre-flight check before an LLM call** — returns ALLOW/WARN/THROTTLE/BLOCK |
| `create_cost_guardrail` | Create a spending limit (global/agent/model/budget/task scope) |
| `list_cost_guardrails` | List all guardrails |
| `delete_cost_guardrail` | Delete a guardrail |
| `trigger_kill_switch` | **Emergency stop** — blocks ALL LLM calls |
| `reset_kill_switch` | Reset (requires override token if set) |
| `get_kill_switch_status` | Check if kill switch is active |
| `enable_progressive_throttling` | Enable tiered spend control on a guardrail |
| `list_cost_alerts` | List cost alert events |

### Spend Projection & Loop Detection
| Tool | Description |
|------|-------------|
| `project_spend` | **Burn forecast** — predicts ETA-to-limit |
| `check_loop` | **Loop detection** — checks for repeated similar calls |
| `create_loop_config` | Configure loop detection parameters |
| `list_loop_configs` | List loop detection configs |
| `delete_loop_config` | Delete a loop detection config |

### Model Cost Optimizer (v0.11.0)
| Tool | Description |
|------|-------------|
| `compare_model_costs` | Compare current model against all alternatives |
| `recommend_model_switch` | Get cheapest viable alternative with rationale |
| `project_model_savings` | Monthly savings projection for a model switch |
| `estimate_call_cost` | Cost estimate for a model + token profile |
| `get_cheapest_model` | Find cheapest model at/above a capability tier |
| `get_model_pricing_leaderboard` | Browse models by cost |

### Reserve & Settle (v0.9.0)
| Tool | Description |
|------|-------------|
| `create_reservation` | Reserve funds before an operation |
| `settle_reservation` | Settle a reservation (full or partial) |
| `release_reservation` | Release an unsettled reservation |
| `list_reservations` | List active reservations |

### Budget & Expense Tools
| Tool | Description |
|------|-------------|
| `create_budget` / `list_budgets` / `get_budget` / `update_budget` / `delete_budget` | Budget CRUD |
| `process_budget_rollover` | Carry unspent budget forward |
| `get_budget_status` / `compare_budget_actual` | Budget vs. actual analysis |
| `add_expense` / `update_expense` / `list_expenses` / `get_expense` / `delete_expense` | Expense CRUD |
| `create_savings_goal` / `contribute_to_savings` / `withdraw_from_savings` | Savings goals |
| `create_spending_rule` / `check_expense_rules` | Spending rules |
| `add_recurring_expense` / `process_recurring_expenses` | Recurring expenses |
| `get_spending_forecast` / `get_spending_summary` | Analysis |
| `export_data` | Export to JSON/CSV/Markdown |

## How Guardrails Work

```
Agent wants to call GPT-4o
    │
    ▼
check_cost_guardrail(cost=$0.05, agent_id="worker-1", model_id="gpt-4o")
    │
    ▼
Guardrail engine checks in priority order:
  1. Kill switch active?  ──────────────────────▶  BLOCK (all calls)
  2. Per-call limit exceeded?  ─────────────────▶  BLOCK
  3. Progressive throttle tier active?  ────────▶  THROTTLE (advisory or block)
  4. Daily/hourly/monthly limit exceeded?  ─────▶  BLOCK
  5. Approaching a limit?  ─────────────────────▶  WARN
  6. All clear  ────────────────────────────────▶  ALLOW
    │
    ▼
Returns: GuardrailDecision {
    action: ALLOW | WARN | THROTTLE | BLOCK,
    reason: "...",
    suggestions: [...],
    throttle_tier: "60%" | "75%" | "90%" | null,
    max_recommended_cost_usd: ...,
    recommended_model: "gpt-4o-mini" | null,
}
    │
    ▼
Agent proceeds, adjusts, or stops
```

### Progressive Throttling Tiers

Instead of a cliff-edge (full speed → hard block), spend degrades gracefully:

| Spend Level | Max Cost/Call | Action | Model Recommendation |
|-------------|---------------|--------|---------------------|
| < 60% of limit | No restriction | ALLOW | — |
| 60% of limit | $0.50 | THROTTLE (advisory) | — |
| 75% of limit | $0.20 | THROTTLE (advisory) | Switch to gpt-4o-mini |
| 90% of limit | $0.05 | BLOCK if exceeded | Switch to gpt-4o-mini |

## Python API

```python
from agent_budget.service import BudgetService
from agent_budget.models import GuardrailScope, GuardrailAction

svc = BudgetService()

# Set up guardrails with progressive throttling
svc.create_guardrail(
    name="Daily agent cap",
    scope=GuardrailScope.AGENT,
    scope_id="worker-bot",
    daily_limit_usd=20.0,
    warn_at_percent=75,
    progressive_throttling=True,  # Enable tiered control
)

# Before each LLM call — check guardrails
decision = svc.check_guardrails(
    estimated_cost_usd=0.05,
    agent_id="worker-bot",
    model_id="gpt-4o",
)

if decision.action in (GuardrailAction.BLOCK, GuardrailAction.KILL):
    print(f"Blocked: {decision.reason}")
elif decision.action == GuardrailAction.THROTTLE:
    print(f"Throttled at {decision.throttle_tier}")
    print(f"Max recommended cost: ${decision.max_recommended_cost_usd}")
    if decision.recommended_model:
        print(f"Consider switching to: {decision.recommended_model}")
elif decision.action == GuardrailAction.WARN:
    print(f"Warning: {decision.reason}")
else:
    make_llm_call()  # Proceed

# Model optimization — find cheaper alternatives
from agent_budget.optimizer import ModelOptimizer

opt = ModelOptimizer()
recommendation = opt.recommend(
    current_model="gpt-4o",
    input_tokens=1000,
    output_tokens=500,
    monthly_calls=10000,
)
if recommendation:
    print(f"Switch to {recommendation.recommended_model}")
    print(f"Save {recommendation.savings_percent:.0f}% per call")
    print(f"Monthly savings: ${recommendation.projected_monthly_savings:.2f}")
    print(f"Rationale: {recommendation.rationale}")

# Reserve & settle — prevent double-spending in concurrent agents
reservation = svc.create_reservation(
    agent_id="worker-bot",
    amount_usd=5.00,
    scope=GuardrailScope.AGENT,
    scope_id="worker-bot",
    ttl_seconds=300,  # Auto-release after 5 min
)
# ... do the operation ...
svc.settle_reservation(reservation.id, actual_amount_usd=4.23)
# Remaining $0.77 is released back

# Burn forecast
proj = svc.project_spend(
    scope=GuardrailScope.AGENT,
    scope_id="worker-bot",
    period="daily",
)
if proj.will_breach_guardrail:
    print(f"⚠️ Will breach in {proj.eta_minutes_to_limit:.0f} min")
    print(f"Recommendation: {proj.recommendation}")

# Emergency kill switch
svc.trigger_kill_switch(reason="Security incident", override_token="admin-only")
# All check_guardrails() now return action=KILL
svc.reset_kill_switch(override_token="admin-only")
```

## Data Storage

All data is stored in JSON files under `~/.agent-budget/` (or the directory specified by the `AGENT_BUDGET_DIR` environment variable). No external database required.

## Supported Currencies

USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR, BRL, KRW, MXN, SGD, SEK, NZD

## Testing

```bash
# Run all 677 tests
uv venv .venv
VIRTUAL_ENV=$(pwd)/.venv uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Architecture

```
src/agent_budget/
├── models.py        # Pydantic models (Budget, Expense, Guardrail, ThrottleTier, ...)
├── store.py         # JSON file persistence
├── service.py       # Business logic (BudgetService)
├── llm_costs.py     # Model pricing catalog + cost calculation
├── optimizer.py     # Model cost optimizer (compare, recommend, project)
├── mcp_server.py    # MCP server with 40+ tools
├── api_server.py    # REST API (FastAPI)
└── cli.py           # CLI (click + rich)
```

## Changelog

### v0.13.0 — Multi-Currency FX Engine + Drift Detection 💱
- **Exchange rate management** — set custom rates, auto-derive inverses and cross-rates
- **Triangulation via USD** — derive any pair from USD-anchored rates
- **Built-in static reference table** — 15 major currencies with mid-2025 estimates
- **Multi-currency summary** — unify budgets, spending, income, and savings into one base currency
- **FX rate drift detection** — snapshot rates, detect which pairs moved beyond a configurable threshold
- **Financial impact computation** — quantify the budget impact of FX drift on specific exposure amounts
- **Rate change history** — automatic snapshots on overwrite + manual periodic snapshots
- **10 new MCP tools** (6 FX core + 4 drift detection)
- **10 new CLI commands** (`fx set|get|list|delete|convert|summary|snapshot|history|drift|clear-history`)
- 96 new tests (809 total)

### v0.12.0 — Session Cost Importer 📥
- Import from Hermes state.db, JSONL transcripts, request dumps
- Auto-sync imported costs to budgets
- 3 new MCP tools

### v0.11.0 — Model Cost Optimizer 🧮
- Model comparison, recommendation, savings projection
- Capability tier classification (high/medium/economy)
- 6 new MCP tools, 5 new API endpoints
- 39 new tests (677 total)

### v0.10.0 — Spend Anomaly Detection 🔍
- Statistical outlier detection (z-score based)
- Per-agent/model baselines
- Anomaly webhook events

### v0.9.0 — Reserve & Settle Protocol 💰
- Concurrency-safe fund reservation
- TTL-based expiry, partial settlement
- Double-spend prevention

### v0.8.0 — Progressive Cost Throttling 📉
- Tiered spend control (60%/75%/90%)
- Model downgrade suggestions
- Custom tiers support

### v0.7.0 — Guardrail Webhooks 🔔
- Webhook notifications with HMAC signing
- Event filtering, retry with backoff
- Slack/Discord/PagerDuty ready

### v0.6.0 — Spend Projection & Loop Detection 🔮
- Burn forecast with ETA-to-limit
- Runaway loop detection with auto-block

### v0.5.0 — Cost Guardrails & Kill Switch 🚨
- Real-time pre-flight checks
- Multi-scope, multi-period guardrails
- Emergency kill switch

### v0.4.0 — Income & Financial Dashboard 📊
- Income tracking, cash flow, burn rate
- Financial health scoring, REST API

### v0.2.0 — Savings & Rules 💰
- Savings goals, budget rollover
- Spending rules, expense receipts

### v0.1.0 — Initial Release 🎉
- Budget management, expense tracking
- Multi-currency, data export

## License

MIT
