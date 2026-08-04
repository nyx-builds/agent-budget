"""Multi-currency FX (foreign exchange) engine for Agent Budget.

Manages exchange rates between currencies and provides conversion utilities.
This module enables agents that operate across multiple currencies (e.g.,
paying for US-based APIs in USD but reporting in EUR) to get accurate
budget and spending comparisons.

Rate sources (in priority order):
  1. Custom rates set by the user (manual override) — highest priority
  2. Built-in static rates (curated, updated periodically) — fallback

All rates are expressed as **units of target currency per 1 unit of source
currency**.  For example, ``USD→EUR = 0.92`` means 1 USD = 0.92 EUR.

The engine supports:
  * Direct lookup (USD→EUR)
  * Inverse derivation (if USD→EUR known, derive EUR→USD)
  * Triangulation through USD (if EUR→USD and GBP→USD known, derive EUR→GBP
    via USD as pivot) — this mirrors how most FX desks handle cross-rates.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .models import SUPPORTED_CURRENCIES, CurrencyInfo


# ---------------------------------------------------------------------------
# Built-in static rates (as of mid-2025 estimates).
#
# All rates are relative to USD (USD → X).  These are approximate reference
# rates suitable for budgeting and reporting — NOT for live trading.
# ---------------------------------------------------------------------------

_STATIC_RATES_TO_USD: dict[str, float] = {
    # 1 unit of KEY currency = VALUE in USD
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "CAD": 0.73,
    "AUD": 0.66,
    "CHF": 1.12,
    "CNY": 0.14,
    "INR": 0.012,
    "BRL": 0.18,
    "KRW": 0.00073,
    "MXN": 0.055,
    "SGD": 0.74,
    "SEK": 0.095,
    "NZD": 0.60,
}


class FXRateSource:
    """Static built-in rate table, expressed as currency→USD."""

    @staticmethod
    def get_rate_to_usd(currency: str) -> Optional[float]:
        return _STATIC_RATES_TO_USD.get(currency.upper())

    @staticmethod
    def all_codes() -> list[str]:
        return list(_STATIC_RATES_TO_USD.keys())


class FXRate(BaseModel):
    """A single exchange rate entry.

    Rates are stored as ``rate`` = units of ``to`` currency per 1 unit of
    ``from`` currency.
    """

    from_currency: str = Field(description="Source currency ISO code")
    to_currency: str = Field(description="Target currency ISO code")
    rate: float = Field(gt=0, description="Units of `to` per 1 unit of `from`")
    source: str = Field(default="static", description="Rate source: static, manual, api")
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MultiCurrencySummary(BaseModel):
    """Aggregated spending/budget summary across multiple currencies."""

    base_currency: str = Field(description="Currency all amounts are converted to")
    budgets_by_currency: dict[str, float] = Field(
        default_factory=dict,
        description="Original budget limits grouped by currency code",
    )
    total_budget_converted: float = Field(default=0.0, description="Total budget in base currency")
    spending_by_currency: dict[str, float] = Field(
        default_factory=dict,
        description="Original spending grouped by currency code",
    )
    total_spending_converted: float = Field(default=0.0, description="Total spending in base currency")
    income_by_currency: dict[str, float] = Field(
        default_factory=dict,
        description="Original income grouped by currency code",
    )
    total_income_converted: float = Field(default=0.0, description="Total income in base currency")
    savings_by_currency: dict[str, float] = Field(
        default_factory=dict,
        description="Original savings grouped by currency code",
    )
    total_savings_converted: float = Field(default=0.0, description="Total savings in base currency")
    currencies_involved: list[str] = Field(default_factory=list, description="All currency codes found")
    rates_used: dict[str, float] = Field(
        default_factory=dict,
        description="Rates used for conversion (keyed as 'FROM→TO')"
    )


class FXEngine:
    """Exchange-rate engine with manual override support.

    Usage::

        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)  # manual override
        amount_usd = engine.convert(100, "EUR", "USD")
        # → 110.0
    """

    def __init__(self) -> None:
        # custom_rates stores (from, to) → rate
        self._custom: dict[tuple[str, str], FXRate] = {}
        # Rate history for drift detection
        self._history: list[FXRateSnapshot] = []

    # ------------------------------------------------------------------
    # Rate management
    # ------------------------------------------------------------------

    def set_rate(self, from_currency: str, to_currency: str, rate: float) -> FXRate:
        """Set a custom exchange rate.

        If a previous rate existed for this pair, a history snapshot is
        recorded automatically so drift can be detected later.

        Raises:
            ValueError: if currencies are identical, unknown, or rate ≤ 0.
        """
        fc = from_currency.upper()
        tc = to_currency.upper()
        if fc == tc:
            raise ValueError("from_currency and to_currency must differ")
        if rate <= 0:
            raise ValueError("rate must be positive")

        # Snapshot old rate before overwriting (for drift detection)
        existing = self._custom.get((fc, tc))
        if existing is not None:
            self._history.append(FXRateSnapshot(
                from_currency=fc, to_currency=tc,
                rate=existing.rate, source=existing.source,
            ))

        fxr = FXRate(
            from_currency=fc,
            to_currency=tc,
            rate=rate,
            source="manual",
        )
        self._custom[(fc, tc)] = fxr
        return fxr

    def get_rate(self, from_currency: str, to_currency: str) -> Optional[FXRate]:
        """Look up the exchange rate for a currency pair.

        Resolution order:
          1. Exact custom pair (``from→to``)
          2. Inverse of custom pair (``to→from``)
          3. Triangulation via USD using custom rates
          4. Static reference table
        """
        fc = from_currency.upper()
        tc = to_currency.upper()

        if fc == tc:
            return FXRate(from_currency=fc, to_currency=tc, rate=1.0, source="identity")

        # 1. Exact custom pair
        key = (fc, tc)
        if key in self._custom:
            return self._custom[key]

        # 2. Inverse of custom reverse pair
        inv_key = (tc, fc)
        if inv_key in self._custom:
            inv_rate = self._custom[inv_key].rate
            return FXRate(
                from_currency=fc, to_currency=tc,
                rate=1.0 / inv_rate, source="manual_inverse",
            )

        # 3. Triangulation via USD using custom rates
        tri = self._triangulate_via_usd(fc, tc)
        if tri is not None:
            return FXRate(
                from_currency=fc, to_currency=tc,
                rate=tri, source="triangulated",
            )

        # 4. Static reference table
        static = self._static_rate(fc, tc)
        if static is not None:
            return FXRate(
                from_currency=fc, to_currency=tc,
                rate=static, source="static",
            )

        return None

    def remove_rate(self, from_currency: str, to_currency: str) -> bool:
        """Remove a custom rate. Returns True if it existed."""
        key = (from_currency.upper(), to_currency.upper())
        return self._custom.pop(key, None) is not None

    def list_rates(self) -> list[FXRate]:
        """List all custom rates."""
        return list(self._custom.values())

    def clear_rates(self) -> int:
        """Clear all custom rates. Returns count removed."""
        n = len(self._custom)
        self._custom.clear()
        return n

    # ------------------------------------------------------------------
    # Rate history & drift detection
    # ------------------------------------------------------------------

    def get_history(self, from_currency: str, to_currency: str) -> list[FXRateSnapshot]:
        """Return all historical snapshots for a pair (oldest first)."""
        fc = from_currency.upper()
        tc = to_currency.upper()
        return [
            s for s in self._history
            if s.from_currency == fc and s.to_currency == tc
        ]

    def snapshot_all_rates(self) -> int:
        """Take a snapshot of ALL current custom rates for later comparison.

        Returns the number of rates snapshotted.  Call this, wait a while
        (e.g., a day), update rates, then call ``detect_drift``.
        """
        count = 0
        for (fc, tc), fxr in self._custom.items():
            self._history.append(FXRateSnapshot(
                from_currency=fc, to_currency=tc,
                rate=fxr.rate, source=fxr.source,
            ))
            count += 1
        return count

    def detect_drift(
        self,
        threshold_percent: float = 5.0,
        exposure_amount: float = 0.0,
        exposure_currency: str = "",
    ) -> list[FXRateChangeAlert]:
        """Detect rate changes since the last snapshot.

        Compares each pair's **most recent historical snapshot** against its
        current rate.  If the change exceeds ``threshold_percent``, an
        :class:`FXRateChangeAlert` is generated.

        Args:
            threshold_percent: Minimum percentage change to trigger an alert.
            exposure_amount: If > 0, compute the financial impact on this
                amount (expressed in ``from_currency``).
            exposure_currency: The currency of ``exposure_amount`` (for
                labelling in the alert).

        Returns:
            List of alerts, sorted by absolute change (largest first).
        """
        alerts: list[FXRateChangeAlert] = []

        for (fc, tc), current in self._custom.items():
            # Find most recent history entry for this pair
            pair_history = [
                s for s in self._history
                if s.from_currency == fc and s.to_currency == tc
            ]
            if not pair_history:
                continue

            old_rate = pair_history[-1].rate
            if old_rate <= 0:
                continue

            change_pct = ((current.rate - old_rate) / old_rate) * 100.0
            if abs(change_pct) < threshold_percent:
                continue

            direction = "up" if change_pct > 0 else "down"
            impact = 0.0
            if exposure_amount > 0:
                # Impact = how much more/less (in target currency) the same
                # exposure would cost after the rate change.
                old_cost = exposure_amount * old_rate
                new_cost = exposure_amount * current.rate
                impact = round(new_cost - old_cost, 2)

            alerts.append(FXRateChangeAlert(
                from_currency=fc,
                to_currency=tc,
                old_rate=old_rate,
                new_rate=current.rate,
                change_percent=round(change_pct, 4),
                threshold_percent=threshold_percent,
                direction=direction,
                impact_amount=impact,
                impact_currency=exposure_currency or tc,
            ))

        alerts.sort(key=lambda a: abs(a.change_percent), reverse=True)
        return alerts

    def clear_history(self, from_currency: str = "", to_currency: str = "") -> int:
        """Clear rate history. If pair specified, only clears that pair.

        Returns count of snapshots removed.
        """
        if not from_currency:
            n = len(self._history)
            self._history.clear()
            return n

        fc = from_currency.upper()
        tc = to_currency.upper()
        before = len(self._history)
        self._history = [
            s for s in self._history
            if not (s.from_currency == fc and (not tc or s.to_currency == tc))
        ]
        return before - len(self._history)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def convert(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
    ) -> float:
        """Convert ``amount`` from one currency to another.

        Raises:
            ValueError: if no rate can be found for the pair.
        """
        fxr = self.get_rate(from_currency, to_currency)
        if fxr is None:
            raise ValueError(
                f"No exchange rate available for {from_currency}→{to_currency}"
            )
        return round(amount * fxr.rate, 10)

    def convert_many(
        self,
        items: list[tuple[float, str]],
        to_currency: str,
    ) -> list[float]:
        """Convert a batch of (amount, currency) pairs to ``to_currency``."""
        return [self.convert(amt, cur, to_currency) for amt, cur in items]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _triangulate_via_usd(self, fc: str, tc: str) -> Optional[float]:
        """Compute fc→tc by routing through USD.

        Uses ONLY custom rates for triangulation.  If either leg relies on
        the static table, returns None so the caller falls through to the
        pure static path (which is cheaper and gets labeled "static").
        """
        fc_to_usd = self._custom_to_usd(fc)
        tc_to_usd = self._custom_to_usd(tc)
        if fc_to_usd is not None and tc_to_usd is not None:
            return fc_to_usd / tc_to_usd
        return None

    def _custom_to_usd(self, currency: str) -> Optional[float]:
        """Resolve currency→USD using ONLY custom rates (no static fallback)."""
        c = currency.upper()
        if c == "USD":
            return 1.0
        key = (c, "USD")
        if key in self._custom:
            return self._custom[key].rate
        inv_key = ("USD", c)
        if inv_key in self._custom:
            return 1.0 / self._custom[inv_key].rate
        return None

    @staticmethod
    def _static_rate(fc: str, tc: str) -> Optional[float]:
        """Derive fc→tc from the static USD-based reference table."""
        fc_to_usd = FXRateSource.get_rate_to_usd(fc)
        tc_to_usd = FXRateSource.get_rate_to_usd(tc)
        if fc_to_usd is None or tc_to_usd is None:
            return None
        return fc_to_usd / tc_to_usd

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize engine state for persistence."""
        return {
            "rates": [
                {
                    "from_currency": r.from_currency,
                    "to_currency": r.to_currency,
                    "rate": r.rate,
                    "source": r.source,
                    "as_of": r.as_of.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in self._custom.values()
            ],
            "history": [
                {
                    "from_currency": s.from_currency,
                    "to_currency": s.to_currency,
                    "rate": s.rate,
                    "source": s.source,
                    "timestamp": s.timestamp.isoformat(),
                }
                for s in self._history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FXEngine":
        """Reconstruct an engine from serialized state."""
        engine = cls()
        for entry in data.get("rates", []):
            fxr = FXRate(
                from_currency=entry["from_currency"],
                to_currency=entry["to_currency"],
                rate=entry["rate"],
                source=entry.get("source", "manual"),
                as_of=datetime.fromisoformat(entry["as_of"]) if "as_of" in entry else datetime.now(timezone.utc),
                updated_at=datetime.fromisoformat(entry["updated_at"]) if "updated_at" in entry else datetime.now(timezone.utc),
            )
            engine._custom[(fxr.from_currency, fxr.to_currency)] = fxr
        for entry in data.get("history", []):
            engine._history.append(FXRateSnapshot(
                from_currency=entry["from_currency"],
                to_currency=entry["to_currency"],
                rate=entry["rate"],
                source=entry.get("source", "manual"),
                timestamp=datetime.fromisoformat(entry["timestamp"]) if "timestamp" in entry else datetime.now(timezone.utc),
            ))
        return engine


class FXRateSnapshot(BaseModel):
    """Historical snapshot of a rate, for drift detection."""

    from_currency: str
    to_currency: str
    rate: float
    source: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FXRateChangeAlert(BaseModel):
    """Alert triggered when an exchange rate has drifted beyond a threshold.

    For agents operating across currencies, a sudden FX movement can silently
    push a budget over its limit.  For example, if EUR strengthens 10% against
    USD, an agent with a USD budget paying for European services suddenly
    spends 10% more than expected — with no change in actual usage.
    """

    from_currency: str = Field(description="Source currency")
    to_currency: str = Field(description="Target currency")
    old_rate: float = Field(description="Previous rate")
    new_rate: float = Field(description="Current rate")
    change_percent: float = Field(description="Percentage change (old→new)")
    threshold_percent: float = Field(description="Threshold that was exceeded")
    direction: str = Field(description="'up' if rate increased, 'down' if decreased")
    impact_amount: float = Field(
        default=0.0,
        description="Estimated impact on a given exposure amount in target currency",
    )
    impact_currency: str = Field(default="", description="Currency of the impact_amount")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def format_multi_currency(
    amount: float,
    currency: str,
    converted_amount: Optional[float] = None,
    converted_currency: Optional[str] = None,
) -> str:
    """Format an amount with optional converted amount shown."""
    from .models import format_currency
    parts = [format_currency(amount, currency)]
    if converted_amount is not None and converted_currency and converted_currency.upper() != currency.upper():
        parts.append(f"({format_currency(converted_amount, converted_currency)})")
    return " ".join(parts)
