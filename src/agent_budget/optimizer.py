"""Model cost optimizer for agent-budget — compare, recommend, and project model savings.

Helps agents pick the cheapest model for their workload and quantifies the
savings from switching.  This is the "fair-price intelligence" layer that
sits on top of the PriceCatalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .llm_costs import PriceCatalog, ModelPrice, ModelProvider, DEFAULT_PRICING


# --- Capability tiers (rough heuristic) ---

CAPABILITY_TIERS: dict[str, str] = {}
for _mid in ("gpt-4", "gpt-4-turbo", "o1", "o3", "claude-3-opus",
             "claude-opus-4-1", "gemini-2.5-pro", "gemini-1.5-pro",
             "mistral-large", "llama-3.1-405b"):
    CAPABILITY_TIERS[_mid] = "high"
for _mid in ("gpt-4o", "claude-3-5-sonnet", "claude-sonnet-4-5",
             "claude-3-sonnet", "gemini-2.0-flash"):
    CAPABILITY_TIERS[_mid] = "medium"
for _mid in ("gpt-4o-mini", "gpt-3.5-turbo", "o1-mini", "o3-mini",
             "claude-3-5-haiku", "claude-3-haiku", "gemini-1.5-flash",
             "mistral-small", "llama-3.1-70b"):
    CAPABILITY_TIERS[_mid] = "economy"


def capability_tier(model_id: str) -> str:
    """Return 'high', 'medium', or 'economy' for a model."""
    if model_id in CAPABILITY_TIERS:
        return CAPABILITY_TIERS[model_id]
    lower = model_id.lower()
    # Check longer keys first to avoid "gpt-4" matching before "gpt-4o"
    for k in sorted(CAPABILITY_TIERS, key=len, reverse=True):
        if k in lower:
            return CAPABILITY_TIERS[k]
    return "economy"


# --- Data classes ---

@dataclass
class ModelComparison:
    """Cost comparison between two models for a usage profile."""
    current_model: str
    alternative_model: str
    current_cost_per_call: float
    alternative_cost_per_call: float
    savings_per_call: float
    savings_percent: float
    input_tokens: int
    output_tokens: int


@dataclass
class OptimizationRecommendation:
    """A model optimization recommendation."""
    current_model: str
    recommended_model: str
    current_tier: str
    recommended_tier: str
    savings_per_call: float
    savings_percent: float
    projected_monthly_savings: float
    rationale: str
    monthly_calls: int = 0
    alternatives: list[ModelComparison] = field(default_factory=list)


@dataclass
class CostEstimate:
    """Cost estimate for a model + usage profile."""
    model_id: str
    provider: str
    input_price_per_mtok: float
    output_price_per_mtok: float
    cost_per_call: float
    tier: str


class ModelOptimizer:
    """Optimize model selection for cost.

    Wraps a PriceCatalog and provides comparison, recommendation,
    and projection utilities.
    """

    def __init__(self, catalog: Optional[PriceCatalog] = None):
        self.catalog = catalog or PriceCatalog()

    # ---- core helpers ----

    def cost_for_usage(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Cost in USD for a single call."""
        return self.catalog.calculate_cost(
            model_id, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens,
        )

    def estimate(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Optional[CostEstimate]:
        """Build a CostEstimate for a model + token profile."""
        price = self.catalog.get_price(model_id)
        if not price:
            return None
        return CostEstimate(
            model_id=model_id,
            provider=price.provider.value,
            input_price_per_mtok=price.input_price_per_mtok,
            output_price_per_mtok=price.output_price_per_mtok,
            cost_per_call=self.cost_for_usage(model_id, input_tokens, output_tokens),
            tier=capability_tier(model_id),
        )

    # ---- comparison ----

    def compare_models(
        self,
        current_model: str,
        input_tokens: int,
        output_tokens: int,
        min_tier: Optional[str] = None,
        provider: Optional[ModelProvider] = None,
        max_cost_per_call: Optional[float] = None,
    ) -> list[ModelComparison]:
        """Compare ``current_model`` against all alternatives.

        Returns a list sorted by savings (highest first).  Negative
        savings (i.e. the alternative is more expensive) are included
        so the caller can see the full picture.
        """
        current_cost = self.cost_for_usage(current_model, input_tokens, output_tokens)
        current_tier = capability_tier(current_model)
        tier_rank = {"economy": 0, "medium": 1, "high": 2}
        min_rank = tier_rank.get(min_tier or "economy", 0)

        results: list[ModelComparison] = []
        for price in self.catalog.list_prices():
            alt = price.model_id
            if alt == current_model:
                continue
            if provider and price.provider != provider:
                continue
            alt_tier = capability_tier(alt)
            if tier_rank.get(alt_tier, 0) < min_rank:
                continue
            alt_cost = self.cost_for_usage(alt, input_tokens, output_tokens)
            if max_cost_per_call and alt_cost > max_cost_per_call:
                continue
            savings = current_cost - alt_cost
            pct = (savings / current_cost * 100) if current_cost > 0 else 0.0
            results.append(ModelComparison(
                current_model=current_model,
                alternative_model=alt,
                current_cost_per_call=round(current_cost, 6),
                alternative_cost_per_call=round(alt_cost, 6),
                savings_per_call=round(savings, 6),
                savings_percent=round(pct, 2),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ))
        results.sort(key=lambda c: c.savings_per_call, reverse=True)
        return results

    # ---- recommendation ----

    def recommend(
        self,
        current_model: str,
        input_tokens: int = 1000,
        output_tokens: int = 500,
        monthly_calls: int = 0,
        min_tier: Optional[str] = None,
    ) -> Optional[OptimizationRecommendation]:
        """Recommend the cheapest alternative model.

        If ``current_model`` is already the cheapest at or above
        ``min_tier``, returns ``None`` (no switch recommended).
        """
        comparisons = self.compare_models(
            current_model, input_tokens, output_tokens, min_tier=min_tier,
        )
        if not comparisons:
            return None

        # Best alternative = highest savings (first after sort)
        best = comparisons[0]
        if best.savings_per_call <= 0:
            return None  # current is already cheapest

        current_tier = capability_tier(current_model)
        recommended_tier = capability_tier(best.alternative_model)
        projected = best.savings_per_call * monthly_calls if monthly_calls else 0.0

        if recommended_tier == current_tier:
            rationale = (
                f"Switch from {current_model} ({current_tier}) to "
                f"{best.alternative_model} ({recommended_tier}) — same tier, "
                f"{best.savings_percent:.0f}% cheaper per call."
            )
        elif tier_lower(recommended_tier, current_tier):
            rationale = (
                f"Downgrade from {current_model} ({current_tier}) to "
                f"{best.alternative_model} ({recommended_tier}) — "
                f"saves {best.savings_percent:.0f}% per call. "
                f"Verify output quality is acceptable at this tier."
            )
        else:
            rationale = (
                f"Upgrade from {current_model} ({current_tier}) to "
                f"{best.alternative_model} ({recommended_tier}) and "
                f"still save {best.savings_percent:.0f}% — "
                f"current model is overpriced for its tier."
            )

        # Keep top 5 alternatives for the caller
        return OptimizationRecommendation(
            current_model=current_model,
            recommended_model=best.alternative_model,
            current_tier=current_tier,
            recommended_tier=recommended_tier,
            savings_per_call=best.savings_per_call,
            savings_percent=best.savings_percent,
            projected_monthly_savings=round(projected, 2),
            rationale=rationale,
            monthly_calls=monthly_calls,
            alternatives=comparisons[:5],
        )

    # ---- batch projection ----

    def project_switch(
        self,
        from_model: str,
        to_model: str,
        input_tokens: int,
        output_tokens: int,
        monthly_calls: int,
    ) -> dict:
        """Project monthly savings from switching models."""
        from_cost = self.cost_for_usage(from_model, input_tokens, output_tokens)
        to_cost = self.cost_for_usage(to_model, input_tokens, output_tokens)
        per_call = from_cost - to_cost
        monthly = per_call * monthly_calls
        pct = (per_call / from_cost * 100) if from_cost > 0 else 0.0
        return {
            "from_model": from_model,
            "to_model": to_model,
            "from_cost_per_call": round(from_cost, 6),
            "to_cost_per_call": round(to_cost, 6),
            "savings_per_call": round(per_call, 6),
            "savings_percent": round(pct, 2),
            "monthly_calls": monthly_calls,
            "projected_monthly_savings": round(monthly, 2),
        }

    def cheapest_for_tier(
        self,
        min_tier: str = "economy",
        input_tokens: int = 1000,
        output_tokens: int = 500,
        provider: Optional[ModelProvider] = None,
    ) -> Optional[CostEstimate]:
        """Return the cheapest model at or above ``min_tier``."""
        tier_rank = {"economy": 0, "medium": 1, "high": 2}
        min_rank = tier_rank.get(min_tier, 0)
        candidates: list[tuple[float, CostEstimate]] = []
        for price in self.catalog.list_prices():
            if provider and price.provider != provider:
                continue
            t = capability_tier(price.model_id)
            if tier_rank.get(t, 0) < min_rank:
                continue
            est = self.estimate(price.model_id, input_tokens, output_tokens)
            if est:
                candidates.append((est.cost_per_call, est))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]


def tier_lower(a: str, b: str) -> bool:
    """Return True if tier *a* is lower than tier *b*."""
    rank = {"economy": 0, "medium": 1, "high": 2}
    return rank.get(a, 0) < rank.get(b, 0)
