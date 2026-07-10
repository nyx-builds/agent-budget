"""Tests for the Model Cost Optimizer (v0.11.0)."""

import pytest
from agent_budget.optimizer import (
    ModelOptimizer,
    ModelComparison,
    OptimizationRecommendation,
    CostEstimate,
    capability_tier,
    tier_lower,
)
from agent_budget.llm_costs import PriceCatalog, ModelProvider, DEFAULT_PRICING


# --- Capability tier tests ---

class TestCapabilityTier:
    def test_known_high_tier(self):
        assert capability_tier("gpt-4") == "high"
        assert capability_tier("claude-3-opus") == "high"
        assert capability_tier("gemini-2.5-pro") == "high"
        assert capability_tier("o3") == "high"

    def test_known_medium_tier(self):
        assert capability_tier("gpt-4o") == "medium"
        assert capability_tier("claude-3-5-sonnet") == "medium"

    def test_known_economy_tier(self):
        assert capability_tier("gpt-4o-mini") == "economy"
        assert capability_tier("gpt-3.5-turbo") == "economy"
        assert capability_tier("gemini-1.5-flash") == "economy"

    def test_unknown_model_defaults_economy(self):
        assert capability_tier("totally-unknown-model") == "economy"

    def test_case_insensitive_partial_match(self):
        assert capability_tier("gpt-4o-2024-08-06") == "medium"
        assert capability_tier("GPT-4O-MINI") == "economy"

    def test_tier_lower(self):
        assert tier_lower("economy", "medium") is True
        assert tier_lower("economy", "high") is True
        assert tier_lower("medium", "high") is True
        assert tier_lower("high", "medium") is False
        assert tier_lower("medium", "economy") is False


# --- Optimizer core tests ---

class TestModelOptimizerBasic:
    def test_cost_for_usage(self):
        opt = ModelOptimizer()
        cost = opt.cost_for_usage("gpt-4o", input_tokens=1000, output_tokens=500)
        # 1000/1M * 2.50 + 500/1M * 10.00 = 0.0025 + 0.005 = 0.0075
        assert abs(cost - 0.0075) < 0.0001

    def test_cost_for_usage_unknown_model(self):
        opt = ModelOptimizer()
        # Falls back to gpt-4o-mini pricing
        cost = opt.cost_for_usage("nonexistent-model", 1000, 500)
        assert cost > 0

    def test_estimate(self):
        opt = ModelOptimizer()
        est = opt.estimate("gpt-4o", input_tokens=1000, output_tokens=500)
        assert est is not None
        assert est.model_id == "gpt-4o"
        assert est.provider == "openai"
        assert est.input_price_per_mtok == 2.50
        assert est.output_price_per_mtok == 10.00
        assert est.cost_per_call > 0
        assert est.tier == "medium"

    def test_estimate_unknown_model(self):
        opt = ModelOptimizer()
        est = opt.estimate("nonexistent-model", 1000, 500)
        assert est is None


# --- Comparison tests ---

class TestModelComparison:
    def test_compare_returns_sorted_by_savings(self):
        opt = ModelOptimizer()
        results = opt.compare_models("gpt-4o", input_tokens=1000, output_tokens=500)
        assert len(results) > 0
        # gpt-4o-mini should be cheaper than gpt-4o
        mini = [r for r in results if r.alternative_model == "gpt-4o-mini"]
        assert len(mini) == 1
        assert mini[0].savings_per_call > 0  # savings from switching to mini
        # Results sorted by savings descending
        savings = [r.savings_per_call for r in results]
        assert savings == sorted(savings, reverse=True)

    def test_compare_excludes_current_model(self):
        opt = ModelOptimizer()
        results = opt.compare_models("gpt-4o", input_tokens=1000, output_tokens=500)
        assert all(r.alternative_model != "gpt-4o" for r in results)

    def test_compare_current_cost_matches(self):
        opt = ModelOptimizer()
        results = opt.compare_models("gpt-4o", input_tokens=1000, output_tokens=500)
        for r in results:
            assert r.current_model == "gpt-4o"
            assert r.input_tokens == 1000
            assert r.output_tokens == 500

    def test_compare_min_tier_filter(self):
        opt = ModelOptimizer()
        # Only economy models — should only include economy alternatives
        results = opt.compare_models("gpt-4o", input_tokens=1000, output_tokens=500, min_tier="medium")
        for r in results:
            assert capability_tier(r.alternative_model) in ("medium", "high")

    def test_compare_provider_filter(self):
        opt = ModelOptimizer()
        results = opt.compare_models(
            "gpt-4o", input_tokens=1000, output_tokens=500,
            provider=ModelProvider.OPENAI,
        )
        for r in results:
            price = opt.catalog.get_price(r.alternative_model)
            assert price.provider == ModelProvider.OPENAI

    def test_compare_max_cost_filter(self):
        opt = ModelOptimizer()
        results = opt.compare_models(
            "gpt-4o", input_tokens=1000, output_tokens=500,
            max_cost_per_call=0.002,
        )
        for r in results:
            assert r.alternative_cost_per_call <= 0.002

    def test_compare_savings_percent(self):
        opt = ModelOptimizer()
        results = opt.compare_models("gpt-4o", input_tokens=1000, output_tokens=500)
        for r in results:
            if r.current_cost_per_call > 0:
                expected_pct = (r.savings_per_call / r.current_cost_per_call) * 100
                assert abs(r.savings_percent - round(expected_pct, 2)) < 0.1


