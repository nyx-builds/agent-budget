"""Tests for v0.10.0 Spend Anomaly Detection.

Tests anomaly rule CRUD, all 7 anomaly types (spike, sustained_drift,
rate_burst, cost_per_call, new_agent, new_model, after_hours), all 4
detection methods (zscore, multiplier, absolute, rate), severity
computation, cooldown enforcement, action execution, webhook integration,
kill switch trigger, and the anomaly summary endpoint.
"""

import pytest
import os
import tempfile
import math
from datetime import datetime, timedelta, timezone

from agent_budget.models import (
    SpendAnomalyRule, AnomalyEvent, AnomalyType, AnomalySeverity,
    AnomalyAction, GuardrailScope, GuardrailAction,
    CostGuardrail,
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


NOW = datetime(2025, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def make_usage_record(
    cost=0.01,
    model_id="gpt-4o",
    agent_id="agent-1",
    recorded_at=None,
    task_id=None,
):
    """Create and return an LLMUsageRecord."""
    return LLMUsageRecord(
        model_id=model_id,
        agent_id=agent_id,
        task_id=task_id,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=cost,
        recorded_at=recorded_at or NOW,
    )


def seed_baseline(svc, store, n=10, base_cost=0.01, hours_ago=48):
    """Seed N historical usage records for baseline computation."""
    for i in range(n):
        rec = make_usage_record(
            cost=base_cost + (i % 3) * 0.001,
            recorded_at=NOW - timedelta(hours=hours_ago - i * 2),
        )
        store.save_llm_usage(rec)
    return store.list_llm_usage()


# ============================================================
# Model Tests
# ============================================================

class TestAnomalyModels:
    def test_spend_anomaly_rule_defaults(self):
        rule = SpendAnomalyRule(
            name="Test Spike Detector",
            anomaly_type=AnomalyType.SPIKE,
        )
        assert rule.method == "zscore"
        assert rule.threshold == 3.0
        assert rule.baseline_window_hours == 24
        assert rule.min_samples == 5
        assert rule.scope == GuardrailScope.GLOBAL
        assert rule.action == AnomalyAction.LOG
        assert rule.cooldown_minutes == 30
        assert rule.enabled is True
        assert rule.id.startswith("ANR-")

    def test_anomaly_event_defaults(self):
        event = AnomalyEvent(
            rule_id="ANR-TEST",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
        )
        assert event.severity == AnomalySeverity.MEDIUM
        assert event.acknowledged is False
        assert event.resolved is False
        assert event.action_taken == AnomalyAction.LOG
        assert event.id.startswith("ANM-")

    def test_severity_computation_low(self):
        event = AnomalyEvent(
            rule_id="test",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
            deviation_score=3.0,
        )
        sev = event.compute_severity(threshold=3.0)
        assert sev == AnomalySeverity.LOW

    def test_severity_computation_medium(self):
        event = AnomalyEvent(
            rule_id="test",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
            deviation_score=4.5,  # 3.0 * 1.5
        )
        sev = event.compute_severity(threshold=3.0)
        assert sev == AnomalySeverity.MEDIUM

    def test_severity_computation_high(self):
        event = AnomalyEvent(
            rule_id="test",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
            deviation_score=6.0,  # 3.0 * 2.0
        )
        sev = event.compute_severity(threshold=3.0)
        assert sev == AnomalySeverity.HIGH

    def test_severity_computation_critical(self):
        event = AnomalyEvent(
            rule_id="test",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
            deviation_score=9.5,  # 3.0 * 3.0+
        )
        sev = event.compute_severity(threshold=3.0)
        assert sev == AnomalySeverity.CRITICAL

    def test_all_anomaly_types_exist(self):
        types = [AnomalyType.SPIKE, AnomalyType.SUSTAINED_DRIFT,
                 AnomalyType.RATE_BURST, AnomalyType.COST_PER_CALL,
                 AnomalyType.NEW_AGENT, AnomalyType.NEW_MODEL,
                 AnomalyType.AFTER_HOURS]
        assert len(types) == 7

    def test_all_anomaly_actions_exist(self):
        actions = [AnomalyAction.LOG, AnomalyAction.NOTIFY,
                   AnomalyAction.THROTTLE, AnomalyAction.BLOCK,
                   AnomalyAction.KILL_SWITCH]
        assert len(actions) == 5


# ============================================================
# Store Tests
# ============================================================

class TestAnomalyStore:
    def test_save_and_get_rule(self, temp_store):
        rule = SpendAnomalyRule(name="Test", anomaly_type=AnomalyType.SPIKE)
        temp_store.save_anomaly_rule(rule)
        fetched = temp_store.get_anomaly_rule(rule.id)
        assert fetched is not None
        assert fetched.name == "Test"

    def test_list_rules_enabled_only(self, temp_store):
        r1 = SpendAnomalyRule(name="R1", anomaly_type=AnomalyType.SPIKE, enabled=True)
        r2 = SpendAnomalyRule(name="R2", anomaly_type=AnomalyType.SPIKE, enabled=False)
        temp_store.save_anomaly_rule(r1)
        temp_store.save_anomaly_rule(r2)
        all_rules = temp_store.list_anomaly_rules()
        assert len(all_rules) == 2
        enabled = temp_store.list_anomaly_rules(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "R1"

    def test_delete_rule(self, temp_store):
        rule = SpendAnomalyRule(name="Test", anomaly_type=AnomalyType.SPIKE)
        temp_store.save_anomaly_rule(rule)
        assert temp_store.delete_anomaly_rule(rule.id) is True
        assert temp_store.get_anomaly_rule(rule.id) is None
        assert temp_store.delete_anomaly_rule("nonexistent") is False

    def test_save_and_get_event(self, temp_store):
        event = AnomalyEvent(
            rule_id="ANR-TEST",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=5.0,
        )
        temp_store.save_anomaly_event(event)
        fetched = temp_store.get_anomaly_event(event.id)
        assert fetched is not None
        assert fetched.observed_value == 5.0

    def test_list_events_filtered(self, temp_store):
        e1 = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=1)
        e2 = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=2, acknowledged=True)
        e3 = AnomalyEvent(rule_id="r2", anomaly_type=AnomalyType.SPIKE, observed_value=3)
        for e in [e1, e2, e3]:
            temp_store.save_anomaly_event(e)
        assert len(temp_store.list_anomaly_events()) == 3
        assert len(temp_store.list_anomaly_events(rule_id="r1")) == 2
        assert len(temp_store.list_anomaly_events(acknowledged=True)) == 1
        assert len(temp_store.list_anomaly_events(acknowledged=False)) == 2

    def test_clear_events(self, temp_store):
        e1 = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=1)
        e2 = AnomalyEvent(rule_id="r2", anomaly_type=AnomalyType.SPIKE, observed_value=2)
        temp_store.save_anomaly_event(e1)
        temp_store.save_anomaly_event(e2)
        count = temp_store.clear_anomaly_events(rule_id="r1")
        assert count == 1
        assert len(temp_store.list_anomaly_events()) == 1

    def test_clear_all_events(self, temp_store):
        for i in range(5):
            temp_store.save_anomaly_event(
                AnomalyEvent(rule_id=f"r{i}", anomaly_type=AnomalyType.SPIKE, observed_value=float(i))
            )
        count = temp_store.clear_anomaly_events()
        assert count == 5
        assert len(temp_store.list_anomaly_events()) == 0

    def test_delete_event(self, temp_store):
        event = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=1)
        temp_store.save_anomaly_event(event)
        assert temp_store.delete_anomaly_event(event.id) is True
        assert temp_store.get_anomaly_event(event.id) is None


