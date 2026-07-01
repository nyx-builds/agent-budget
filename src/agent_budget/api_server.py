"""REST API server for Agent Budget.

Provides a full HTTP API for budget management, expense tracking, income,
cash flow, burn rate, savings goals, spending rules, analytics, and templates.

Run standalone:
    python -m agent_budget.api_server

Or via CLI:
    agent-budget api --host 0.0.0.0 --port 8100

Endpoints:
    GET    /health                       Health check
    GET    /                             API info

    # Budgets
    POST   /budgets                      Create a budget
    GET    /budgets                      List budgets
    GET    /budgets/{budget_id}          Get a budget
    PUT    /budgets/{budget_id}          Update a budget
    DELETE /budgets/{budget_id}          Delete a budget
    GET    /budgets/{budget_id}/status   Budget status
    POST   /budgets/rollover             Process budget rollover

    # Expenses
    POST   /expenses                     Add an expense
    GET    /expenses                     List expenses (with filters)
    GET    /expenses/{expense_id}        Get an expense
    PUT    /expenses/{expense_id}        Update an expense
    DELETE /expenses/{expense_id}        Delete an expense

    # Income
    POST   /income                       Add income
    GET    /income                       List income (with filters)
    GET    /income/summary               Income summary by source
    GET    /income/{income_id}           Get income
    PUT    /income/{income_id}           Update income
    DELETE /income/{income_id}           Delete income

    # Recurring Income
    POST   /recurring-income             Create recurring income
    GET    /recurring-income             List recurring income
    POST   /recurring-income/process     Process due recurring income

    # Recurring Expenses
    POST   /recurring-expenses           Create recurring expense
    GET    /recurring-expenses           List recurring expenses
    POST   /recurring-expenses/process   Process due recurring expenses

    # Savings Goals
    POST   /savings-goals                Create a savings goal
    GET    /savings-goals                List savings goals
    GET    /savings-goals/{goal_id}      Get a savings goal
    PUT    /savings-goals/{goal_id}      Update a savings goal
    POST   /savings-goals/{goal_id}/contribute   Contribute to a goal
    POST   /savings-goals/{goal_id}/withdraw     Withdraw from a goal
    DELETE /savings-goals/{goal_id}      Delete a savings goal

    # Spending Rules
    POST   /spending-rules               Create a spending rule
    GET    /spending-rules               List spending rules
    DELETE /spending-rules/{rule_id}     Delete a spending rule

    # Analytics & Reports
    GET    /analytics/cash-flow          Cash flow analysis
    GET    /analytics/burn-rate          Burn rate and runway
    GET    /analytics/dashboard          Financial health dashboard
    GET    /analytics/spending-summary   Spending summary
    GET    /analytics/forecast           Spending forecast
    GET    /analytics/trends             Spending trends
    GET    /analytics/breakdown          Category breakdown
    GET    /analytics/compare-periods    Period comparison
    GET    /analytics/budget-vs-actual   Budget vs actual comparison

    # Budget Templates
    GET    /templates                    List budget templates
    GET    /templates/{template_id}      Get a template
    POST   /templates                    Create a template
    POST   /templates/{template_id}/instantiate   Instantiate a template

    # Alerts
    GET    /alerts                       List alerts
    DELETE /alerts                       Clear alerts

    # Data
    GET    /export                       Export all data (JSON)
    POST   /import/csv                   Import expenses from CSV
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .service import BudgetService
from .store import BudgetStore
from .models import (
    BudgetPeriod,
    RecurringFrequency,
    IncomeStatus,
    ExpenseStatus,
    SpendingRuleAction,
    SavingsGoalStatus,
)

__version__ = "0.4.0"

# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------

_service: Optional[BudgetService] = None


def get_service() -> BudgetService:
    global _service
    if _service is None:
        data_dir = os.environ.get("AGENT_BUDGET_DATA_DIR", str(Path.home() / ".agent-budget"))
        _service = BudgetService(BudgetStore(data_dir=data_dir))
    return _service


def set_service(svc: BudgetService) -> None:
    """Override the global service (used in tests)."""
    global _service
    _service = svc


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateBudgetRequest(BaseModel):
    name: str
    limit: float
    period: str = "monthly"
    category: Optional[str] = None
    currency: str = "USD"
    rollover_enabled: bool = False
    rollover_cap: Optional[float] = None


class UpdateBudgetRequest(BaseModel):
    name: Optional[str] = None
    limit: Optional[float] = None
    period: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    rollover_enabled: Optional[bool] = None
    rollover_cap: Optional[float] = None


class AddExpenseRequest(BaseModel):
    amount: float
    category: str
    description: str = ""
    expense_date: Optional[str] = None
    tags: Optional[list[str]] = None
    currency: str = "USD"
    budget_id: Optional[str] = None
    vendor: Optional[str] = None
    receipt_url: Optional[str] = None
    reimbursable: bool = False


class UpdateExpenseRequest(BaseModel):
    amount: Optional[float] = None
    category: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    vendor: Optional[str] = None
    receipt_url: Optional[str] = None
    reimbursable: Optional[bool] = None


class AddIncomeRequest(BaseModel):
    amount: float
    source: str
    description: str = ""
    income_date: Optional[str] = None
    tags: Optional[list[str]] = None
    currency: str = "USD"
    status: str = "received"
    invoice_ref: Optional[str] = None


class UpdateIncomeRequest(BaseModel):
    amount: Optional[float] = None
    source: Optional[str] = None
    description: Optional[str] = None
    income_date: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    invoice_ref: Optional[str] = None


class CreateRecurringIncomeRequest(BaseModel):
    name: str
    amount: float
    source: str
    frequency: str = "monthly"
    description: str = ""
    currency: str = "USD"
    tags: Optional[list[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CreateRecurringExpenseRequest(BaseModel):
    name: str
    amount: float
    category: str
    frequency: str = "monthly"
    description: str = ""
    currency: str = "USD"
    tags: Optional[list[str]] = None
    budget_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CreateSavingsGoalRequest(BaseModel):
    name: str
    target_amount: float
    currency: str = "USD"
    target_date: Optional[str] = None
    category: Optional[str] = None
    description: str = ""


class UpdateSavingsGoalRequest(BaseModel):
    name: Optional[str] = None
    target_amount: Optional[float] = None
    target_date: Optional[str] = None
    description: Optional[str] = None


class ContributeRequest(BaseModel):
    amount: float
    note: str = ""


class CreateSpendingRuleRequest(BaseModel):
    name: str
    category: str
    action: str = Field(default="warn", description="'warn', 'block', 'alert'")
    threshold_amount: Optional[float] = None
    threshold_percent: Optional[float] = None
    budget_id: Optional[str] = None
    requires_approval_above: Optional[float] = None
    description: str = ""


class CreateTemplateRequest(BaseModel):
    name: str
    category: str
    default_limit: float
    period: str = "monthly"
    description: str = ""
    currency: str = "USD"


class InstantiateTemplateRequest(BaseModel):
    name: Optional[str] = None
    limit: Optional[float] = None
    currency: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _err(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Budget API",
        description="Budget management, expense tracking, income, cash flow, and financial analytics for autonomous agents.",
        version=__version__,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Root / health --------------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def root():
        return {
            "name": "Agent Budget API",
            "version": __version__,
            "docs": "/docs",
            "endpoints": [
                "/budgets", "/expenses", "/income", "/recurring-income",
                "/recurring-expenses", "/savings-goals", "/spending-rules",
                "/analytics/cash-flow", "/analytics/burn-rate",
                "/analytics/dashboard", "/analytics/forecast",
                "/templates", "/alerts", "/export", "/import/csv",
            ],
        }

    # -- Budgets --------------------------------------------------------

    @app.post("/budgets", status_code=201)
    def create_budget(body: CreateBudgetRequest):
        svc = get_service()
        try:
            budget = svc.create_budget(
                name=body.name,
                limit=body.limit,
                period=BudgetPeriod(body.period),
                category=body.category,
                currency=body.currency,
                rollover_enabled=body.rollover_enabled,
                rollover_cap=body.rollover_cap,
            )
            return budget.model_dump(mode="json")
        except (ValueError, KeyError) as e:
            raise _err(400, str(e))

    @app.get("/budgets")
    def list_budgets(active_only: bool = Query(default=True)):
        svc = get_service()
        budgets = svc.store.list_budgets(active_only=active_only)
        return [b.model_dump(mode="json") for b in budgets]

    @app.get("/budgets/{budget_id}")
    def get_budget(budget_id: str):
        svc = get_service()
        budget = svc.get_budget(budget_id)
        if not budget:
            raise _err(404, f"Budget {budget_id} not found")
        return budget.model_dump(mode="json")

    @app.put("/budgets/{budget_id}")
    def update_budget(budget_id: str, body: UpdateBudgetRequest):
        svc = get_service()
        try:
            period = BudgetPeriod(body.period) if body.period else None
            budget = svc.update_budget(
                budget_id,
                name=body.name,
                limit=body.limit,
                period=period,
                category=body.category,
                active=body.active,
                rollover_enabled=body.rollover_enabled,
                rollover_cap=body.rollover_cap,
            )
            return budget.model_dump(mode="json")
        except ValueError as e:
            raise _err(404, str(e))

    @app.delete("/budgets/{budget_id}")
    def delete_budget(budget_id: str):
        svc = get_service()
        if not svc.delete_budget(budget_id):
            raise _err(404, f"Budget {budget_id} not found")
        return {"deleted": budget_id}

    @app.get("/budgets/{budget_id}/status")
    def budget_status(budget_id: str):
        svc = get_service()
        try:
            status = svc.get_budget_status(budget_id)
            return status.model_dump(mode="json")
        except ValueError as e:
            raise _err(404, str(e))

    @app.post("/budgets/rollover")
    def process_rollover(budget_id: Optional[str] = None):
        svc = get_service()
        if budget_id:
            rollover = svc.process_budget_rollover(budget_id)
            if rollover:
                return rollover.model_dump(mode="json")
            return {"message": "No rollover processed"}
        results = []
        for b in svc.store.list_budgets(active_only=True):
            rollover = svc.process_budget_rollover(b.id)
            if rollover:
                results.append(rollover.model_dump(mode="json"))
        return {"processed": len(results), "rollovers": results}

    # -- Expenses -------------------------------------------------------

    @app.post("/expenses", status_code=201)
    def add_expense(body: AddExpenseRequest):
        svc = get_service()
        try:
            expense = svc.add_expense(
                amount=body.amount,
                category=body.category,
                description=body.description,
                expense_date=_parse_date(body.expense_date),
                tags=body.tags,
                currency=body.currency,
                budget_id=body.budget_id,
                vendor=body.vendor,
                receipt_url=body.receipt_url,
                reimbursable=body.reimbursable,
            )
            return expense.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/expenses")
    def list_expenses(
        category: Optional[str] = None,
        budget_id: Optional[str] = None,
        vendor: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = Query(default=100, le=1000),
    ):
        svc = get_service()
        expenses = svc.store.list_expenses(
            category=category,
            budget_id=budget_id,
            vendor=vendor,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            status=status,
        )
        return [e.model_dump(mode="json") for e in expenses[:limit]]

    @app.get("/expenses/{expense_id}")
    def get_expense(expense_id: str):
        svc = get_service()
        expense = svc.get_expense(expense_id)
        if not expense:
            raise _err(404, f"Expense {expense_id} not found")
        return expense.model_dump(mode="json")

    @app.put("/expenses/{expense_id}")
    def update_expense(expense_id: str, body: UpdateExpenseRequest):
        svc = get_service()
        try:
            expense = svc.update_expense(
                expense_id,
                amount=body.amount,
                category=body.category,
                description=body.description,
                tags=body.tags,
                status=body.status,
                vendor=body.vendor,
                receipt_url=body.receipt_url,
                reimbursable=body.reimbursable,
            )
            return expense.model_dump(mode="json")
        except ValueError as e:
            raise _err(404 if "not found" in str(e).lower() else 400, str(e))

    @app.delete("/expenses/{expense_id}")
    def delete_expense(expense_id: str):
        svc = get_service()
        if not svc.delete_expense(expense_id):
            raise _err(404, f"Expense {expense_id} not found")
        return {"deleted": expense_id}

    # -- Income ---------------------------------------------------------

    @app.post("/income", status_code=201)
    def add_income(body: AddIncomeRequest):
        svc = get_service()
        try:
            income = svc.add_income(
                amount=body.amount,
                source=body.source,
                description=body.description,
                income_date=_parse_date(body.income_date),
                tags=body.tags,
                currency=body.currency,
                status=IncomeStatus(body.status),
                invoice_ref=body.invoice_ref,
            )
            return income.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/income")
    def list_income(
        source: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = Query(default=100, le=1000),
    ):
        svc = get_service()
        incomes = svc.list_income(
            source=source,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            status=status,
        )
        return [i.model_dump(mode="json") for i in incomes[:limit]]

    @app.get("/income/summary")
    def income_summary(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        svc = get_service()
        summary = svc.get_income_summary(
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        return {"by_source": summary, "total": sum(summary.values())}

    @app.get("/income/{income_id}")
    def get_income(income_id: str):
        svc = get_service()
        income = svc.get_income(income_id)
        if not income:
            raise _err(404, f"Income {income_id} not found")
        return income.model_dump(mode="json")

    @app.put("/income/{income_id}")
    def update_income(income_id: str, body: UpdateIncomeRequest):
        svc = get_service()
        try:
            inc_status = IncomeStatus(body.status) if body.status else None
            income = svc.update_income(
                income_id,
                amount=body.amount,
                source=body.source,
                description=body.description,
                income_date=_parse_date(body.income_date),
                tags=body.tags,
                status=inc_status,
                invoice_ref=body.invoice_ref,
            )
            return income.model_dump(mode="json")
        except ValueError as e:
            raise _err(404 if "not found" in str(e).lower() else 400, str(e))

    @app.delete("/income/{income_id}")
    def delete_income(income_id: str):
        svc = get_service()
        if not svc.delete_income(income_id):
            raise _err(404, f"Income {income_id} not found")
        return {"deleted": income_id}

    # -- Recurring Income -----------------------------------------------

    @app.post("/recurring-income", status_code=201)
    def create_recurring_income(body: CreateRecurringIncomeRequest):
        svc = get_service()
        try:
            rec = svc.add_recurring_income(
                name=body.name,
                amount=body.amount,
                source=body.source,
                frequency=RecurringFrequency(body.frequency),
                description=body.description,
                currency=body.currency,
                tags=body.tags,
                start_date=_parse_date(body.start_date),
                end_date=_parse_date(body.end_date),
            )
            return rec.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/recurring-income")
    def list_recurring_income(active_only: bool = Query(default=True)):
        svc = get_service()
        items = svc.list_recurring_income(active_only=active_only)
        return [r.model_dump(mode="json") for r in items]

    @app.post("/recurring-income/process")
    def process_recurring_income():
        svc = get_service()
        generated = svc.process_recurring_income()
        return {
            "processed": len(generated),
            "total_amount": sum(i.amount for i in generated),
            "income_ids": [i.id for i in generated],
        }

    # -- Recurring Expenses ---------------------------------------------

    @app.post("/recurring-expenses", status_code=201)
    def create_recurring_expense(body: CreateRecurringExpenseRequest):
        svc = get_service()
        try:
            rec = svc.add_recurring_expense(
                name=body.name,
                amount=body.amount,
                category=body.category,
                frequency=RecurringFrequency(body.frequency),
                description=body.description,
                currency=body.currency,
                tags=body.tags,
                budget_id=body.budget_id,
                start_date=_parse_date(body.start_date),
                end_date=_parse_date(body.end_date),
            )
            return rec.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/recurring-expenses")
    def list_recurring_expenses(active_only: bool = Query(default=True)):
        svc = get_service()
        items = svc.store.list_recurring_expenses(active_only=active_only)
        return [r.model_dump(mode="json") for r in items]

    @app.post("/recurring-expenses/process")
    def process_recurring_expenses():
        svc = get_service()
        generated = svc.process_recurring_expenses()
        return {
            "processed": len(generated),
            "total_amount": sum(e.amount for e in generated),
            "expense_ids": [e.id for e in generated],
        }

    # -- Savings Goals --------------------------------------------------

    @app.post("/savings-goals", status_code=201)
    def create_savings_goal(body: CreateSavingsGoalRequest):
        svc = get_service()
        try:
            goal = svc.create_savings_goal(
                name=body.name,
                target_amount=body.target_amount,
                currency=body.currency,
                target_date=_parse_date(body.target_date),
                category=body.category,
                description=body.description,
            )
            return goal.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/savings-goals")
    def list_savings_goals(status: Optional[str] = None):
        svc = get_service()
        goals = svc.store.list_savings_goals()
        if status:
            goals = [g for g in goals if g.status == status]
        return [g.model_dump(mode="json") for g in goals]

    @app.get("/savings-goals/{goal_id}")
    def get_savings_goal(goal_id: str):
        svc = get_service()
        goal = svc.store.get_savings_goal(goal_id)
        if not goal:
            raise _err(404, f"Savings goal {goal_id} not found")
        return goal.model_dump(mode="json")

    @app.put("/savings-goals/{goal_id}")
    def update_savings_goal(goal_id: str, body: UpdateSavingsGoalRequest):
        svc = get_service()
        try:
            goal = svc.update_savings_goal(
                goal_id,
                name=body.name,
                target_amount=body.target_amount,
                target_date=_parse_date(body.target_date),
                description=body.description,
            )
            return goal.model_dump(mode="json")
        except ValueError as e:
            raise _err(404, str(e))

    @app.post("/savings-goals/{goal_id}/contribute")
    def contribute_to_savings(goal_id: str, body: ContributeRequest):
        svc = get_service()
        try:
            goal = svc.contribute_to_savings(goal_id, amount=body.amount, note=body.note)
            return goal.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.post("/savings-goals/{goal_id}/withdraw")
    def withdraw_from_savings(goal_id: str, body: ContributeRequest):
        svc = get_service()
        try:
            goal = svc.withdraw_from_savings(goal_id, amount=body.amount, note=body.note)
            return goal.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.delete("/savings-goals/{goal_id}")
    def delete_savings_goal(goal_id: str):
        svc = get_service()
        if not svc.delete_savings_goal(goal_id):
            raise _err(404, f"Savings goal {goal_id} not found")
        return {"deleted": goal_id}

    # -- Spending Rules -------------------------------------------------

    @app.post("/spending-rules", status_code=201)
    def create_spending_rule(body: CreateSpendingRuleRequest):
        svc = get_service()
        try:
            rule = svc.create_spending_rule(
                name=body.name,
                category=body.category,
                action=SpendingRuleAction(body.action),
                threshold_amount=body.threshold_amount,
                threshold_percent=body.threshold_percent,
                budget_id=body.budget_id,
                requires_approval_above=body.requires_approval_above,
                description=body.description,
            )
            return rule.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/spending-rules")
    def list_spending_rules(enabled_only: bool = Query(default=True)):
        svc = get_service()
        rules = svc.store.list_spending_rules()
        if enabled_only:
            rules = [r for r in rules if r.enabled]
        return [r.model_dump(mode="json") for r in rules]

    @app.delete("/spending-rules/{rule_id}")
    def delete_spending_rule(rule_id: str):
        svc = get_service()
        if not svc.delete_spending_rule(rule_id):
            raise _err(404, f"Spending rule {rule_id} not found")
        return {"deleted": rule_id}

    # -- Analytics & Reports --------------------------------------------

    @app.get("/analytics/cash-flow")
    def cash_flow(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        svc = get_service()
        flow = svc.get_cash_flow(
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        return flow.model_dump(mode="json")

    @app.get("/analytics/burn-rate")
    def burn_rate(months: int = Query(default=3, ge=1, le=24)):
        svc = get_service()
        try:
            burn = svc.get_burn_rate(months=months)
            return burn.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/analytics/dashboard")
    def dashboard():
        svc = get_service()
        dash = svc.get_financial_dashboard()
        return dash.model_dump(mode="json")

    @app.get("/analytics/spending-summary")
    def spending_summary(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        svc = get_service()
        today = date.today()
        end = _parse_date(end_date) or today
        start = _parse_date(start_date) or end.replace(day=1)
        summary = svc.get_category_summary(start_date=start, end_date=end)
        return {
            "start_date": str(start),
            "end_date": str(end),
            "total": round(sum(summary.values()), 2),
            "by_category": summary,
        }

    @app.get("/analytics/forecast")
    def forecast(
        months: int = Query(default=3, ge=1, le=12),
        category: Optional[str] = None,
        budget_id: Optional[str] = None,
    ):
        svc = get_service()
        forecasts = svc.get_spending_forecast(
            months=months, category=category, budget_id=budget_id,
        )
        return [f.model_dump(mode="json") for f in forecasts]

    @app.get("/analytics/trends")
    def trends(
        category: Optional[str] = None,
        period_type: str = Query(default="monthly"),
    ):
        svc = get_service()
        result = svc.get_spending_trends(category=category, period_type=period_type)
        return [t.model_dump(mode="json") for t in result]

    @app.get("/analytics/breakdown")
    def breakdown(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        top_n: int = Query(default=10, ge=1, le=100),
    ):
        svc = get_service()
        result = svc.get_category_breakdown(
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
            top_n=top_n,
        )
        return [b.model_dump(mode="json") for b in result]

    @app.get("/analytics/compare-periods")
    def compare_periods(
        period_a_start: str = Query(...),
        period_a_end: str = Query(...),
        period_b_start: str = Query(...),
        period_b_end: str = Query(...),
    ):
        svc = get_service()
        try:
            result = svc.compare_periods(
                period_a_start=_parse_date(period_a_start) or date.today(),
                period_a_end=_parse_date(period_a_end) or date.today(),
                period_b_start=_parse_date(period_b_start) or date.today(),
                period_b_end=_parse_date(period_b_end) or date.today(),
            )
            return result.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.get("/analytics/budget-vs-actual")
    def budget_vs_actual(budget_id: Optional[str] = None):
        svc = get_service()
        if budget_id:
            comp = svc.get_budget_status(budget_id)
            return comp.model_dump(mode="json")
        # Return all active budget comparisons
        results = []
        for b in svc.store.list_budgets(active_only=True):
            try:
                comp = svc.get_budget_status(b.id)
                results.append(comp.model_dump(mode="json"))
            except ValueError:
                pass
        return results

    # -- Budget Templates -----------------------------------------------

    @app.get("/templates")
    def list_templates(category: Optional[str] = None):
        svc = get_service()
        templates = svc.list_budget_templates(category=category)
        return [t.model_dump(mode="json") for t in templates]

    @app.get("/templates/{template_id}")
    def get_template(template_id: str):
        svc = get_service()
        template = svc.get_budget_template(template_id)
        if not template:
            raise _err(404, f"Template {template_id} not found")
        return template.model_dump(mode="json")

    @app.post("/templates", status_code=201)
    def create_template(body: CreateTemplateRequest):
        svc = get_service()
        try:
            template = svc.create_budget_template(
                name=body.name,
                category=body.category,
                default_limit=body.default_limit,
                period=BudgetPeriod(body.period),
                description=body.description,
                currency=body.currency,
            )
            return template.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    @app.post("/templates/{template_id}/instantiate")
    def instantiate_template(template_id: str, body: Optional[InstantiateTemplateRequest] = None):
        svc = get_service()
        name = body.name if body else None
        limit = body.limit if body else None
        currency = body.currency if body else None
        try:
            budget = svc.instantiate_budget_template(
                template_id, name=name, limit=limit, currency=currency,
            )
            return budget.model_dump(mode="json")
        except ValueError as e:
            raise _err(400, str(e))

    # -- Alerts ---------------------------------------------------------

    @app.get("/alerts")
    def list_alerts(budget_id: Optional[str] = None):
        svc = get_service()
        alerts = svc.store.list_alerts(budget_id=budget_id)
        return [a.model_dump(mode="json") for a in alerts]

    @app.delete("/alerts")
    def clear_alerts(budget_id: Optional[str] = None):
        svc = get_service()
        cleared = svc.store.clear_alerts(budget_id=budget_id)
        return {"cleared": cleared}

    # -- Data I/O -------------------------------------------------------

    @app.get("/export")
    def export_data(format: str = Query(default="json")):
        svc = get_service()
        data = svc.export_data(format=format)
        if format == "json":
            return json.loads(data)
        return Response(content=data, media_type="application/json")

    @app.post("/import/csv")
    async def import_csv(
        file: UploadFile = File(...),
        category: Optional[str] = None,
        skip_duplicates: bool = True,
    ):
        svc = get_service()
        content = await file.read()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as f:
            f.write(content)
            tmp_path = f.name
        try:
            result = svc.import_csv(
                file_path=tmp_path,
                category=category,
                skip_duplicates=skip_duplicates,
            )
            return result.model_dump(mode="json")
        finally:
            os.unlink(tmp_path)

    # -- Error handler --------------------------------------------------

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


# Singleton used by uvicorn
app = create_app()


def run_server(host: str = "0.0.0.0", port: int = 8100):
    """Run the REST API server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
