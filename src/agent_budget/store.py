"""JSON-file backed persistence for agent-budget."""

from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from .models import Budget, CostEntry, BudgetStatus


DEFAULT_DATA_DIR = os.path.expanduser("~/.agent-budget/data")


def _default_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class BudgetStore:
    """Manages persistence of budgets and cost entries to JSON files."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── Budget CRUD ────────────────────────────────────────────────────────

    def _budget_path(self, budget_id: str) -> Path:
        return self.data_dir / "budgets" / f"{budget_id}.json"

    def _costs_path(self, budget_id: str) -> Path:
        return self.data_dir / "costs" / f"{budget_id}.json"

    def _index_path(self) -> Path:
        return self.data_dir / "index.json"

    def _ensure_dirs(self) -> None:
        (self.data_dir / "budgets").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "costs").mkdir(parents=True, exist_ok=True)

    def save_budget(self, budget: Budget) -> None:
        self._ensure_dirs()
        path = self._budget_path(budget.id)
        path.write_text(json.dumps(budget.model_dump(mode="json"), indent=2, default=_default_serializer))
        self._update_index(budget)

    def load_budget(self, budget_id: str) -> Optional[Budget]:
        path = self._budget_path(budget_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Budget.model_validate(data)

    def delete_budget(self, budget_id: str) -> bool:
        path = self._budget_path(budget_id)
        if not path.exists():
            return False
        path.unlink()
        # Also remove costs file
        costs_path = self._costs_path(budget_id)
        if costs_path.exists():
            costs_path.unlink()
        self._remove_from_index(budget_id)
        return True

    def list_budgets(self) -> list[Budget]:
        self._ensure_dirs()
        budgets = []
        budgets_dir = self.data_dir / "budgets"
        for path in sorted(budgets_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                budgets.append(Budget.model_validate(data))
            except Exception:
                continue
        return budgets

    def list_budget_ids(self) -> list[str]:
        index = self._load_index()
        return list(index.keys())

    # ── Cost Entry CRUD ────────────────────────────────────────────────────

    def save_cost_entry(self, entry: CostEntry) -> None:
        self._ensure_dirs()
        path = self._costs_path(entry.budget_id)
        entries = self.load_cost_entries(entry.budget_id)
        entries.append(entry)
        path.write_text(
            json.dumps([e.model_dump(mode="json") for e in entries], indent=2, default=_default_serializer)
        )

    def load_cost_entries(self, budget_id: str) -> list[CostEntry]:
        path = self._costs_path(budget_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        return [CostEntry.model_validate(d) for d in data]

    def delete_cost_entry(self, budget_id: str, entry_id: str) -> bool:
        entries = self.load_cost_entries(budget_id)
        new_entries = [e for e in entries if e.id != entry_id]
        if len(new_entries) == len(entries):
            return False
        path = self._costs_path(budget_id)
        path.write_text(
            json.dumps([e.model_dump(mode="json") for e in new_entries], indent=2, default=_default_serializer)
        )
        return True

    def load_cost_entries_by_date_range(
        self, budget_id: str, start: datetime, end: datetime
    ) -> list[CostEntry]:
        entries = self.load_cost_entries(budget_id)
        return [e for e in entries if start <= e.timestamp <= end]

    # ── Index ──────────────────────────────────────────────────────────────

    def _update_index(self, budget: Budget) -> None:
        index = self._load_index()
        index[budget.id] = {
            "name": budget.name,
            "category": budget.category.value,
            "status": budget.status.value,
            "limit": budget.limit,
            "spent": budget.spent,
            "currency": budget.currency,
        }
        self._save_index(index)

    def _remove_from_index(self, budget_id: str) -> None:
        index = self._load_index()
        index.pop(budget_id, None)
        self._save_index(index)

    def _load_index(self) -> dict:
        path = self._index_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_index(self, index: dict) -> None:
        self._ensure_dirs()
        path = self._index_path()
        path.write_text(json.dumps(index, indent=2, default=_default_serializer))
