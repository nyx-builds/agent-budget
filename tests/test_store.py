"""Tests for agent-budget store (persistence)."""

import pytest
from datetime import datetime

from agent_budget.models import Budget, CostEntry, BudgetPeriod, CostCategory
from agent_budget.store import BudgetStore


@pytest.fixture
def store(tmp_path):
    return BudgetStore(data_dir=str(tmp_path / "data"))


class TestBudgetStore:
    def test_save_and_load_budget(self, store):
        budget = Budget(name="Test", limit=100.0)
        store.save_budget(budget)
        loaded = store.load_budget(budget.id)
        assert loaded is not None
        assert loaded.name == "Test"
        assert loaded.limit == 100.0

    def test_load_nonexistent(self, store):
        assert store.load_budget("nonexistent") is None

    def test_delete_budget(self, store):
        budget = Budget(name="Test", limit=100.0)
        store.save_budget(budget)
        assert store.delete_budget(budget.id) is True
        assert store.load_budget(budget.id) is None

    def test_delete_nonexistent(self, store):
        assert store.delete_budget("nonexistent") is False

    def test_list_budgets(self, store):
        b1 = Budget(name="A", limit=100.0)
        b2 = Budget(name="B", limit=200.0)
        store.save_budget(b1)
        store.save_budget(b2)
        budgets = store.list_budgets()
        assert len(budgets) == 2

    def test_list_budget_ids(self, store):
        b1 = Budget(name="A", limit=100.0)
        store.save_budget(b1)
        ids = store.list_budget_ids()
        assert b1.id in ids


class TestCostEntryStore:
    def test_save_and_load_cost_entries(self, store):
        entry = CostEntry(budget_id="abc123", amount=25.0, category=CostCategory.API_CALLS)
        store.save_cost_entry(entry)
        entries = store.load_cost_entries("abc123")
        assert len(entries) == 1
        assert entries[0].amount == 25.0

    def test_load_nonexistent(self, store):
        assert store.load_cost_entries("nonexistent") == []

    def test_multiple_entries(self, store):
        e1 = CostEntry(budget_id="b1", amount=10.0)
        e2 = CostEntry(budget_id="b1", amount=20.0)
        store.save_cost_entry(e1)
        store.save_cost_entry(e2)
        entries = store.load_cost_entries("b1")
        assert len(entries) == 2

    def test_delete_cost_entry(self, store):
        e1 = CostEntry(budget_id="b1", amount=10.0)
        e2 = CostEntry(budget_id="b1", amount=20.0)
        store.save_cost_entry(e1)
        store.save_cost_entry(e2)
        assert store.delete_cost_entry("b1", e1.id) is True
        entries = store.load_cost_entries("b1")
        assert len(entries) == 1
        assert entries[0].amount == 20.0

    def test_delete_cost_entry_not_found(self, store):
        assert store.delete_cost_entry("b1", "nonexistent") is False

    def test_load_by_date_range(self, store):
        old_entry = CostEntry(
            budget_id="b1", amount=10.0,
            timestamp=datetime(2026, 1, 1, 12, 0),
        )
        new_entry = CostEntry(
            budget_id="b1", amount=20.0,
            timestamp=datetime(2026, 6, 15, 12, 0),
        )
        store.save_cost_entry(old_entry)
        store.save_cost_entry(new_entry)
        filtered = store.load_cost_entries_by_date_range(
            "b1",
            start=datetime(2026, 6, 1),
            end=datetime(2026, 7, 1),
        )
        assert len(filtered) == 1
        assert filtered[0].amount == 20.0

    def test_delete_budget_removes_costs(self, store):
        budget = Budget(name="Test", limit=100.0)
        store.save_budget(budget)
        entry = CostEntry(budget_id=budget.id, amount=10.0)
        store.save_cost_entry(entry)
        assert len(store.load_cost_entries(budget.id)) == 1
        store.delete_budget(budget.id)
        assert store.load_cost_entries(budget.id) == []
