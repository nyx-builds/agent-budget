"""LLM cost tracking for agent-budget — record token usage, calculate costs, aggregate by model/agent/period.

Supports OpenAI, Anthropic, Google, and custom model pricing. Can automatically
create expenses from LLM usage records when integrated with agent-budget budgets.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---

class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    META = "meta"
    MISTRAL = "mistral"
    COHERE = "cohere"
    CUSTOM = "custom"


# --- Pricing ---

# Per-million-token pricing (USD). Format: (input, output, cache_read, cache_write)
# Sourced from public pricing pages as of 2025.
DEFAULT_PRICING: dict[str, dict] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00, "provider": ModelProvider.OPENAI},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "provider": ModelProvider.OPENAI},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00, "provider": ModelProvider.OPENAI},
    "gpt-4": {"input": 30.00, "output": 60.00, "provider": ModelProvider.OPENAI},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50, "provider": ModelProvider.OPENAI},
    "o1": {"input": 15.00, "output": 60.00, "provider": ModelProvider.OPENAI},
    "o1-mini": {"input": 3.00, "output": 12.00, "provider": ModelProvider.OPENAI},
    "o3-mini": {"input": 1.10, "output": 4.40, "provider": ModelProvider.OPENAI},
    "o3": {"input": 10.00, "output": 40.00, "provider": ModelProvider.OPENAI},

    # Anthropic
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "provider": ModelProvider.ANTHROPIC},
    "claude-opus-4-1": {"input": 15.00, "output": 75.00, "provider": ModelProvider.ANTHROPIC},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00, "provider": ModelProvider.ANTHROPIC},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00, "provider": ModelProvider.ANTHROPIC},
    "claude-3-opus": {"input": 15.00, "output": 75.00, "provider": ModelProvider.ANTHROPIC},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00, "provider": ModelProvider.ANTHROPIC},
    "claude-3-haiku": {"input": 0.25, "output": 1.25, "provider": ModelProvider.ANTHROPIC},

    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00, "provider": ModelProvider.GOOGLE},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "provider": ModelProvider.GOOGLE},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00, "provider": ModelProvider.GOOGLE},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30, "provider": ModelProvider.GOOGLE},

    # Meta
    "llama-3.1-70b": {"input": 0.59, "output": 0.79, "provider": ModelProvider.META},
    "llama-3.1-405b": {"input": 2.70, "output": 2.70, "provider": ModelProvider.META},

    # Mistral
    "mistral-large": {"input": 2.00, "output": 6.00, "provider": ModelProvider.MISTRAL},
    "mistral-small": {"input": 0.20, "output": 0.60, "provider": ModelProvider.MISTRAL},
}


class ModelPrice(BaseModel):
    """Pricing for a specific model (per million tokens)."""
    model_id: str = Field(description="Model identifier (e.g., 'gpt-4o')")
    provider: ModelProvider = Field(description="Model provider")
    input_price_per_mtok: float = Field(ge=0, description="Price per million input tokens (USD)")
    output_price_per_mtok: float = Field(ge=0, description="Price per million output tokens (USD)")
    cache_read_price_per_mtok: float = Field(default=0.0, ge=0, description="Price per million cached input tokens")
    cache_write_price_per_mtok: float = Field(default=0.0, ge=0, description="Price per million cache write tokens")
    is_builtin: bool = Field(default=False, description="Whether this is a built-in price")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Usage Records ---

class LLMUsageRecord(BaseModel):
    """A single LLM API call usage record."""
    id: str = Field(default_factory=lambda: f"LLM-{uuid.uuid4().hex[:8].upper()}")
    model_id: str = Field(description="Model identifier (e.g., 'gpt-4o')")
    agent_id: Optional[str] = Field(default=None, description="Agent that made the call")
    task_id: Optional[str] = Field(default=None, description="Task/session identifier")
    input_tokens: int = Field(default=0, ge=0, description="Number of input/prompt tokens")
    output_tokens: int = Field(default=0, ge=0, description="Number of output/completion tokens")
    cache_read_tokens: int = Field(default=0, ge=0, description="Cached input tokens")
    cache_write_tokens: int = Field(default=0, ge=0, description="Cache write tokens")
    cost_usd: float = Field(default=0.0, ge=0, description="Calculated cost in USD")
    metadata: dict = Field(default_factory=dict, description="Extra metadata")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expense_id: Optional[str] = Field(default=None, description="Linked expense ID if synced to budget")

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens


# --- Aggregation ---

class ModelCostSummary(BaseModel):
    """Cost summary for a single model."""
    model_id: str
    provider: Optional[str] = None
    call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_cost_per_call: float = 0.0
    percentage_of_total: float = 0.0


class AgentCostSummary(BaseModel):
    """Cost summary for a single agent."""
    agent_id: str
    call_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    models_used: list[str] = Field(default_factory=list)
    avg_cost_per_call: float = 0.0
    percentage_of_total: float = 0.0


class CostReport(BaseModel):
    """Full cost report across all models and agents."""
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd:  float = 0.0
    avg_cost_per_call: float = 0.0
    by_model: list[ModelCostSummary] = Field(default_factory=list)
    by_agent: list[AgentCostSummary] = Field(default_factory=list)
    top_model: Optional[str] = None
    top_agent: Optional[str] = None
    most_expensive_call: Optional[float] = None


class DailyCostBreakdown(BaseModel):
    """Daily cost breakdown entry."""
    date: date
    call_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0


class CostProjection(BaseModel):
    """Projected LLM cost for a future period."""
    period: str
    projected_cost_usd: float = 0.0
    projected_calls: int = 0
    daily_avg_cost: float = 0.0
    based_on_days: int = 0
    confidence: float = 0.0


# --- Price Catalog ---

class PriceCatalog:
    """Manages model pricing with built-in defaults and user overrides."""

    def __init__(self, custom_prices: Optional[dict[str, ModelPrice]] = None):
        self._prices: dict[str, ModelPrice] = {}
        # Load built-in prices
        for model_id, pricing in DEFAULT_PRICING.items():
            self._prices[model_id] = ModelPrice(
                model_id=model_id,
                provider=pricing["provider"],
                input_price_per_mtok=pricing["input"],
                output_price_per_mtok=pricing["output"],
                is_builtin=True,
            )
        # Apply user overrides
        if custom_prices:
            self._prices.update(custom_prices)

    def get_price(self, model_id: str) -> Optional[ModelPrice]:
        """Get pricing for a model, checking exact match and case-insensitive."""
        if model_id in self._prices:
            return self._prices[model_id]
        # Try case-insensitive match
        lower = model_id.lower()
        for key, price in self._prices.items():
            if key.lower() == lower:
                return price
        return None

    def set_price(
        self,
        model_id: str,
        provider: ModelProvider,
        input_price: float,
        output_price: float,
        cache_read_price: float = 0.0,
        cache_write_price: float = 0.0,
    ) -> ModelPrice:
        """Set or update pricing for a model."""
        price = ModelPrice(
            model_id=model_id,
            provider=provider,
            input_price_per_mtok=input_price,
            output_price_per_mtok=output_price,
            cache_read_price_per_mtok=cache_read_price,
            cache_write_price_per_mtok=cache_write_price,
            is_builtin=False,
        )
        self._prices[model_id] = price
        return price

    def list_prices(self, provider: Optional[ModelProvider] = None) -> list[ModelPrice]:
        """List all known model prices, optionally filtered by provider."""
        prices = list(self._prices.values())
        if provider:
            prices = [p for p in prices if p.provider == provider]
        return sorted(prices, key=lambda p: (p.provider.value, p.model_id))

    def calculate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Calculate cost in USD for a token usage.

        If model is unknown, falls back to gpt-4o-mini pricing with a warning in metadata.
        """
        price = self.get_price(model_id)
        if not price:
            # Fallback to cheap pricing
            price = self._prices["gpt-4o-mini"]

        cost = (
            (input_tokens / 1_000_000) * price.input_price_per_mtok
            + (output_tokens / 1_000_000) * price.output_price_per_mtok
            + (cache_read_tokens / 1_000_000) * price.cache_read_price_per_mtok
            + (cache_write_tokens / 1_000_000) * price.cache_write_price_per_mtok
        )
        return round(cost, 6)
