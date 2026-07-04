"""Tests for v0.6.0 Spend Projection & Loop Detection."""

import pytest
import tempfile
from datetime import datetime, timedelta, timezone

from agent_budget.models import (
    SpendProjection, LoopDetectionConfig, LoopDetectionResult,
    CostGuardrail, GuardrailScope, GuardrailAction,
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


def add_llm_usage(store, cost_usd, agent_id=None, model_id="gpt-4o", minutes_ago=0,
                  task_id=None, budget_id=None, input_tokens=1000, output_tokens=500):
    """Helper to add LLM usage records for testing."""
    metadata = {}
    if budget_id:
        metadata["budget_id"] = budget_id

    record = LLMUsageRecord(
        model_id=model_id,
        agent_id=agent_id,
        task_id=task_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        recorded_at=NOW - timedelta(minutes=minutes_ago),
        metadata=metadata,
    )
    store.save_llm_usage(record)
    return record


# --- Model Tests ---

class TestProjectionModels:
    def test_spend_projection_defaults(self):
        proj = SpendProjection(scope=GuardrailScope.GLOBAL, period="daily")
        assert proj.scope == GuardrailScope.GLOBAL
        assert proj.current_spend_usd == 0.0
        assert proj.projected_spend_usd == 0.0
        assert proj.spend_rate_per_hour == 0.0
        assert proj.projected_exceeds_limit is False
        assert proj.will_breach_guardrail is False
        assert proj.confidence == 0.0
        assert proj.eta_minutes_to_limit is None

    def test_loop_config_defaults(self):
        config = LoopDetectionConfig(name="test")
        assert config.id.startswith("LDC-")
        assert config.enabled is True
        assert config.window_minutes == 10
        assert config.repeat_threshold == 5
        assert config.similarity_threshold == 0.9
        assert config.auto_block_minutes == 0
        assert config.min_cost_usd == 0.0

    def test_loop_config_validation(self):
        with pytest.raises(Exception):
            LoopDetectionConfig(name="", window_minutes=10)
        with pytest.raises(Exception):
            LoopDetectionConfig(name="test", window_minutes=0)

    def test_loop_result_defaults(self):
        result = LoopDetectionResult(detected=False)
        assert result.detected is False
        assert result.call_count == 0
        assert result.cumulative_cost_usd == 0.0


# --- Spend Projection Service Tests ---

class TestSpendProjection:
    def test_projection_no_data(self, svc):
        """Projection with no usage data returns zeros."""
        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.current_spend_usd == 0.0
        assert proj.projected_spend_usd == 0.0
        assert proj.spend_rate_per_hour == 0.0
        assert proj.call_count_in_period == 0
        assert proj.confidence == 0.0

    def test_projection_with_data(self, temp_store, svc):
        """Projection correctly aggregates spend data."""
        add_llm_usage(temp_store, cost_usd=1.0, minutes_ago=30)
        add_llm_usage(temp_store, cost_usd=0.5, minutes_ago=10)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.current_spend_usd == 1.5
        assert proj.call_count_in_period == 2
        assert proj.avg_cost_per_call == 0.75
        assert proj.spend_rate_per_hour > 0
        assert proj.confidence == 0.4  # 2 calls

    def test_projection_agent_scope(self, temp_store, svc):
        """Projection filters by agent_id."""
        add_llm_usage(temp_store, cost_usd=1.0, agent_id="bot-1", minutes_ago=10)
        add_llm_usage(temp_store, cost_usd=2.0, agent_id="bot-2", minutes_ago=10)

        proj = svc.project_spend(
            scope=GuardrailScope.AGENT, scope_id="bot-1", period="daily", now=NOW
        )
        assert proj.current_spend_usd == 1.0
        assert proj.call_count_in_period == 1

    def test_projection_model_scope(self, temp_store, svc):
        """Projection filters by model_id."""
        add_llm_usage(temp_store, cost_usd=1.0, model_id="gpt-4o", minutes_ago=10)
        add_llm_usage(temp_store, cost_usd=2.0, model_id="claude-3", minutes_ago=10)

        proj = svc.project_spend(
            scope=GuardrailScope.MODEL, scope_id="gpt-4o", period="daily", now=NOW
        )
        assert proj.current_spend_usd == 1.0

    def test_projection_with_guardrail_limit(self, temp_store, svc):
        """Projection finds applicable guardrail and predicts breach."""
        # Add guardrail with daily limit
        svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
        )
        # Add some spend
        add_llm_usage(temp_store, cost_usd=5.0, minutes_ago=30)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.limit_usd == 10.0
        assert proj.current_spend_usd == 5.0
        assert proj.guardrail_id is not None

    def test_projection_predicts_breach(self, temp_store, svc):
        """Projection correctly predicts breach when rate is high."""
        svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=5.0,
            warn_at_percent=80.0,
            block_at_percent=100.0,
        )
        # High spend rate: $5 in first hour, rest of day will exceed
        add_llm_usage(temp_store, cost_usd=5.0, minutes_ago=1)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.current_spend_usd == 5.0
        # At this rate, projected spend for full day will exceed limit
        assert proj.projected_spend_usd > 5.0

    def test_projection_eta_to_limit(self, temp_store, svc):
        """Projection calculates ETA to limit."""
        svc.create_guardrail(
            name="Hourly Cap",
            scope=GuardrailScope.GLOBAL,
            hourly_limit_usd=10.0,
        )
        # $5 spent in 30 min → $10/hour rate → limit in ~30 more min
        add_llm_usage(temp_store, cost_usd=5.0, minutes_ago=30)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="hourly", now=NOW)
        assert proj.limit_usd == 10.0
        assert proj.spend_rate_per_hour > 0
        # ETA should be positive (haven't hit limit yet)
        if proj.eta_minutes_to_limit is not None:
            assert proj.eta_minutes_to_limit > 0

    def test_projection_no_limit_no_eta(self, temp_store, svc):
        """Projection without guardrail has no ETA."""
        add_llm_usage(temp_store, cost_usd=1.0, minutes_ago=10)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.limit_usd is None
        assert proj.eta_minutes_to_limit is None
        assert proj.will_breach_guardrail is False

    def test_projection_recommendation_text(self, temp_store, svc):
        """Projection includes human-readable recommendation."""
        svc.create_guardrail(
            name="Daily Cap",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
        )
        add_llm_usage(temp_store, cost_usd=1.0, minutes_ago=10)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert len(proj.recommendation) > 0

    def test_projection_hourly_period(self, temp_store, svc):
        """Projection works with hourly period."""
        add_llm_usage(temp_store, cost_usd=2.0, minutes_ago=15)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="hourly", now=NOW)
        assert proj.period == "hourly"
        assert proj.current_spend_usd == 2.0

    def test_projection_monthly_period(self, temp_store, svc):
        """Projection works with monthly period."""
        add_llm_usage(temp_store, cost_usd=100.0, minutes_ago=60)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="monthly", now=NOW)
        assert proj.period == "monthly"
        assert proj.current_spend_usd == 100.0

    def test_projection_confidence_scales(self, temp_store, svc):
        """Confidence increases with more data points."""
        # 1 call
        add_llm_usage(temp_store, cost_usd=0.1, minutes_ago=5)
        proj1 = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj1.confidence == 0.2

        # Add more calls for higher confidence
        for i in range(20):
            add_llm_usage(temp_store, cost_usd=0.1, minutes_ago=i+1)
        proj20 = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj20.confidence >= 0.9

    def test_projection_excludes_outside_period(self, temp_store, svc):
        """Projection only counts records within the period."""
        # Today's record
        add_llm_usage(temp_store, cost_usd=1.0, minutes_ago=30)
        # Yesterday's record (outside daily window)
        old_record = LLMUsageRecord(
            model_id="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=5.0,
            recorded_at=NOW - timedelta(days=1),
        )
        temp_store.save_llm_usage(old_record)

        proj = svc.project_spend(scope=GuardrailScope.GLOBAL, period="daily", now=NOW)
        assert proj.current_spend_usd == 1.0  # Only today's record counted


