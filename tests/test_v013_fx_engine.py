"""Tests for v0.13.0 Multi-Currency FX Engine.

Covers the FXEngine class, service-layer FX methods, and multi-currency
aggregation logic.
"""

import pytest
from datetime import date

from agent_budget.fx import (
    FXEngine,
    FXRate,
    FXRateSource,
    MultiCurrencySummary,
    format_multi_currency,
)
from agent_budget.service import BudgetService
from agent_budget.store import BudgetStore
from agent_budget.models import (
    Budget,
    BudgetPeriod,
    Expense,
    Income,
    SavingsGoal,
    SavingsGoalStatus,
)


# ---------------------------------------------------------------------------
# FXRateSource (static table)
# ---------------------------------------------------------------------------

class TestFXRateSource:
    def test_get_usd_rate(self):
        assert FXRateSource.get_rate_to_usd("USD") == 1.0

    def test_get_eur_rate(self):
        assert FXRateSource.get_rate_to_usd("EUR") == 1.08

    def test_get_unknown_currency(self):
        assert FXRateSource.get_rate_to_usd("XYZ") is None

    def test_case_insensitive(self):
        assert FXRateSource.get_rate_to_usd("eur") == 1.08

    def test_all_codes_includes_majors(self):
        codes = FXRateSource.all_codes()
        assert "USD" in codes
        assert "EUR" in codes
        assert "GBP" in codes
        assert "JPY" in codes
        assert len(codes) >= 15

    def test_all_rates_positive(self):
        for code in FXRateSource.all_codes():
            rate = FXRateSource.get_rate_to_usd(code)
            assert rate > 0, f"Rate for {code} should be positive"


# ---------------------------------------------------------------------------
# FXEngine — set/get rates
# ---------------------------------------------------------------------------