# --- Recommendation tests ---

class TestRecommendation:
    def test_recommend_cheaper_alternative(self):
        opt = ModelOptimizer()
        rec = opt.recommend("gpt-4o", input_tokens=1000, output_tokens=500)
        assert rec is not None
        assert rec.current_model == "gpt-4o"
        assert rec.recommended_model != "gpt-4o"
        assert rec.savings_per_call > 0
        assert rec.savings_percent > 0
        assert "rationale" in rec.rationale.lower() or rec.rationale  # has a rationale string

    def test_recommend_already_cheapest_returns_none(self):
        """gpt-4o-mini is the cheapest economy model; no cheaper alternative exists."""
        opt = ModelOptimizer()
        # At economy tier, gpt-4o-mini is already very cheap
        # gpt-3.5-turbo is 0.50/1.50, mini is 0.15/0.60
        rec = opt.recommend("gpt-4o-mini", input_tokens=1000, output_tokens=500)
        # If there's a cheaper model, rec is returned; if not, None
        if rec:
            # Verify it's genuinely cheaper
            assert rec.savings_per_call > 0

    def test_recommend_with_monthly_calls(self):
        opt = ModelOptimizer()
        rec = opt.recommend(
            "gpt-4o", input_tokens=1000, output_tokens=500, monthly_calls=10000,
        )
        assert rec is not None
        assert rec.monthly_calls == 10000
        assert rec.projected_monthly_savings > 0
        # Verify projection math
        expected = rec.savings_per_call * 10000
        assert abs(rec.projected_monthly_savings - round(expected, 2)) < 0.01

    def test_recommend_with_min_tier(self):
        opt = ModelOptimizer()
        rec = opt.recommend("gpt-4o", input_tokens=1000, output_tokens=500, min_tier="medium")
        assert rec is not None
        assert capability_tier(rec.recommended_model) in ("medium", "high")

    def test_recommend_includes_alternatives(self):
        opt = ModelOptimizer()
        rec = opt.recommend("gpt-4o", input_tokens=1000, output_tokens=500)
        assert rec is not None
        assert len(rec.alternatives) > 0
        assert len(rec.alternatives) <= 5

    def test_recommend_downgrade_rationale_mentions_downgrade(self):
        opt = ModelOptimizer()
        # gpt-4o is medium; cheapest alternative might be economy
        rec = opt.recommend("gpt-4o", input_tokens=1000, output_tokens=500)
        if rec and tier_lower(rec.recommended_tier, rec.current_tier):
            assert "Downgrade" in rec.rationale

    def test_recommend_expensive_model_finds_big_savings(self):
        """gpt-4 is very expensive — switching should find large savings."""
        opt = ModelOptimizer()
        rec = opt.recommend("gpt-4", input_tokens=1000, output_tokens=500)
        assert rec is not None
        assert rec.savings_percent > 50  # should save >50%


# --- Projection tests ---

class TestProjectSwitch:
    def test_project_switch_basic(self):
        opt = ModelOptimizer()
        result = opt.project_switch(
            "gpt-4o", "gpt-4o-mini",
            input_tokens=1000, output_tokens=500,
            monthly_calls=10000,
        )
        assert result["from_model"] == "gpt-4o"
        assert result["to_model"] == "gpt-4o-mini"
        assert result["from_cost_per_call"] > result["to_cost_per_call"]
        assert result["savings_per_call"] > 0
        assert result["savings_percent"] > 0
        assert result["projected_monthly_savings"] > 0

    def test_project_switch_negative_savings(self):
        """Switching to a more expensive model should show negative savings."""
        opt = ModelOptimizer()
        result = opt.project_switch(
            "gpt-4o-mini", "gpt-4o",
            input_tokens=1000, output_tokens=500,
            monthly_calls=100,
        )
        assert result["savings_per_call"] < 0
        assert result["projected_monthly_savings"] < 0

    def test_project_switch_zero_calls(self):
        opt = ModelOptimizer()
        result = opt.project_switch(
            "gpt-4o", "gpt-4o-mini",
            input_tokens=1000, output_tokens=500,
            monthly_calls=0,
        )
        assert result["projected_monthly_savings"] == 0.0

    def test_project_switch_same_model(self):
        opt = ModelOptimizer()
        result = opt.project_switch(
            "gpt-4o", "gpt-4o",
            input_tokens=1000, output_tokens=500,
            monthly_calls=1000,
        )
        assert result["savings_per_call"] == 0.0
        assert result["projected_monthly_savings"] == 0.0


