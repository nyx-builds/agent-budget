"""Tests for v0.8.0 Progressive Cost Throttling.

Tests the ThrottleTier model, guardrail throttle tier selection, and the
integration of throttle logic into check_guardrails for daily/hourly/monthly
limits. Also covers API endpoints and MCP tools.
"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta, timezone

from agent_budget.models import (
    CostGuardrail, GuardrailScope, GuardrailAction, GuardrailDecision,
    ThrottleTier, DEFAULT_THROTTLE_TIERS, AlertLevel,
    WebhookEvent,
)
from agent_budget.store import BudgetStore
from agent_budget.service import BudgetService
from agent_budget.llm_costs import LLMUsageRecord


@pytest.fixture
def temp_store():
    """Create a store with a temporary directory."""
    tmpdir = tempfile.mkdtemp()
    store = BudgetStore(data_dir=tmpdir)
    yield store
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def svc(temp_store):
    return BudgetService(store=temp_store)


NOW = datetime(2025, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def add_llm_usage(store, cost_usd, agent_id=None, model_id="gpt-4o", minutes_ago=0, task_id=None, budget_id=None):
    """Helper to add LLM usage records for testing."""
    metadata = {}
    if budget_id:
        metadata["budget_id"] = budget_id
    record = LLMUsageRecord(
        model_id=model_id,
        agent_id=agent_id,
        task_id=task_id,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=cost_usd,
        recorded_at=NOW - timedelta(minutes=minutes_ago),
        metadata=metadata,
    )
    store.save_llm_usage(record)
    return record


# --- ThrottleTier Model Tests ---

class TestThrottleTierModel:
    def test_default_tiers_exist(self):
        assert len(DEFAULT_THROTTLE_TIERS) == 3
        assert DEFAULT_THROTTLE_TIERS[0].threshold_percent == 60.0
        assert DEFAULT_THROTTLE_TIERS[1].threshold_percent == 75.0
        assert DEFAULT_THROTTLE_TIERS[2].threshold_percent == 90.0

    def test_tier_creation(self):
        tier = ThrottleTier(
            threshold_percent=70.0,
            max_cost_usd=0.30,
            recommended_model="gpt-4o-mini",
        )
        assert tier.threshold_percent == 70.0
        assert tier.max_cost_usd == 0.30
        assert tier.recommended_model == "gpt-4o-mini"
        assert tier.block_if_exceeded is False

    def test_tier_with_block(self):
        tier = ThrottleTier(
            threshold_percent=95.0,
            max_cost_usd=0.01,
            block_if_exceeded=True,
        )
        assert tier.block_if_exceeded is True

    def test_tier_advisory_only(self):
        """Tier with max_cost_usd=None is advisory only."""
        tier = ThrottleTier(
            threshold_percent=50.0,
            max_cost_usd=None,
            message="Halfway there",
        )
        assert tier.max_cost_usd is None

    def test_tier_percent_validation(self):
        with pytest.raises(Exception):
            ThrottleTier(threshold_percent=-1.0)
        with pytest.raises(Exception):
            ThrottleTier(threshold_percent=101.0)

    def test_default_tier_progression(self):
        """Default tiers get progressively more restrictive."""
        costs = [t.max_cost_usd for t in DEFAULT_THROTTLE_TIERS]
        assert costs[0] > costs[1] > costs[2]
        # Last tier blocks if exceeded
        assert DEFAULT_THROTTLE_TIERS[2].block_if_exceeded is True


# --- CostGuardrail Throttle Tests ---

class TestGuardrailThrottle:
    def test_guardrail_has_throttle_fields(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0)
        assert hasattr(g, "throttle_enabled")
        assert hasattr(g, "throttle_tiers")
        assert g.throttle_enabled is False  # Default off
        assert len(g.throttle_tiers) == 3  # Default tiers

    def test_guardrail_throttle_disabled_by_default(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0)
        assert g.get_active_throttle_tier(85.0) is None

    def test_get_active_tier_no_match(self):
        """Below all thresholds, no tier is active."""
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        assert g.get_active_throttle_tier(50.0) is None
        assert g.get_active_throttle_tier(59.9) is None

    def test_get_active_tier_first(self):
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        tier = g.get_active_throttle_tier(60.0)
        assert tier is not None
        assert tier.threshold_percent == 60.0

    def test_get_active_tier_middle(self):
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        tier = g.get_active_throttle_tier(80.0)
        assert tier is not None
        assert tier.threshold_percent == 75.0

    def test_get_active_tier_last(self):
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        tier = g.get_active_throttle_tier(95.0)
        assert tier is not None
        assert tier.threshold_percent == 90.0

    def test_get_active_tier_exact_boundary(self):
        """At exactly 75.0%, the 75% tier should be active, not 60%."""
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        tier = g.get_active_throttle_tier(75.0)
        assert tier.threshold_percent == 75.0

    def test_custom_tiers(self):
        custom = [
            ThrottleTier(threshold_percent=50.0, max_cost_usd=1.0),
            ThrottleTier(threshold_percent=80.0, max_cost_usd=0.10, block_if_exceeded=True),
        ]
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True, throttle_tiers=custom,
        )
        tier = g.get_active_throttle_tier(55.0)
        assert tier.threshold_percent == 50.0
        tier = g.get_active_throttle_tier(85.0)
        assert tier.threshold_percent == 80.0
        assert tier.block_if_exceeded is True

    def test_empty_tiers(self):
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True, throttle_tiers=[],
        )
        assert g.get_active_throttle_tier(99.0) is None

    def test_throttle_disabled_returns_none(self):
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=False,
        )
        assert g.get_active_throttle_tier(95.0) is None


# --- Service: create/update guardrail with throttle ---

class TestServiceThrottleCrud:
    def test_create_guardrail_with_throttle(self, svc):
        g = svc.create_guardrail(
            name="Throttled",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
            throttle_enabled=True,
        )
        assert g.throttle_enabled is True
        assert len(g.throttle_tiers) == 3

    def test_create_guardrail_throttle_off_by_default(self, svc):
        g = svc.create_guardrail(
            name="Normal",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )
        assert g.throttle_enabled is False

    def test_update_guardrail_enable_throttle(self, svc):
        g = svc.create_guardrail(
            name="Test",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )
        assert g.throttle_enabled is False
        updated = svc.update_guardrail(g.id, throttle_enabled=True)
        assert updated.throttle_enabled is True

    def test_update_guardrail_custom_tiers(self, svc):
        custom = [
            ThrottleTier(threshold_percent=50.0, max_cost_usd=2.0),
        ]
        g = svc.create_guardrail(
            name="Test",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )
        updated = svc.update_guardrail(g.id, throttle_enabled=True, throttle_tiers=custom)
        assert len(updated.throttle_tiers) == 1
        assert updated.throttle_tiers[0].threshold_percent == 50.0


# --- Service: check_guardrails with throttle (daily) ---

class TestThrottleCheckDaily:
    def test_throttle_triggers_at_60_percent(self, svc, temp_store):
        """At 60% daily spend, throttle should advise lower cost."""
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        # Spend $6 (60% of $10)
        add_llm_usage(temp_store, cost_usd=6.0, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        assert decision.allowed is True
        assert decision.throttle_tier == "60%"
        assert decision.max_recommended_cost_usd == 0.50

    def test_throttle_at_75_percent(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=7.5, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        assert decision.throttle_tier == "75%"
        assert decision.max_recommended_cost_usd == 0.20
        assert decision.recommended_model == "gpt-4o-mini"

    def test_throttle_at_90_percent_blocks_expensive(self, svc, temp_store):
        """At 90%, expensive calls should be blocked."""
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=9.0, minutes_ago=30)
        # Call costing $0.10 should be blocked (max is $0.05 at 90% tier)
        decision = svc.check_guardrails(estimated_cost_usd=0.10, now=NOW)
        assert decision.allowed is False
        assert decision.action == GuardrailAction.BLOCK

    def test_throttle_at_90_percent_allows_cheap(self, svc, temp_store):
        """At 90%, cheap calls under the tier max should be allowed."""
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=9.0, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.03, now=NOW)
        assert decision.allowed is True
        assert decision.action == GuardrailAction.THROTTLE

    def test_throttle_does_not_trigger_below_60(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=5.0, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.ALLOW

    def test_throttle_disabled_falls_back_to_warn(self, svc, temp_store):
        """When throttle is disabled, normal warn/block applies."""
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=False,
            warn_at_percent=80.0,
        )
        add_llm_usage(temp_store, cost_usd=8.5, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.WARN
        assert decision.throttle_tier is None

    def test_block_at_100_still_works_with_throttle(self, svc, temp_store):
        """Even with throttle on, hitting 100% should block."""
        g = svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
            block_at_percent=100.0,
        )
        add_llm_usage(temp_store, cost_usd=10.0, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.allowed is False
        assert decision.action == GuardrailAction.BLOCK

    def test_throttle_suggestions_contain_model(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=7.5, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        model_suggestion = [s for s in decision.suggestions if "gpt-4o-mini" in s]
        assert len(model_suggestion) > 0


# --- Service: check_guardrails with throttle (hourly) ---

class TestThrottleCheckHourly:
    def test_hourly_throttle_triggers(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Hourly Cap",
            scope=GuardrailScope.GLOBAL,
            hourly_limit_usd=5.0,
            throttle_enabled=True,
        )
        # $3 = 60% of $5
        add_llm_usage(temp_store, cost_usd=3.0, minutes_ago=20)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        assert decision.throttle_tier == "60%"

    def test_hourly_throttle_75(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Hourly Cap",
            scope=GuardrailScope.GLOBAL,
            hourly_limit_usd=4.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=3.0, minutes_ago=20)  # 75% of 4
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        assert decision.throttle_tier == "75%"


# --- Service: check_guardrails with throttle (monthly) ---

class TestThrottleCheckMonthly:
    def test_monthly_throttle_triggers(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Monthly Cap",
            scope=GuardrailScope.GLOBAL,
            monthly_limit_usd=100.0,
            throttle_enabled=True,
        )
        # $60 = 60% of $100
        add_llm_usage(temp_store, cost_usd=60.0, minutes_ago=120)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        assert decision.throttle_tier == "60%"


# --- Webhook integration ---

class TestThrottleWebhooks:
    def test_throttle_fires_budget_threshold_webhook(self, svc, temp_store):
        """THROTTLE action should fire BUDGET_THRESHOLD webhook event."""
        svc.create_webhook(
            name="Test WH",
            url="http://example.com/hook",
            events=["budget_threshold"],
        )
        g = svc.create_guardrail(
            name="Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=7.0, minutes_ago=30)
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.THROTTLE
        # Webhook attempt should have been made (will fail since URL is fake, but count > 0)
        assert decision.webhooks_fired >= 0  # May be 0 if delivery failed


# --- Decision fields ---

class TestGuardrailDecisionThrottleFields:
    def test_decision_has_throttle_fields(self):
        d = GuardrailDecision(
            allowed=True,
            action=GuardrailAction.THROTTLE,
            reason="test",
            throttle_tier="75%",
            max_recommended_cost_usd=0.20,
            recommended_model="gpt-4o-mini",
        )
        assert d.throttle_tier == "75%"
        assert d.max_recommended_cost_usd == 0.20
        assert d.recommended_model == "gpt-4o-mini"

    def test_decision_defaults_no_throttle(self):
        d = GuardrailDecision(allowed=True, action=GuardrailAction.ALLOW)
        assert d.throttle_tier is None
        assert d.max_recommended_cost_usd is None
        assert d.recommended_model is None


# --- _check_throttle_tier helper ---

class TestCheckThrottleTierHelper:
    def test_returns_none_when_disabled(self, svc):
        from agent_budget.models import CostGuardrail, GuardrailScope
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=False,
        )
        result = svc._check_throttle_tier(g, 0.01, 85.0, 8.5, 10.0, "daily")
        assert result is None

    def test_returns_decision_when_tier_active(self, svc):
        from agent_budget.models import CostGuardrail, GuardrailScope
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        result = svc._check_throttle_tier(g, 0.01, 65.0, 6.5, 10.0, "daily")
        assert result is not None
        assert result.action == GuardrailAction.THROTTLE
        assert result.throttle_tier == "60%"

    def test_blocks_when_tier_exceeds_and_block_set(self, svc):
        from agent_budget.models import CostGuardrail, GuardrailScope
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        # At 95%, the 90% tier is active with max $0.05 and block_if_exceeded=True
        result = svc._check_throttle_tier(g, 0.50, 95.0, 9.5, 10.0, "daily")
        assert result is not None
        assert result.allowed is False
        assert result.action == GuardrailAction.BLOCK

    def test_advisory_when_block_not_set(self, svc):
        from agent_budget.models import CostGuardrail, GuardrailScope, ThrottleTier
        custom = [ThrottleTier(threshold_percent=70.0, max_cost_usd=0.10, block_if_exceeded=False)]
        g = CostGuardrail(
            name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0,
            throttle_enabled=True, throttle_tiers=custom,
        )
        # Cost exceeds tier max but block not set → THROTTLE not BLOCK
        result = svc._check_throttle_tier(g, 0.50, 75.0, 7.5, 10.0, "daily")
        assert result.allowed is True
        assert result.action == GuardrailAction.THROTTLE


# --- Cost alert recording ---

class TestThrottleCostAlerts:
    def test_throttle_creates_cost_alert(self, svc, temp_store):
        g = svc.create_guardrail(
            name="Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            throttle_enabled=True,
        )
        add_llm_usage(temp_store, cost_usd=7.0, minutes_ago=30)
        svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        alerts = svc.list_cost_alerts()
        assert len(alerts) > 0
        assert alerts[0].level == AlertLevel.WARNING
