"""Tests for v0.5.0 Cost Guardrails: real-time pre-flight checks, kill switch, cost alerts."""

import pytest
import os
import tempfile
from datetime import datetime, timedelta, timezone

from agent_budget.models import (
    CostGuardrail, GuardrailScope, GuardrailAction, GuardrailDecision,
    KillSwitch, CostAlertEvent, AlertLevel,
)
from agent_budget.store import BudgetStore
from agent_budget.service import BudgetService
from agent_budget.llm_costs import LLMUsageRecord, PriceCatalog


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


# --- Model Tests ---

class TestGuardrailModels:
    def test_guardrail_defaults(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0)
        assert g.id.startswith("GDR-")
        assert g.enabled is True
        assert g.warn_at_percent == 80.0
        assert g.block_at_percent == 100.0

    def test_guardrail_matches_global(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0)
        assert g.matches(GuardrailScope.GLOBAL)
        assert g.matches(GuardrailScope.GLOBAL, "any-id")

    def test_guardrail_matches_agent(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.AGENT, scope_id="agent-1", daily_limit_usd=10.0)
        assert g.matches(GuardrailScope.AGENT, "agent-1")
        assert not g.matches(GuardrailScope.AGENT, "agent-2")
        assert not g.matches(GuardrailScope.MODEL, "agent-1")

    def test_guardrail_disabled_doesnt_match(self):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10.0, enabled=False)
        assert not g.matches(GuardrailScope.GLOBAL)

    def test_kill_switch_default_inactive(self):
        ks = KillSwitch()
        assert not ks.is_active()

    def test_kill_switch_active(self):
        ks = KillSwitch(active=True, reason="test", triggered_at=NOW)
        assert ks.is_active(NOW)

    def test_kill_switch_expired(self):
        past = NOW - timedelta(minutes=30)
        ks = KillSwitch(active=True, reason="test", triggered_at=past, expires_at=past + timedelta(minutes=10))
        assert not ks.is_active(NOW)

    def test_kill_switch_not_expired(self):
        ks = KillSwitch(active=True, reason="test", triggered_at=NOW, expires_at=NOW + timedelta(minutes=10))
        assert ks.is_active(NOW)


# --- Store Tests ---