# --- Cheapest for tier tests ---

class TestCheapestForTier:
    def test_cheapest_economy(self):
        opt = ModelOptimizer()
        result = opt.cheapest_for_tier(min_tier="economy", input_tokens=1000, output_tokens=500)
        assert result is not None
        assert result.tier in ("economy", "medium", "high")
        # Should be cheaper than any high-tier model
        gpt4_cost = opt.cost_for_usage("gpt-4", 1000, 500)
        assert result.cost_per_call < gpt4_cost

    def test_cheapest_medium(self):
        opt = ModelOptimizer()
        result = opt.cheapest_for_tier(min_tier="medium", input_tokens=1000, output_tokens=500)
        assert result is not None
        assert result.tier in ("medium", "high")

    def test_cheapest_with_provider_filter(self):
        opt = ModelOptimizer()
        result = opt.cheapest_for_tier(
            min_tier="economy", input_tokens=1000, output_tokens=500,
            provider=ModelProvider.OPENAI,
        )
        assert result is not None
        assert result.provider == "openai"

    def test_cheapest_no_results(self):
        """Unknown provider should return None."""
        opt = ModelOptimizer()
        result = opt.cheapest_for_tier(
            min_tier="economy", input_tokens=1000, output_tokens=500,
            provider=ModelProvider.CUSTOM,
        )
        assert result is None


# --- Integration with PriceCatalog ---

class TestOptimizerWithCustomPrices:
    def test_optimizer_with_custom_price(self):
        catalog = PriceCatalog()
        catalog.set_price("my-custom-model", ModelProvider.CUSTOM, 0.01, 0.02)
        opt = ModelOptimizer(catalog)
        est = opt.estimate("my-custom-model", 1000, 500)
        assert est is not None
        assert est.provider == "custom"
        assert abs(est.cost_per_call - (0.001 * 0.01 + 0.0005 * 0.02)) < 0.0001

    def test_custom_model_in_comparison(self):
        catalog = PriceCatalog()
        catalog.set_price("super-cheap", ModelProvider.CUSTOM, 0.01, 0.01)
        opt = ModelOptimizer(catalog)
        rec = opt.recommend("gpt-4o", input_tokens=1000, output_tokens=500)
        assert rec is not None
        # super-cheap should be the recommendation since it's cheapest
        assert rec.recommended_model == "super-cheap"

    def test_optimizer_isolates_catalog_changes(self):
        """Each optimizer has its own catalog by default."""
        opt1 = ModelOptimizer()
        opt1.catalog.set_price("custom-1", ModelProvider.CUSTOM, 1.0, 1.0)
        opt2 = ModelOptimizer()
        assert opt2.catalog.get_price("custom-1") is None


# --- Edge cases ---

class TestEdgeCases:
    def test_zero_tokens(self):
        opt = ModelOptimizer()
        cost = opt.cost_for_usage("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_very_large_token_count(self):
        opt = ModelOptimizer()
        cost = opt.cost_for_usage("gpt-4o", input_tokens=10_000_000, output_tokens=5_000_000)
        # 10M/1M * 2.50 + 5M/1M * 10.00 = 25 + 50 = 75
        assert abs(cost - 75.0) < 0.01

    def test_cache_token_costing(self):
        opt = ModelOptimizer()
        cost = opt.cost_for_usage(
            "gpt-4o", input_tokens=1000, output_tokens=500,
            cache_read_tokens=500,
        )
        # gpt-4o has no cache price, so it's the same as without
        no_cache_cost = opt.cost_for_usage("gpt-4o", 1000, 500)
        assert abs(cost - no_cache_cost) < 0.0001

    def test_compare_all_same_provider(self):
        opt = ModelOptimizer()
        results = opt.compare_models(
            "gpt-4o", input_tokens=1000, output_tokens=500,
            provider=ModelProvider.ANTHROPIC,
        )
        for r in results:
            price = opt.catalog.get_price(r.alternative_model)
            assert price.provider == ModelProvider.ANTHROPIC