class TestFXEngineSetGet:
    def test_set_and_get_direct_rate(self):
        engine = FXEngine()
        fxr = engine.set_rate("EUR", "USD", 1.10)
        assert fxr.from_currency == "EUR"
        assert fxr.to_currency == "USD"
        assert fxr.rate == 1.10
        assert fxr.source == "manual"

        result = engine.get_rate("EUR", "USD")
        assert result is not None
        assert result.rate == 1.10

    def test_get_identity_rate(self):
        engine = FXEngine()
        fxr = engine.get_rate("USD", "USD")
        assert fxr is not None
        assert fxr.rate == 1.0
        assert fxr.source == "identity"

    def test_set_rate_case_insensitive(self):
        engine = FXEngine()
        engine.set_rate("eur", "usd", 1.10)
        result = engine.get_rate("EUR", "USD")
        assert result is not None
        assert result.rate == 1.10

    def test_set_same_currency_raises(self):
        engine = FXEngine()
        with pytest.raises(ValueError, match="must differ"):
            engine.set_rate("USD", "USD", 1.0)

    def test_set_negative_rate_raises(self):
        engine = FXEngine()
        with pytest.raises(ValueError, match="positive"):
            engine.set_rate("EUR", "USD", -1.0)

    def test_set_zero_rate_raises(self):
        engine = FXEngine()
        with pytest.raises(ValueError, match="positive"):
            engine.set_rate("EUR", "USD", 0)

    def test_get_nonexistent_rate(self):
        engine = FXEngine()
        result = engine.get_rate("XYZ", "ABC")
        assert result is None

    def test_list_rates_empty(self):
        engine = FXEngine()
        rates = engine.list_rates()
        assert rates == []

    def test_list_rates_after_setting(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        engine.set_rate("GBP", "USD", 1.27)
        rates = engine.list_rates()
        assert len(rates) == 2

    def test_remove_rate(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        assert engine.remove_rate("EUR", "USD") is True
        assert engine.remove_rate("EUR", "USD") is False  # already removed

    def test_clear_rates(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        engine.set_rate("GBP", "USD", 1.27)
        count = engine.clear_rates()
        assert count == 2
        assert engine.list_rates() == []


# ---------------------------------------------------------------------------
# FXEngine — inverse derivation
# ---------------------------------------------------------------------------

class TestFXEngineInverse:
    def test_inverse_custom_rate(self):
        """If USD→EUR is set, EUR→USD should be derived as inverse."""
        engine = FXEngine()
        engine.set_rate("USD", "EUR", 0.90)  # 1 USD = 0.90 EUR
        fxr = engine.get_rate("EUR", "USD")
        assert fxr is not None
        assert fxr.source == "manual_inverse"
        assert abs(fxr.rate - 1.0 / 0.90) < 0.0001  # ≈ 1.111...

    def test_inverse_round_trip(self):
        engine = FXEngine()
        engine.set_rate("USD", "EUR", 0.90)
        # Convert 100 USD → EUR → USD should be ~100
        eur = engine.convert(100, "USD", "EUR")
        usd_back = engine.convert(eur, "EUR", "USD")
        assert abs(usd_back - 100) < 0.01


# ---------------------------------------------------------------------------
# FXEngine — triangulation via USD
# ---------------------------------------------------------------------------

class TestFXEngineTriangulation:
    def test_triangulate_custom_rates(self):
        """If EUR→USD and GBP→USD are set, derive EUR→GBP via triangulation."""
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)  # 1 EUR = 1.10 USD
        engine.set_rate("GBP", "USD", 1.30)  # 1 GBP = 1.30 USD
        # EUR→GBP = 1.10 / 1.30 ≈ 0.8462
        fxr = engine.get_rate("EUR", "GBP")
        assert fxr is not None
        assert fxr.source == "triangulated"
        assert abs(fxr.rate - 1.10 / 1.30) < 0.001

    def test_triangulation_falls_back_to_static(self):
        """Without custom rates, triangulation uses static table."""
        engine = FXEngine()
        fxr = engine.get_rate("EUR", "GBP")
        assert fxr is not None
        assert fxr.source == "static"
        # EUR→USD=1.08, GBP→USD=1.27 → EUR→GBP = 1.08/1.27
        expected = 1.08 / 1.27
        assert abs(fxr.rate - expected) < 0.01


# ---------------------------------------------------------------------------
# FXEngine — conversion
# ---------------------------------------------------------------------------

class TestFXEngineConvert:
    def test_convert_identity(self):
        engine = FXEngine()
        assert engine.convert(100, "USD", "USD") == 100

    def test_convert_with_custom_rate(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        result = engine.convert(100, "EUR", "USD")
        assert abs(result - 110.0) < 0.01

    def test_convert_with_static_rate(self):
        engine = FXEngine()
        # EUR→USD from static = 1.08
        result = engine.convert(100, "EUR", "USD")
        assert abs(result - 108.0) < 0.5

    def test_convert_no_rate_raises(self):
        engine = FXEngine()
        with pytest.raises(ValueError, match="No exchange rate"):
            engine.convert(100, "XYZ", "ABC")

    def test_convert_zero_amount(self):
        engine = FXEngine()
        assert engine.convert(0, "USD", "EUR") == 0

    def test_convert_many(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        engine.set_rate("GBP", "USD", 1.30)
        items = [(100, "EUR"), (50, "GBP"), (200, "USD")]
        results = engine.convert_many(items, "USD")
        assert abs(results[0] - 110.0) < 0.01
        assert abs(results[1] - 65.0) < 0.01
        assert results[2] == 200

    def test_convert_jpy_zero_decimals(self):
        """JPY has 0 decimal places — but conversion should still work numerically."""
        engine = FXEngine()
        result = engine.convert(10000, "JPY", "USD")
        # Static: 1 JPY = 0.0067 USD → 10000 JPY ≈ 67 USD
        assert result > 50
        assert result < 100


# ---------------------------------------------------------------------------
# FXEngine — serialization
# ---------------------------------------------------------------------------

class TestFXEngineSerialization:
    def test_to_dict_empty(self):
        engine = FXEngine()
        d = engine.to_dict()
        assert d == {"rates": [], "history": []}

    def test_round_trip(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        engine.set_rate("GBP", "USD", 1.30)
        d = engine.to_dict()
        restored = FXEngine.from_dict(d)
        assert len(restored.list_rates()) == 2
        fxr = restored.get_rate("EUR", "USD")
        assert fxr is not None
        assert fxr.rate == 1.10

    def test_from_empty_dict(self):
        engine = FXEngine.from_dict({})
        assert engine.list_rates() == []


# ---------------------------------------------------------------------------
# Service-layer FX integration
# ---------------------------------------------------------------------------

class TestServiceFX:
    def _make_service(self):
        return BudgetService(BudgetStore())

    def test_service_has_fx_engine(self):
        svc = self._make_service()
        assert hasattr(svc, "fx")
        assert isinstance(svc.fx, FXEngine)

    def test_service_set_and_get_rate(self):
        svc = self._make_service()
        fxr = svc.set_fx_rate("EUR", "USD", 1.10)
        assert fxr.rate == 1.10
        result = svc.get_fx_rate("EUR", "USD")
        assert result is not None
        assert result.rate == 1.10

    def test_service_convert_currency(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        result = svc.convert_currency(100, "EUR", "USD")
        assert abs(result - 110.0) < 0.01

    def test_service_list_fx_rates(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        svc.set_fx_rate("GBP", "USD", 1.30)
        rates = svc.list_fx_rates()
        assert len(rates) == 2

    def test_service_delete_fx_rate(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        assert svc.delete_fx_rate("EUR", "USD") is True
        assert svc.delete_fx_rate("EUR", "USD") is False


# ---------------------------------------------------------------------------
# Multi-currency summary aggregation
# ---------------------------------------------------------------------------

class TestMultiCurrencySummary:
    def _make_service(self):
        """Each test gets a fresh store in a temp dir for isolation."""
        import tempfile
        tmpdir = tempfile.mkdtemp()
        return BudgetService(BudgetStore(data_dir=tmpdir))

    def test_empty_summary(self):
        svc = self._make_service()
        summary = svc.get_multi_currency_summary("USD")
        assert summary.base_currency == "USD"
        assert summary.total_budget_converted == 0.0
        assert summary.total_spending_converted == 0.0
        assert summary.total_income_converted == 0.0
        assert summary.total_savings_converted == 0.0
        assert summary.currencies_involved == []

    def test_single_currency_usd(self):
        svc = self._make_service()
        svc.create_budget("API Budget", 500, BudgetPeriod.MONTHLY, currency="USD")
        svc.add_expense(100, "API", currency="USD")
        svc.add_income(1000, "Job", currency="USD")
        svc.create_savings_goal("Emergency", 1000, currency="USD")

        summary = svc.get_multi_currency_summary("USD")
        assert summary.base_currency == "USD"
        assert summary.total_budget_converted == 500
        assert summary.total_spending_converted == 100
        assert summary.total_income_converted == 1000
        assert summary.total_savings_converted == 0  # no contribution yet
        assert "USD" in summary.currencies_involved

    def test_multi_currency_with_custom_rates(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        svc.set_fx_rate("GBP", "USD", 1.30)

        svc.create_budget("US API", 500, BudgetPeriod.MONTHLY, currency="USD")
        svc.create_budget("EU Services", 1000, BudgetPeriod.MONTHLY, currency="EUR")
        svc.create_budget("UK Tools", 800, BudgetPeriod.MONTHLY, currency="GBP")

        svc.add_expense(100, "API", currency="USD")
        svc.add_expense(200, "Cloud", currency="EUR")
        svc.add_expense(50, "Tools", currency="GBP")

        summary = svc.get_multi_currency_summary("USD")
        assert "USD" in summary.currencies_involved
        assert "EUR" in summary.currencies_involved
        assert "GBP" in summary.currencies_involved

        # Budget: 500 + (1000 * 1.10) + (800 * 1.30) = 500 + 1100 + 1040 = 2640
        assert summary.total_budget_converted == 2640

        # Spending: 100 + (200 * 1.10) + (50 * 1.30) = 100 + 220 + 65 = 385
        assert summary.total_spending_converted == 385

        # Check breakdowns
        assert summary.budgets_by_currency["USD"] == 500
        assert summary.budgets_by_currency["EUR"] == 1000
        assert summary.budgets_by_currency["GBP"] == 800

    def test_multi_currency_with_static_rates(self):
        """Without custom rates, should fall back to static reference table."""
        svc = self._make_service()

        svc.create_budget("US Budget", 100, BudgetPeriod.MONTHLY, currency="USD")
        svc.create_budget("EU Budget", 100, BudgetPeriod.MONTHLY, currency="EUR")

        svc.add_expense(50, "Test", currency="USD")
        svc.add_expense(50, "Test", currency="EUR")

        summary = svc.get_multi_currency_summary("USD")
        # Static EUR→USD = 1.08
        # Budget: 100 + (100 * 1.08) = 208
        assert abs(summary.total_budget_converted - 208) < 1
        # Spending: 50 + (50 * 1.08) = 104
        assert abs(summary.total_spending_converted - 104) < 1

    def test_summary_with_different_base_currency(self):
        """Convert everything to EUR instead of USD."""
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)

        svc.create_budget("US Budget", 110, BudgetPeriod.MONTHLY, currency="USD")
        summary = svc.get_multi_currency_summary("EUR")
        # USD→EUR = 1/1.10 ≈ 0.909
        # Budget: 110 USD * 0.909 ≈ 100 EUR
        assert abs(summary.total_budget_converted - 100) < 1

    def test_rates_used_populated(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        svc.create_budget("EU", 1000, BudgetPeriod.MONTHLY, currency="EUR")
        svc.add_expense(100, "Test", currency="EUR")

        summary = svc.get_multi_currency_summary("USD")
        assert "EUR→USD" in summary.rates_used
        assert summary.rates_used["EUR→USD"] == 1.10

    def test_cancelled_expenses_excluded(self):
        svc = self._make_service()
        svc.add_expense(100, "Test", currency="USD")
        # Add and cancel another
        exp = svc.add_expense(200, "Test", currency="USD")
        svc.update_expense(exp.id, status="cancelled")

        summary = svc.get_multi_currency_summary("USD")
        assert summary.total_spending_converted == 100

    def test_income_multi_currency(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        svc.add_income(1000, "Job", currency="USD")
        svc.add_income(2000, "Freelance", currency="EUR")

        summary = svc.get_multi_currency_summary("USD")
        # 1000 + (2000 * 1.10) = 1000 + 2200 = 3200
        assert summary.total_income_converted == 3200
        assert summary.income_by_currency["USD"] == 1000
        assert summary.income_by_currency["EUR"] == 2000

    def test_savings_multi_currency(self):
        svc = self._make_service()
        svc.set_fx_rate("EUR", "USD", 1.10)
        goal_usd = svc.create_savings_goal("US Savings", 1000, currency="USD")
        svc.contribute_to_savings(goal_usd.id, 500)
        goal_eur = svc.create_savings_goal("EU Savings", 1000, currency="EUR")
        svc.contribute_to_savings(goal_eur.id, 300)

        summary = svc.get_multi_currency_summary("USD")
        # 500 + (300 * 1.10) = 500 + 330 = 830
        assert summary.total_savings_converted == 830

    def test_currency_codes_uppercase(self):
        svc = self._make_service()
        svc.create_budget("Test", 100, BudgetPeriod.MONTHLY, currency="eur")
        summary = svc.get_multi_currency_summary("usd")
        assert summary.base_currency == "USD"
        assert all(c.isupper() for c in summary.currencies_involved)


# ---------------------------------------------------------------------------
# FXRate model
# ---------------------------------------------------------------------------

class TestFXRateModel:
    def test_default_source_is_static(self):
        fxr = FXRate(from_currency="EUR", to_currency="USD", rate=1.10)
        assert fxr.source == "static"

    def test_rate_must_be_positive(self):
        with pytest.raises(Exception):
            FXRate(from_currency="EUR", to_currency="USD", rate=0)
        with pytest.raises(Exception):
            FXRate(from_currency="EUR", to_currency="USD", rate=-1)

    def test_fields_set(self):
        fxr = FXRate(from_currency="EUR", to_currency="USD", rate=1.10, source="manual")
        assert fxr.from_currency == "EUR"
        assert fxr.to_currency == "USD"
        assert fxr.rate == 1.10
        assert fxr.source == "manual"


# ---------------------------------------------------------------------------
# format_multi_currency helper
# ---------------------------------------------------------------------------

class TestFormatMultiCurrency:
    def test_no_conversion_shown(self):
        result = format_multi_currency(100, "USD")
        assert "$100.00" in result

    def test_with_conversion(self):
        result = format_multi_currency(100, "EUR", 110, "USD")
        assert "€100.00" in result
        assert "$110.00" in result

    def test_same_currency_no_repeat(self):
        result = format_multi_currency(100, "USD", 100, "USD")
        # Should not show (100.00) since currencies are the same
        assert result.count("$") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_currency_in_summary(self):
        """Unknown currencies should be counted at face value (no crash)."""
        import tempfile
        svc = BudgetService(BudgetStore(data_dir=tempfile.mkdtemp()))
        svc.create_budget("Weird", 100, BudgetPeriod.MONTHLY, currency="XYZ")
        summary = svc.get_multi_currency_summary("USD")
        # XYZ has no rate, so it's counted at face value
        assert summary.total_budget_converted == 100
        assert "XYZ" in summary.currencies_involved

    def test_overwrite_existing_rate(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.10)
        engine.set_rate("EUR", "USD", 1.12)  # overwrite
        fxr = engine.get_rate("EUR", "USD")
        assert fxr.rate == 1.12

    def test_custom_overrides_static(self):
        engine = FXEngine()
        fxr_static = engine.get_rate("EUR", "USD")
        assert fxr_static.source == "static"

        engine.set_rate("EUR", "USD", 1.15)
        fxr_custom = engine.get_rate("EUR", "USD")
        assert fxr_custom.source == "manual"
        assert fxr_custom.rate == 1.15

    def test_remove_rate_falls_back_to_static(self):
        engine = FXEngine()
        engine.set_rate("EUR", "USD", 1.15)
        engine.remove_rate("EUR", "USD")

        fxr = engine.get_rate("EUR", "USD")
        assert fxr is not None
        assert fxr.source == "static"
        assert fxr.rate != 1.15

    def test_large_conversion_precision(self):
        engine = FXEngine()
        engine.set_rate("KRW", "USD", 0.00073)
        result = engine.convert(1_000_000, "KRW", "USD")
        # 1M KRW * 0.00073 = 730 USD
        assert abs(result - 730) < 1