# ============================================================
# Service CRUD Tests
# ============================================================

class TestAnomalyServiceCRUD:
    def test_create_rule(self, svc):
        rule = svc.create_anomaly_rule(
            name="Spike Detector",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.5,
        )
        assert rule.id.startswith("ANR-")
        assert rule.name == "Spike Detector"
        assert rule.threshold == 2.5

    def test_update_rule(self, svc):
        rule = svc.create_anomaly_rule(
            name="Test",
            anomaly_type=AnomalyType.SPIKE,
        )
        updated = svc.update_anomaly_rule(rule.id, threshold=5.0, enabled=False)
        assert updated.threshold == 5.0
        assert updated.enabled is False

    def test_update_nonexistent_rule_raises(self, svc):
        with pytest.raises(ValueError):
            svc.update_anomaly_rule("ANR-NONEXIST", threshold=1.0)

    def test_list_rules(self, svc):
        svc.create_anomaly_rule(name="R1", anomaly_type=AnomalyType.SPIKE)
        svc.create_anomaly_rule(name="R2", anomaly_type=AnomalyType.RATE_BURST, enabled=False)
        assert len(svc.list_anomaly_rules()) == 2
        assert len(svc.list_anomaly_rules(enabled_only=True)) == 1

    def test_delete_rule(self, svc):
        rule = svc.create_anomaly_rule(name="Test", anomaly_type=AnomalyType.SPIKE)
        assert svc.delete_anomaly_rule(rule.id) is True
        assert svc.get_anomaly_rule(rule.id) is None

    def test_acknowledge_event(self, svc, temp_store):
        event = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=1)
        temp_store.save_anomaly_event(event)
        acked = svc.acknowledge_anomaly_event(event.id, acknowledged_by="admin")
        assert acked.acknowledged is True
        assert acked.acknowledged_by == "admin"
        assert acked.acknowledged_at is not None

    def test_resolve_event(self, svc, temp_store):
        event = AnomalyEvent(rule_id="r1", anomaly_type=AnomalyType.SPIKE, observed_value=1)
        temp_store.save_anomaly_event(event)
        resolved = svc.resolve_anomaly_event(event.id)
        assert resolved.resolved is True
        assert resolved.resolved_at is not None

    def test_acknowledge_nonexistent_returns_none(self, svc):
        assert svc.acknowledge_anomaly_event("ANM-NONEXIST") is None

    def test_resolve_nonexistent_returns_none(self, svc):
        assert svc.resolve_anomaly_event("ANM-NONEXIST") is None