class TestGuardrailStore:
    def test_save_and_list_guardrails(self, temp_store):
        g1 = CostGuardrail(name="global cap", scope=GuardrailScope.GLOBAL, daily_limit_usd=100, priority=1)
        g2 = CostGuardrail(name="agent cap", scope=GuardrailScope.AGENT, scope_id="a1", daily_limit_usd=50, priority=5)
        temp_store.save_guardrail(g1)
        temp_store.save_guardrail(g2)

        guardrails = temp_store.list_guardrails()
        assert len(guardrails) == 2
        # Should be sorted by priority descending
        assert guardrails[0].name == "agent cap"

    def test_get_guardrail(self, temp_store):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        temp_store.save_guardrail(g)
        retrieved = temp_store.get_guardrail(g.id)
        assert retrieved is not None
        assert retrieved.name == "test"

    def test_get_guardrail_not_found(self, temp_store):
        assert temp_store.get_guardrail("GDR-NONEXIST") is None

    def test_update_guardrail(self, temp_store):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        temp_store.save_guardrail(g)
        g.daily_limit_usd = 25
        g.name = "updated"
        temp_store.save_guardrail(g)

        retrieved = temp_store.get_guardrail(g.id)
        assert retrieved.daily_limit_usd == 25
        assert retrieved.name == "updated"

    def test_delete_guardrail(self, temp_store):
        g = CostGuardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        temp_store.save_guardrail(g)
        assert temp_store.delete_guardrail(g.id) is True
        assert temp_store.get_guardrail(g.id) is None

    def test_delete_guardrail_not_found(self, temp_store):
        assert temp_store.delete_guardrail("GDR-NONEXIST") is False

    def test_list_enabled_only(self, temp_store):
        g1 = CostGuardrail(name="enabled", scope=GuardrailScope.GLOBAL, daily_limit_usd=10, enabled=True)
        g2 = CostGuardrail(name="disabled", scope=GuardrailScope.GLOBAL, daily_limit_usd=10, enabled=False)
        temp_store.save_guardrail(g1)
        temp_store.save_guardrail(g2)

        all_g = temp_store.list_guardrails()
        enabled_g = temp_store.list_guardrails(enabled_only=True)
        assert len(all_g) == 2
        assert len(enabled_g) == 1

    def test_kill_switch_default(self, temp_store):
        ks = temp_store.get_kill_switch()
        assert ks.active is False

    def test_save_and_get_kill_switch(self, temp_store):
        ks = KillSwitch(active=True, reason="emergency", triggered_at=NOW)
        temp_store.save_kill_switch(ks)
        retrieved = temp_store.get_kill_switch()
        assert retrieved.active is True
        assert retrieved.reason == "emergency"

    def test_cost_alert_save_and_list(self, temp_store):
        alert = CostAlertEvent(
            guardrail_id="GDR-TEST",
            scope=GuardrailScope.GLOBAL,
            level=AlertLevel.WARNING,
            message="Approaching limit",
            current_spend_usd=8.0,
            limit_usd=10.0,
        )
        temp_store.save_cost_alert(alert)
        alerts = temp_store.list_cost_alerts()
        assert len(alerts) == 1
        assert alerts[0].message == "Approaching limit"

    def test_acknowledge_cost_alert(self, temp_store):
        alert = CostAlertEvent(message="test")
        temp_store.save_cost_alert(alert)
        acked = temp_store.acknowledge_cost_alert(alert.id)
        assert acked is not None
        assert acked.acknowledged is True

    def test_clear_cost_alerts(self, temp_store):
        alert1 = CostAlertEvent(message="test1")
        alert2 = CostAlertEvent(message="test2")
        temp_store.save_cost_alert(alert1)
        temp_store.save_cost_alert(alert2)
        cleared = temp_store.clear_cost_alerts()
        assert cleared == 2
        assert len(temp_store.list_cost_alerts()) == 0


# --- Service: Guardrail CRUD ---

class TestGuardrailService:
    def test_create_guardrail(self, svc):
        g = svc.create_guardrail(
            name="Daily cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=50.0,
        )
        assert g.id.startswith("GDR-")
        assert g.daily_limit_usd == 50.0
        assert g.scope == GuardrailScope.GLOBAL

    def test_create_guardrail_requires_limit(self, svc):
        with pytest.raises(ValueError, match="At least one limit"):
            svc.create_guardrail(name="test", scope=GuardrailScope.GLOBAL)

    def test_create_guardrail_with_all_limits(self, svc):
        g = svc.create_guardrail(
            name="comprehensive",
            scope=GuardrailScope.AGENT,
            scope_id="agent-1",
            daily_limit_usd=100,
            hourly_limit_usd=10,
            per_call_limit_usd=1,
            monthly_limit_usd=2000,
            warn_at_percent=70,
            block_at_percent=90,
        )
        assert g.daily_limit_usd == 100
        assert g.hourly_limit_usd == 10
        assert g.per_call_limit_usd == 1
        assert g.monthly_limit_usd == 2000

    def test_update_guardrail(self, svc):
        g = svc.create_guardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=50)
        updated = svc.update_guardrail(g.id, daily_limit_usd=100, enabled=False)
        assert updated.daily_limit_usd == 100
        assert updated.enabled is False

    def test_update_guardrail_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.update_guardrail("GDR-NONEXIST", daily_limit_usd=100)

    def test_delete_guardrail(self, svc):
        g = svc.create_guardrail(name="test", scope=GuardrailScope.GLOBAL, daily_limit_usd=50)
        assert svc.delete_guardrail(g.id) is True
        assert svc.get_guardrail(g.id) is None


# --- Service: Pre-flight Check (check_guardrails) ---