# --- Loop Detection Config CRUD Tests ---

class TestLoopConfigCRUD:
    def test_create_loop_config(self, svc):
        config = svc.create_loop_config(name="Global Guard")
        assert config.id.startswith("LDC-")
        assert config.name == "Global Guard"
        assert config.enabled is True

    def test_list_loop_configs(self, svc):
        svc.create_loop_config(name="Config 1")
        svc.create_loop_config(name="Config 2")
        configs = svc.list_loop_configs()
        assert len(configs) == 2

    def test_list_enabled_only(self, svc):
        svc.create_loop_config(name="Enabled", enabled=True)
        svc.create_loop_config(name="Disabled", enabled=False)
        enabled = svc.list_loop_configs(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "Enabled"

    def test_delete_loop_config(self, svc):
        config = svc.create_loop_config(name="To Delete")
        assert svc.delete_loop_config(config.id) is True
        assert svc.list_loop_configs() == []

    def test_delete_nonexistent(self, svc):
        assert svc.delete_loop_config("LDC-NONEXIST") is False

    def test_update_loop_config(self, svc):
        config = svc.create_loop_config(name="Original")
        updated = svc.update_loop_config(config.id, name="Updated", window_minutes=20)
        assert updated.name == "Updated"
        assert updated.window_minutes == 20

    def test_update_nonexistent_raises(self, svc):
        with pytest.raises(ValueError):
            svc.update_loop_config("LDC-NONEXIST", name="Updated")


# --- Loop Detection Service Tests ---

class TestLoopDetection:
    def test_no_configs_returns_not_detected(self, svc):
        """Without any loop configs, detection returns not detected."""
        result = svc.check_loop(now=NOW)
        assert result.detected is False
        assert "No loop detection" in result.recommendation

    def test_no_loops_detected(self, temp_store, svc):
        """With varied calls, no loop is detected."""
        svc.create_loop_config(name="Test", repeat_threshold=5, window_minutes=10)
        # Add varied calls
        for i in range(3):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-1",
                input_tokens=1000*(i+1), output_tokens=500*(i+1),
                minutes_ago=i+1,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is False

    def test_loop_detected_identical_calls(self, temp_store, svc):
        """Detects loop with 5+ identical calls."""
        svc.create_loop_config(name="Test", repeat_threshold=5, window_minutes=10)
        # Add 5 identical calls
        for i in range(5):
            add_llm_usage(
                temp_store, cost_usd=0.5, agent_id="bot-1",
                input_tokens=1000, output_tokens=500,
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is True
        assert result.call_count == 5
        assert result.cumulative_cost_usd == 2.5

    def test_loop_not_detected_below_threshold(self, temp_store, svc):
        """Doesn't detect loop with fewer calls than threshold."""
        svc.create_loop_config(name="Test", repeat_threshold=5, window_minutes=10)
        # Only 4 identical calls (below threshold of 5)
        for i in range(4):
            add_llm_usage(
                temp_store, cost_usd=0.5, agent_id="bot-1",
                input_tokens=1000, output_tokens=500,
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is False

    def test_loop_with_auto_block(self, temp_store, svc):
        """Loop detection with auto_block_minutes sets blocked_until."""
        svc.create_loop_config(
            name="Auto Block",
            repeat_threshold=3,
            window_minutes=10,
            auto_block_minutes=30,
        )
        for i in range(3):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-1",
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is True
        assert result.blocked_until is not None
        assert result.blocked_until > NOW

    def test_loop_min_cost_filter(self, temp_store, svc):
        """Loop below min_cost_usd is not flagged."""
        svc.create_loop_config(
            name="High Cost Only",
            repeat_threshold=3,
            window_minutes=10,
            min_cost_usd=10.0,
        )
        for i in range(3):
            add_llm_usage(
                temp_store, cost_usd=0.01, agent_id="bot-1",
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is False

    def test_loop_agent_scope_filter(self, temp_store, svc):
        """Loop config with agent_id only applies to that agent."""
        svc.create_loop_config(
            name="Bot-1 Only",
            repeat_threshold=3,
            window_minutes=10,
            agent_id="bot-1",
        )
        # bot-2 has a loop but config only applies to bot-1
        for i in range(5):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-2",
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-2", now=NOW)
        assert result.detected is False

    def test_loop_outside_window_not_counted(self, temp_store, svc):
        """Calls outside the window are not counted."""
        svc.create_loop_config(name="Test", repeat_threshold=5, window_minutes=5)
        # 5 identical calls but all >5 min ago (outside window)
        for i in range(5):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-1",
                minutes_ago=10 + i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is False

    def test_loop_recommendation_text(self, temp_store, svc):
        """Loop result includes recommendation."""
        svc.create_loop_config(name="Test", repeat_threshold=3, window_minutes=10)
        for i in range(3):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-1",
                minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", now=NOW)
        assert result.detected is True
        assert len(result.recommendation) > 0
        assert "Loop detected" in result.recommendation

    def test_loop_no_result_recommendation(self, svc):
        """No-loop result includes a recommendation."""
        svc.create_loop_config(name="Test", repeat_threshold=5)
        result = svc.check_loop(now=NOW)
        assert result.detected is False
        assert len(result.recommendation) > 0

    def test_loop_model_filter(self, temp_store, svc):
        """Loop detection with model_id filter."""
        svc.create_loop_config(
            name="GPT-4o Only",
            repeat_threshold=3,
            window_minutes=10,
            model_id="gpt-4o",
        )
        # Claude calls shouldn't trigger gpt-4o config
        for i in range(5):
            add_llm_usage(
                temp_store, cost_usd=0.1, agent_id="bot-1",
                model_id="claude-3", minutes_ago=i,
            )
        result = svc.check_loop(agent_id="bot-1", model_id="claude-3", now=NOW)
        assert result.detected is False

    def test_call_signature_method(self):
        """Test the call signature helper."""
        record = LLMUsageRecord(
            model_id="gpt-4o",
            input_tokens=1500,
            output_tokens=800,
            cost_usd=0.01,
        )
        sig = BudgetService._call_signature(record)
        assert "gpt-4o" in sig
        assert "in:1k" in sig  # 1500 // 1000 = 1
        assert "out:0k" in sig  # 800 // 1000 = 0

    def test_jaccard_similarity(self):
        """Test Jaccard similarity helper."""
        assert BudgetService._jaccard_similarity({1, 2, 3}, {1, 2, 3}) == 1.0
        assert BudgetService._jaccard_similarity(set(), set()) == 1.0
        assert BudgetService._jaccard_similarity({1}, {2}) == 0.0
        assert 0 < BudgetService._jaccard_similarity({1, 2}, {1, 3}) <= 0.5


# --- Store Tests ---

class TestLoopConfigStore:
    def test_store_persistence(self, temp_store):
        """Loop configs persist to disk."""
        from agent_budget.models import LoopDetectionConfig
        config = LoopDetectionConfig(name="Persist Test", repeat_threshold=7)
        temp_store.save_loop_config(config)

        # Re-read
        configs = temp_store.list_loop_configs()
        assert len(configs) == 1
        assert configs[0].name == "Persist Test"
        assert configs[0].repeat_threshold == 7

    def test_store_update(self, temp_store):
        """Loop configs can be updated."""
        from agent_budget.models import LoopDetectionConfig
        config = LoopDetectionConfig(name="Original")
        temp_store.save_loop_config(config)

        config.name = "Updated"
        temp_store.save_loop_config(config)

        configs = temp_store.list_loop_configs()
        assert len(configs) == 1
        assert configs[0].name == "Updated"

    def test_store_get_by_id(self, temp_store):
        """Loop config can be retrieved by ID."""
        from agent_budget.models import LoopDetectionConfig
        config = LoopDetectionConfig(name="Find Me")
        temp_store.save_loop_config(config)

        found = temp_store.get_loop_config(config.id)
        assert found is not None
        assert found.name == "Find Me"

        assert temp_store.get_loop_config("LDC-NONEXIST") is None

    def test_store_delete(self, temp_store):
        """Loop config can be deleted."""
        from agent_budget.models import LoopDetectionConfig
        config = LoopDetectionConfig(name="Delete Me")
        temp_store.save_loop_config(config)
        assert temp_store.delete_loop_config(config.id) is True
        assert temp_store.list_loop_configs() == []