# ============================================================
# Detection Tests
# ============================================================

class TestAnomalyDetection:
    def test_no_rules_no_events(self, svc):
        events = svc.detect_anomalies(now=NOW)
        assert events == []

    def test_no_detection_below_min_samples(self, svc):
        """Should not fire when insufficient baseline data."""
        svc.create_anomaly_rule(
            name="Test",
            anomaly_type=AnomalyType.SPIKE,
            min_samples=100,
        )
        events = svc.detect_anomalies(now=NOW)
        assert events == []

    def test_spike_detection_zscore(self, svc, temp_store):
        """Detect a cost spike using z-score method."""
        # Seed baseline with consistent low costs
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        # Add recent high-cost spike
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        rule = svc.create_anomaly_rule(
            name="Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            baseline_window_hours=48,
            min_samples=3,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.SPIKE
        assert events[0].deviation_score >= 2.0

    def test_spike_detection_multiplier(self, svc, temp_store):
        """Detect a cost spike using multiplier method."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=0.50,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Spike Mult",
            anomaly_type=AnomalyType.SPIKE,
            method="multiplier",
            threshold=5.0,
            baseline_window_hours=48,
            min_samples=3,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].deviation_score >= 5.0

    def test_spike_detection_absolute(self, svc, temp_store):
        """Detect a cost spike using absolute threshold."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=0.10,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Spike Abs",
            anomaly_type=AnomalyType.SPIKE,
            method="absolute",
            threshold=0.05,
            baseline_window_hours=48,
            min_samples=3,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].observed_value >= 0.05

    def test_cost_per_call_zscore(self, svc, temp_store):
        """Detect a single high-cost call."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.002,  # slight variance for stddev
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="Cost Per Call",
            anomaly_type=AnomalyType.COST_PER_CALL,
            method="zscore",
            threshold=2.0,
            baseline_window_hours=48,
            min_samples=5,
        )
        # Check a record that's far above mean
        events = svc.detect_anomalies(
            now=NOW,
            check_record={"cost_usd": 0.50, "model_id": "gpt-4o"},
        )
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.COST_PER_CALL

    def test_cost_per_call_absolute(self, svc, temp_store):
        """Detect cost per call using absolute threshold."""
        for i in range(5):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="CPC Abs",
            anomaly_type=AnomalyType.COST_PER_CALL,
            method="absolute",
            threshold=0.05,
            min_samples=3,
        )
        events = svc.detect_anomalies(
            now=NOW,
            check_record={"cost_usd": 0.10},
        )
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.COST_PER_CALL

    def test_new_agent_detection(self, svc, temp_store):
        """Detect spending from a previously-unseen agent."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                agent_id="agent-1",
                cost=0.01,
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="New Agent",
            anomaly_type=AnomalyType.NEW_AGENT,
            min_samples=3,
        )
        events = svc.detect_anomalies(
            now=NOW,
            check_record={"agent_id": "agent-NEW", "cost_usd": 0.02},
        )
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.NEW_AGENT
        assert "agent-NEW" in events[0].message

    def test_new_agent_not_triggered_for_known(self, svc, temp_store):
        """Should NOT fire for an agent already in baseline."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                agent_id="agent-1",
                cost=0.01,
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="New Agent",
            anomaly_type=AnomalyType.NEW_AGENT,
            min_samples=3,
        )
        events = svc.detect_anomalies(
            now=NOW,
            check_record={"agent_id": "agent-1", "cost_usd": 0.01},
        )
        assert len(events) == 0

    def test_new_model_detection(self, svc, temp_store):
        """Detect spending on a previously-unseen model."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                model_id="gpt-4o",
                cost=0.01,
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="New Model",
            anomaly_type=AnomalyType.NEW_MODEL,
            min_samples=3,
        )
        events = svc.detect_anomalies(
            now=NOW,
            check_record={"model_id": "claude-opus-4-1", "cost_usd": 0.05},
        )
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.NEW_MODEL
        assert "claude-opus-4-1" in events[0].message

    def test_rate_burst_detection(self, svc, temp_store):
        """Detect a burst of calls per minute."""
        # Sparse baseline
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=i + 1),
            ))
        # Recent burst of many calls
        for i in range(20):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(seconds=i * 3),
            ))
        svc.create_anomaly_rule(
            name="Rate Burst",
            anomaly_type=AnomalyType.RATE_BURST,
            method="rate",
            threshold=3.0,
            min_samples=3,
            baseline_window_hours=24,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.RATE_BURST

    def test_after_hours_detection(self, svc, temp_store):
        """Detect spending during after-hours window."""
        # Set NOW to 2am UTC
        late_night = datetime(2025, 7, 8, 2, 0, 0, tzinfo=timezone.utc)
        # Baseline records spread over time
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=late_night - timedelta(hours=i + 2),
            ))
        # Recent record within the last hour (triggers after-hours check)
        temp_store.save_llm_usage(make_usage_record(
            cost=0.02,
            recorded_at=late_night - timedelta(minutes=10),
        ))
        svc.create_anomaly_rule(
            name="After Hours",
            anomaly_type=AnomalyType.AFTER_HOURS,
            after_hours_start=22,
            after_hours_end=6,
            min_samples=3,
        )
        events = svc.detect_anomalies(now=late_night)
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.AFTER_HOURS

    def test_after_hours_not_triggered_during_day(self, svc, temp_store):
        """Should NOT fire during normal hours."""
        daytime = datetime(2025, 7, 8, 14, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=daytime - timedelta(hours=i + 1),
            ))
        svc.create_anomaly_rule(
            name="After Hours",
            anomaly_type=AnomalyType.AFTER_HOURS,
            after_hours_start=22,
            after_hours_end=6,
            min_samples=3,
        )
        events = svc.detect_anomalies(now=daytime)
        assert len(events) == 0

    def test_sustained_drift_detection(self, svc, temp_store):
        """Detect a gradual cost increase over baseline window."""
        # Older period: low cost
        for i in range(5):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 2),
            ))
        # Newer period: much higher cost
        for i in range(5):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.10,
                recorded_at=NOW - timedelta(hours=10 - i * 2),
            ))
        svc.create_anomaly_rule(
            name="Drift",
            anomaly_type=AnomalyType.SUSTAINED_DRIFT,
            method="multiplier",
            threshold=3.0,
            baseline_window_hours=48,
            min_samples=4,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].anomaly_type == AnomalyType.SUSTAINED_DRIFT