class TestCheckGuardrails:
    def test_no_guardrails_allows(self, svc):
        decision = svc.check_guardrails(estimated_cost_usd=0.05, now=NOW)
        assert decision.allowed is True
        assert decision.action == GuardrailAction.ALLOW

    def test_within_daily_limit_allows(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=100)
        decision = svc.check_guardrails(estimated_cost_usd=5.0, now=NOW)
        assert decision.allowed is True
        assert decision.action == GuardrailAction.ALLOW

    def test_approaching_daily_limit_warns(self, svc):
        svc.create_guardrail(
            name="daily",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10,
            warn_at_percent=80,
        )
        # Spend 7.5 USD (75%), projected 8.0 (80%) → warn
        add_llm_usage(svc.store, 7.5)
        decision = svc.check_guardrails(estimated_cost_usd=0.5, now=NOW)
        assert decision.allowed is True
        assert decision.action == GuardrailAction.WARN
        assert "approaching" in decision.reason.lower()

    def test_exceeding_daily_limit_blocks(self, svc):
        svc.create_guardrail(
            name="daily",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10,
        )
        add_llm_usage(svc.store, 9.0)
        decision = svc.check_guardrails(estimated_cost_usd=2.0, now=NOW)
        assert decision.allowed is False
        assert decision.action == GuardrailAction.BLOCK
        assert decision.guardrail_id is not None
        assert decision.percent_used >= 100

    def test_per_call_limit_blocks(self, svc):
        svc.create_guardrail(
            name="per call",
            scope=GuardrailScope.GLOBAL,
            per_call_limit_usd=0.50,
        )
        decision = svc.check_guardrails(estimated_cost_usd=1.0, now=NOW)
        assert decision.allowed is False
        assert decision.action == GuardrailAction.BLOCK
        assert "Per-call" in decision.reason

    def test_per_call_limit_allows_when_within(self, svc):
        svc.create_guardrail(
            name="per call",
            scope=GuardrailScope.GLOBAL,
            per_call_limit_usd=1.0,
        )
        decision = svc.check_guardrails(estimated_cost_usd=0.50, now=NOW)
        assert decision.allowed is True

    def test_agent_scoped_guardrail(self, svc):
        # Agent A has a limit, Agent B doesn't
        svc.create_guardrail(
            name="agent A cap",
            scope=GuardrailScope.AGENT,
            scope_id="agent-A",
            daily_limit_usd=5.0,
        )
        # Agent A is close to limit
        add_llm_usage(svc.store, 4.0, agent_id="agent-A")
        # Agent A should be blocked
        decision_a = svc.check_guardrails(
            estimated_cost_usd=2.0, agent_id="agent-A", now=NOW
        )
        assert decision_a.allowed is False
        assert decision_a.action == GuardrailAction.BLOCK

        # Agent B should be allowed (no guardrail matches)
        decision_b = svc.check_guardrails(
            estimated_cost_usd=2.0, agent_id="agent-B", now=NOW
        )
        assert decision_b.allowed is True

    def test_model_scoped_guardrail(self, svc):
        svc.create_guardrail(
            name="expensive model cap",
            scope=GuardrailScope.MODEL,
            scope_id="gpt-4o",
            daily_limit_usd=20.0,
        )
        add_llm_usage(svc.store, 15.0, model_id="gpt-4o")

        # gpt-4o should warn
        decision = svc.check_guardrails(
            estimated_cost_usd=5.0, model_id="gpt-4o", now=NOW
        )
        assert decision.action == GuardrailAction.BLOCK  # 15+5=20 = 100%

        # Different model should be fine
        decision2 = svc.check_guardrails(
            estimated_cost_usd=5.0, model_id="gpt-4o-mini", now=NOW
        )
        assert decision2.allowed is True

    def test_priority_ordering(self, svc):
        # Low priority global guardrail with low limit
        svc.create_guardrail(
            name="global",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=1000,
            priority=0,
        )
        # High priority agent guardrail with stricter limit
        svc.create_guardrail(
            name="agent strict",
            scope=GuardrailScope.AGENT,
            scope_id="agent-1",
            daily_limit_usd=5,
            priority=10,
        )
        add_llm_usage(svc.store, 4.0, agent_id="agent-1")
        decision = svc.check_guardrails(
            estimated_cost_usd=2.0, agent_id="agent-1", now=NOW
        )
        assert decision.allowed is False
        # Should reference the stricter agent guardrail
        g = svc.get_guardrail(decision.guardrail_id)
        assert g.name == "agent strict"

    def test_cost_alerts_generated_on_breach(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        add_llm_usage(svc.store, 9.0)
        svc.check_guardrails(estimated_cost_usd=2.0, now=NOW)
        alerts = svc.list_cost_alerts()
        assert len(alerts) >= 1
        assert alerts[0].level == AlertLevel.CRITICAL

    def test_disabled_guardrail_ignored(self, svc):
        svc.create_guardrail(
            name="disabled",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=0.01,
            enabled=False,
        )
        decision = svc.check_guardrails(estimated_cost_usd=100, now=NOW)
        assert decision.allowed is True

    def test_suggestions_on_block(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        add_llm_usage(svc.store, 9.0)
        decision = svc.check_guardrails(estimated_cost_usd=5.0, now=NOW)
        assert decision.allowed is False
        assert len(decision.suggestions) > 0
        assert any("cheaper model" in s for s in decision.suggestions)


# --- Service: Kill Switch ---

class TestKillSwitch:
    def test_trigger_kill_switch(self, svc):
        ks = svc.trigger_kill_switch(reason="budget blown", triggered_by="operator")
        assert ks.active is True
        assert ks.reason == "budget blown"
        assert ks.triggered_by == "operator"
        assert ks.breach_count == 1

    def test_kill_switch_blocks_all_calls(self, svc):
        svc.trigger_kill_switch(reason="emergency")
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.allowed is False
        assert decision.action == GuardrailAction.KILL
        assert "KILL SWITCH" in decision.reason

    def test_kill_switch_overrides_guardrails(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=1000)
        svc.trigger_kill_switch(reason="emergency")
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.KILL

    def test_reset_kill_switch(self, svc):
        svc.trigger_kill_switch(reason="emergency")
        ks = svc.reset_kill_switch()
        assert ks.active is False

    def test_reset_kill_switch_restores_calls(self, svc):
        svc.trigger_kill_switch(reason="emergency")
        svc.reset_kill_switch()
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.allowed is True

    def test_kill_switch_with_override_token(self, svc):
        svc.trigger_kill_switch(reason="emergency", override_token="secret123")
        # Reset without token should fail
        with pytest.raises(ValueError, match="Invalid override token"):
            svc.reset_kill_switch()
        # Reset with correct token should work
        ks = svc.reset_kill_switch(override_token="secret123")
        assert ks.active is False

    def test_kill_switch_auto_expire(self, svc):
        svc.trigger_kill_switch(reason="brief pause", expires_in_minutes=5, now=NOW)
        # Before expiry → blocked
        decision = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert decision.action == GuardrailAction.KILL
        # After expiry → allowed
        future = NOW + timedelta(minutes=10)
        decision2 = svc.check_guardrails(estimated_cost_usd=0.01, now=future)
        assert decision2.allowed is True

    def test_breach_count_increments(self, svc):
        svc.trigger_kill_switch(reason="1")
        svc.reset_kill_switch()
        svc.trigger_kill_switch(reason="2")
        svc.reset_kill_switch()
        svc.trigger_kill_switch(reason="3")
        ks = svc.get_kill_switch_status()
        assert ks.breach_count == 3


# --- Service: Cost Alerts ---

class TestCostAlerts:
    def test_alerts_created_on_warn(self, svc):
        svc.create_guardrail(
            name="daily",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10,
            warn_at_percent=80,
        )
        add_llm_usage(svc.store, 7.5)
        svc.check_guardrails(estimated_cost_usd=0.5, now=NOW)
        alerts = svc.list_cost_alerts()
        assert len(alerts) >= 1

    def test_acknowledge_alert(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        add_llm_usage(svc.store, 9.0)
        svc.check_guardrails(estimated_cost_usd=2.0, now=NOW)
        alerts = svc.list_cost_alerts(unacknowledged_only=True)
        assert len(alerts) >= 1
        acked = svc.acknowledge_cost_alert(alerts[0].id)
        assert acked.acknowledged is True
        unacked = svc.list_cost_alerts(unacknowledged_only=True)
        assert len(unacked) == 0

    def test_clear_alerts(self, svc):
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=10)
        add_llm_usage(svc.store, 9.0)
        svc.check_guardrails(estimated_cost_usd=2.0, now=NOW)
        assert len(svc.list_cost_alerts()) >= 1
        cleared = svc.clear_cost_alerts()
        assert cleared >= 1
        assert len(svc.list_cost_alerts()) == 0


# --- Integration ---

class TestIntegration:
    def test_full_guardrail_workflow(self, svc):
        """End-to-end: create guardrail, track usage, get warned, get blocked."""
        # 1. Set up guardrail
        g = svc.create_guardrail(
            name="Daily agent cap",
            scope=GuardrailScope.AGENT,
            scope_id="worker-bot",
            daily_limit_usd=20.0,
            warn_at_percent=75,
            block_at_percent=100,
        )

        # 2. Agent makes calls, stays under limit
        add_llm_usage(svc.store, 5.0, agent_id="worker-bot")
        d = svc.check_guardrails(estimated_cost_usd=2.0, agent_id="worker-bot", now=NOW)
        assert d.allowed is True
        assert d.action == GuardrailAction.ALLOW

        # 3. Agent approaches limit → warn
        add_llm_usage(svc.store, 10.0, agent_id="worker-bot")  # Total: 15
        d = svc.check_guardrails(estimated_cost_usd=1.0, agent_id="worker-bot", now=NOW)
        # 15+1=16, 16/20=80% >= 75% warn threshold
        assert d.allowed is True
        assert d.action == GuardrailAction.WARN

        # 4. Agent exceeds limit → block
        d = svc.check_guardrails(estimated_cost_usd=6.0, agent_id="worker-bot", now=NOW)
        # 15+6=21 > 20 → block
        assert d.allowed is False
        assert d.action == GuardrailAction.BLOCK
        assert len(d.suggestions) > 0

    def test_multiple_scopes_interact(self, svc):
        """Global limit is high, agent limit is low — agent limit should trigger first."""
        svc.create_guardrail(
            name="global",
            scope=GuardrailScope.GLOBAL,
            monthly_limit_usd=10000,
            priority=0,
        )
        svc.create_guardrail(
            name="agent strict",
            scope=GuardrailScope.AGENT,
            scope_id="cheap-agent",
            daily_limit_usd=2,
            priority=10,
        )
        add_llm_usage(svc.store, 1.5, agent_id="cheap-agent")
        d = svc.check_guardrails(
            estimated_cost_usd=1.0, agent_id="cheap-agent", now=NOW
        )
        # 1.5+1=2.5 > 2 daily limit → block
        assert d.allowed is False
        assert d.guardrail_id is not None
        g = svc.get_guardrail(d.guardrail_id)
        assert g.name == "agent strict"

    def test_kill_switch_then_recovery(self, svc):
        """Kill switch triggers, blocks everything, then resets and works."""
        svc.create_guardrail(name="daily", scope=GuardrailScope.GLOBAL, daily_limit_usd=100)

        # Normal operation
        d = svc.check_guardrails(estimated_cost_usd=1.0, now=NOW)
        assert d.allowed is True

        # Emergency!
        svc.trigger_kill_switch(reason="security incident", triggered_by="admin")

        # All blocked
        d = svc.check_guardrails(estimated_cost_usd=0.01, now=NOW)
        assert d.allowed is False
        assert d.action == GuardrailAction.KILL

        # Reset
        svc.reset_kill_switch()

        # Back to normal
        d = svc.check_guardrails(estimated_cost_usd=1.0, now=NOW)
        assert d.allowed is True