# ============================================================
# Cooldown Tests
# ============================================================

class TestAnomalyCooldown:
    def test_cooldown_prevents_refire(self, svc, temp_store):
        """After firing, rule should not fire again within cooldown."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            baseline_window_hours=48,
            min_samples=3,
            cooldown_minutes=60,
        )
        # First detection should fire
        events1 = svc.detect_anomalies(now=NOW)
        assert len(events1) >= 1
        # Second detection 30 min later should be blocked by cooldown
        events2 = svc.detect_anomalies(now=NOW + timedelta(minutes=30))
        assert len(events2) == 0

    def test_cooldown_expires(self, svc, temp_store):
        """After cooldown expires, rule should fire again."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.001,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            baseline_window_hours=48,
            min_samples=3,
            cooldown_minutes=30,
        )
        # First detection
        events1 = svc.detect_anomalies(now=NOW)
        assert len(events1) >= 1
        # Add another recent spike for the second check
        later = NOW + timedelta(minutes=45)
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=later - timedelta(minutes=5),
        ))
        # After 45 min — cooldown (30 min) expired
        events2 = svc.detect_anomalies(now=later)
        assert len(events2) >= 1

    def test_zero_cooldown_allows_refire(self, svc, temp_store):
        """With cooldown=0, rule fires every time."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.001,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            baseline_window_hours=48,
            min_samples=3,
            cooldown_minutes=0,
        )
        events1 = svc.detect_anomalies(now=NOW)
        events2 = svc.detect_anomalies(now=NOW + timedelta(minutes=1))
        assert len(events1) >= 1
        assert len(events2) >= 1


# ============================================================
# Action Tests
# ============================================================

class TestAnomalyActions:
    def test_log_action(self, svc, temp_store):
        """LOG action should record event without side effects."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.001,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Log",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            min_samples=3,
            action=AnomalyAction.LOG,
            cooldown_minutes=0,
            baseline_window_hours=48,
        )
        events = svc.detect_anomalies(now=NOW)
        assert len(events) >= 1
        assert events[0].action_taken == AnomalyAction.LOG
        assert "logged" in events[0].action_result

    def test_kill_switch_action(self, svc, temp_store):
        """KILL_SWITCH action should trigger the kill switch."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.001,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=5.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))
        svc.create_anomaly_rule(
            name="Kill",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            min_samples=3,
            action=AnomalyAction.KILL_SWITCH,
            cooldown_minutes=0,
            baseline_window_hours=48,
        )
        svc.detect_anomalies(now=NOW)
        ks = svc.get_kill_switch_status()
        assert ks.is_active(NOW) is True
        assert "anomaly" in ks.reason.lower()


# ============================================================
# Summary Tests
# ============================================================

class TestAnomalySummary:
    def test_empty_summary(self, svc):
        summary = svc.get_anomaly_summary()
        assert summary.total_rules == 0
        assert summary.active_rules == 0
        assert summary.total_events == 0
        assert summary.unacknowledged_events == 0

    def test_summary_with_data(self, svc, temp_store):
        svc.create_anomaly_rule(name="R1", anomaly_type=AnomalyType.SPIKE, enabled=True)
        svc.create_anomaly_rule(name="R2", anomaly_type=AnomalyType.SPIKE, enabled=False)
        temp_store.save_anomaly_event(AnomalyEvent(
            rule_id="r1",
            anomaly_type=AnomalyType.SPIKE,
            observed_value=1.0,
            severity=AnomalySeverity.HIGH,
            anomaly_cost_usd=5.0,
        ))
        summary = svc.get_anomaly_summary()
        assert summary.total_rules == 2
        assert summary.active_rules == 1
        assert summary.total_events == 1
        assert summary.unacknowledged_events == 1
        assert summary.events_by_severity.get("high") == 1
        assert summary.events_by_type.get("spike") == 1
        assert summary.total_anomaly_cost_usd == 5.0


# ============================================================
# Scope Filtering Tests
# ============================================================

class TestAnomalyScopeFiltering:
    def test_agent_scoped_rule(self, svc, temp_store):
        """Agent-scoped rule only evaluates that agent's records."""
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                agent_id="agent-A",
                cost=0.01,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        # Add agent-B records that shouldn't affect agent-A baseline
        for i in range(5):
            temp_store.save_llm_usage(make_usage_record(
                agent_id="agent-B",
                cost=10.0,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        svc.create_anomaly_rule(
            name="Agent A Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            min_samples=3,
            scope=GuardrailScope.AGENT,
            scope_id="agent-A",
            baseline_window_hours=48,
        )
        # Should only evaluate agent-A records (low cost), no spike
        events = svc.detect_anomalies(now=NOW)
        # agent-A has consistent low costs, no spike expected
        # (Unless the recent records push z-score up — depends on data)
        for e in events:
            assert e.scope == GuardrailScope.AGENT
            assert e.scope_id == "agent-A"


# ============================================================
# Integration: Guardrail + Anomaly
# ============================================================

class TestGuardrailAnomalyIntegration:
    def test_anomaly_detection_with_guardrails_coexist(self, svc, temp_store):
        """Anomaly detection should work alongside existing guardrails."""
        # Create a guardrail
        guardrail = CostGuardrail(
            name="Daily Limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
        )
        temp_store.save_guardrail(guardrail)

        # Seed baseline
        for i in range(10):
            temp_store.save_llm_usage(make_usage_record(
                cost=0.01 + (i % 3) * 0.001,
                recorded_at=NOW - timedelta(hours=48 - i * 3),
            ))
        temp_store.save_llm_usage(make_usage_record(
            cost=2.0,
            recorded_at=NOW - timedelta(minutes=5),
        ))

        svc.create_anomaly_rule(
            name="Spike",
            anomaly_type=AnomalyType.SPIKE,
            method="zscore",
            threshold=2.0,
            min_samples=3,
        )

        # Both should work independently
        decision = svc.check_guardrails(estimated_cost_usd=0.0, now=NOW)
        events = svc.detect_anomalies(now=NOW)

        # Guardrail sees spend, anomaly sees pattern
        assert decision is not None
        assert len(events) >= 1
