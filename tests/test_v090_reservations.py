"""Tests for v0.9.0 Concurrency-Safe Reserve/Settle Protocol.

Tests the SpendReservation model, the atomic reserve_and_check operation,
settle/release lifecycle, reservation expiry, and — critically — the
parallel-agent race condition that this feature exists to solve.

The marquee test (test_concurrent_calls_blocked_by_reservations) proves
that N concurrent agents cannot all pass a guardrail check when their
combined spend would exceed the limit, which was the exact bug floe-guard
markets against.
"""

import pytest
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from agent_budget.models import (
    CostGuardrail, GuardrailScope, GuardrailAction, GuardrailDecision,
    SpendReservation, ReservationStatus,
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


NOW = datetime(2025, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def make_guardrail(store, daily_limit=10.0, scope=GuardrailScope.GLOBAL, scope_id=None):
    """Helper to create and store a guardrail."""
    g = CostGuardrail(
        name="Test Guardrail",
        scope=scope,
        scope_id=scope_id,
        daily_limit_usd=daily_limit,
        warn_at_percent=80.0,
        block_at_percent=100.0,
    )
    store.save_guardrail(g)
    return g


# ===================== SpendReservation Model =====================

class TestSpendReservationModel:

    def test_default_reservation_is_active(self):
        rsv = SpendReservation(reserved_amount_usd=0.50)
        assert rsv.status == ReservationStatus.ACTIVE
        assert rsv.is_active(NOW)

    def test_reservation_id_format(self):
        rsv = SpendReservation(reserved_amount_usd=1.0)
        assert rsv.id.startswith("RSV-")

    def test_reservation_expires_after_ttl(self):
        rsv = SpendReservation(
            reserved_amount_usd=1.0,
            expires_at=NOW - timedelta(minutes=1),
        )
        assert not rsv.is_active(NOW)

    def test_settled_reservation_is_not_active(self):
        rsv = SpendReservation(
            reserved_amount_usd=1.0,
            status=ReservationStatus.SETTLED,
        )
        assert not rsv.is_active(NOW)

    def test_released_reservation_is_not_active(self):
        rsv = SpendReservation(
            reserved_amount_usd=1.0,
            status=ReservationStatus.RELEASED,
        )
        assert not rsv.is_active(NOW)

    def test_expired_reservation_is_not_active(self):
        rsv = SpendReservation(
            reserved_amount_usd=1.0,
            status=ReservationStatus.EXPIRED,
        )
        assert not rsv.is_active(NOW)

    def test_reserved_amount_must_be_non_negative(self):
        with pytest.raises(Exception):
            SpendReservation(reserved_amount_usd=-0.01)

    def test_default_ttl_is_5_minutes(self):
        rsv = SpendReservation(reserved_amount_usd=1.0)
        assert (rsv.expires_at - rsv.created_at) >= timedelta(minutes=4, seconds=55)


# ===================== Store Reservation CRUD =====================

class TestStoreReservations:

    def test_save_and_get_reservation(self, temp_store):
        rsv = SpendReservation(reserved_amount_usd=2.50, agent_id="agent-1")
        temp_store.save_reservation(rsv)
        fetched = temp_store.get_reservation(rsv.id)
        assert fetched is not None
        assert fetched.reserved_amount_usd == 2.50
        assert fetched.agent_id == "agent-1"

    def test_get_nonexistent_reservation(self, temp_store):
        assert temp_store.get_reservation("RSV-NONEXIST") is None

    def test_list_reservations_all(self, temp_store):
        for i in range(3):
            temp_store.save_reservation(SpendReservation(reserved_amount_usd=float(i)))
        all_rsv = temp_store.list_reservations()
        assert len(all_rsv) == 3

    def test_list_reservations_active_only(self, temp_store):
        active = SpendReservation(reserved_amount_usd=1.0)
        settled = SpendReservation(reserved_amount_usd=2.0, status=ReservationStatus.SETTLED)
        temp_store.save_reservation(active)
        temp_store.save_reservation(settled)
        actives = temp_store.list_reservations(active_only=True, now=NOW)
        assert len(actives) == 1
        assert actives[0].reserved_amount_usd == 1.0

    def test_list_reservations_by_agent(self, temp_store):
        temp_store.save_reservation(SpendReservation(reserved_amount_usd=1.0, agent_id="alpha"))
        temp_store.save_reservation(SpendReservation(reserved_amount_usd=2.0, agent_id="beta"))
        result = temp_store.list_reservations(agent_id="alpha")
        assert len(result) == 1
        assert result[0].agent_id == "alpha"

    def test_list_reservations_by_status(self, temp_store):
        temp_store.save_reservation(SpendReservation(reserved_amount_usd=1.0, status=ReservationStatus.ACTIVE))
        temp_store.save_reservation(SpendReservation(reserved_amount_usd=2.0, status=ReservationStatus.RELEASED))
        result = temp_store.list_reservations(status=ReservationStatus.RELEASED)
        assert len(result) == 1

    def test_delete_reservation(self, temp_store):
        rsv = SpendReservation(reserved_amount_usd=1.0)
        temp_store.save_reservation(rsv)
        assert temp_store.delete_reservation(rsv.id) is True
        assert temp_store.get_reservation(rsv.id) is None

    def test_delete_nonexistent_returns_false(self, temp_store):
        assert temp_store.delete_reservation("RSV-NOPE") is False

    def test_update_reservation_in_place(self, temp_store):
        rsv = SpendReservation(reserved_amount_usd=1.0)
        temp_store.save_reservation(rsv)
        updated = rsv.model_copy(update={"status": ReservationStatus.SETTLED, "settled_amount_usd": 0.90})
        temp_store.save_reservation(updated)
        fetched = temp_store.get_reservation(rsv.id)
        assert fetched.status == ReservationStatus.SETTLED
        assert fetched.settled_amount_usd == 0.90


# ===================== Expiry =====================

class TestReservationExpiry:

    def test_expire_stale_reservations(self, temp_store):
        stale = SpendReservation(
            reserved_amount_usd=1.0,
            expires_at=NOW - timedelta(minutes=2),
        )
        fresh = SpendReservation(
            reserved_amount_usd=2.0,
            expires_at=NOW + timedelta(minutes=5),
        )
        temp_store.save_reservation(stale)
        temp_store.save_reservation(fresh)

        expired_count = temp_store.expire_stale_reservations(now=NOW)
        assert expired_count == 1

        fetched = temp_store.get_reservation(stale.id)
        assert fetched.status == ReservationStatus.EXPIRED

        fresh_fetched = temp_store.get_reservation(fresh.id)
        assert fresh_fetched.status == ReservationStatus.ACTIVE

    def test_expire_with_no_stale_returns_zero(self, temp_store):
        rsv = SpendReservation(reserved_amount_usd=1.0, expires_at=NOW + timedelta(minutes=5))
        temp_store.save_reservation(rsv)
        assert temp_store.expire_stale_reservations(now=NOW) == 0

    def test_expired_reservation_not_counted_in_active(self, temp_store):
        stale = SpendReservation(
            reserved_amount_usd=5.0,
            expires_at=NOW - timedelta(minutes=1),
        )
        temp_store.save_reservation(stale)
        temp_store.expire_stale_reservations(now=NOW)
        actives = temp_store.list_reservations(active_only=True, now=NOW)
        assert len(actives) == 0


# ===================== Reserve & Check (core feature) =====================

class TestReserveAndCheck:

    def test_reserve_allows_when_under_limit(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        decision, rsv = svc.reserve_and_check(
            estimated_cost_usd=1.0, now=NOW,
        )
        assert decision.allowed is True
        assert rsv is not None
        assert rsv.status == ReservationStatus.ACTIVE
        assert rsv.reserved_amount_usd == 1.0

    def test_reserve_blocked_when_over_limit(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=1.0)
        decision, rsv = svc.reserve_and_check(
            estimated_cost_usd=2.0, now=NOW,
        )
        assert decision.allowed is False
        assert rsv is None

    def test_reservation_counts_againSubsequent_checks(self, svc, temp_store):
        """The key behavior: a reservation reduces available budget for
        the next check."""
        make_guardrail(temp_store, daily_limit=10.0)

        # First call reserves $6
        d1, r1 = svc.reserve_and_check(estimated_cost_usd=6.0, now=NOW)
        assert d1.allowed is True

        # Second call for $5 should now see $6 committed → $11 projected → blocked
        d2, r2 = svc.reserve_and_check(estimated_cost_usd=5.0, now=NOW)
        assert d2.allowed is False
        assert r2 is None

    def test_reserve_with_no_guardrails(self, svc, temp_store):
        decision, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        assert decision.allowed is True
        assert rsv is not None

    def test_reserve_zero_cost(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=1.0)
        decision, rsv = svc.reserve_and_check(estimated_cost_usd=0.0, now=NOW)
        assert decision.allowed is True
        assert rsv is not None
        assert rsv.reserved_amount_usd == 0.0

    def test_reservation_ttl_applied(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(
            estimated_cost_usd=1.0, ttl_minutes=10, now=NOW,
        )
        assert (rsv.expires_at - NOW) >= timedelta(minutes=9, seconds=55)


# ===================== Settle =====================

class TestSettleReservation:

    def test_settle_records_actual_cost(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)

        settled = svc.settle_reservation(
            rsv.id, actual_cost_usd=0.85,
            input_tokens=1000, output_tokens=500,
            model_id="gpt-4o", now=NOW,
        )
        assert settled.status == ReservationStatus.SETTLED
        assert settled.settled_amount_usd == 0.85
        assert settled.usage_record_id is not None

        # Verify the usage record was created
        records = temp_store.list_llm_usage()
        assert len(records) == 1
        assert records[0].cost_usd == 0.85
        assert records[0].model_id == "gpt-4o"

    def test_settle_with_overestimate(self, svc, temp_store):
        """Reserved $1.0, actual $0.3 — settled amount is the real cost."""
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        settled = svc.settle_reservation(rsv.id, actual_cost_usd=0.30, now=NOW)
        assert settled.settled_amount_usd == 0.30

    def test_settle_with_underestimate(self, svc, temp_store):
        """Reserved $0.5, actual $1.5 — the overage is still recorded."""
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=0.5, now=NOW)
        settled = svc.settle_reservation(rsv.id, actual_cost_usd=1.50, now=NOW)
        assert settled.settled_amount_usd == 1.50
        records = temp_store.list_llm_usage()
        assert records[0].cost_usd == 1.50

    def test_settle_nonexistent_raises(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.settle_reservation("RSV-NOPE", actual_cost_usd=1.0)

    def test_settle_already_settled_raises(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        svc.settle_reservation(rsv.id, actual_cost_usd=1.0, now=NOW)
        with pytest.raises(ValueError, match="cannot settle"):
            svc.settle_reservation(rsv.id, actual_cost_usd=1.0, now=NOW)

    def test_settle_carries_metadata(self, svc, temp_store):
        """Settled usage records carry the reservation ID in metadata."""
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(
            estimated_cost_usd=1.0, agent_id="a1", task_id="t1", now=NOW,
        )
        svc.settle_reservation(rsv.id, actual_cost_usd=0.5, now=NOW)
        rec = temp_store.list_llm_usage()[0]
        assert rec.metadata["reservation_id"] == rsv.id
        assert rec.metadata["reserved_amount_usd"] == 1.0


# ===================== Release =====================

class TestReleaseReservation:

    def test_release_returns_budget(self, svc, temp_store):
        """Released reservation no longer counts against the budget."""
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=8.0, now=NOW)
        assert rsv is not None

        # Release it
        released = svc.release_reservation(rsv.id, now=NOW)
        assert released.status == ReservationStatus.RELEASED

        # Now $9 should be allowed again (budget returned)
        d2, _ = svc.reserve_and_check(estimated_cost_usd=9.0, now=NOW)
        assert d2.allowed is True

    def test_release_nonexistent_raises(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.release_reservation("RSV-NOPE")

    def test_release_already_released_raises(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        svc.release_reservation(rsv.id, now=NOW)
        with pytest.raises(ValueError, match="cannot release"):
            svc.release_reservation(rsv.id, now=NOW)

    def test_release_records_reason(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=10.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        released = svc.release_reservation(rsv.id, reason="api_timeout", now=NOW)
        assert released.metadata.get("release_reason") == "api_timeout"


# ===================== THE Race Condition Test =====================

class TestConcurrencySafety:
    """These tests prove the reserve/settle protocol actually prevents
    the parallel-agent fan-out race that floe-guard markets against."""

    def test_concurrent_calls_blocked_by_reservations(self, svc, temp_store):
        """20 concurrent agents each try to reserve $1 against a $10 limit.

        Without reservations, all 20 would read spend=$0, all pass, and
        total spend would hit $20 (2x the limit).  With the lock + reserve
        protocol, only 9 succeed (the 10th would hit exactly 100% = blocked);
        the rest are blocked.  Critically, total committed spend never
        exceeds the limit.
        """
        make_guardrail(temp_store, daily_limit=10.0)

        results = []
        lock = threading.Lock()

        def try_reserve(agent_num):
            decision, rsv = svc.reserve_and_check(
                estimated_cost_usd=1.0,
                agent_id=f"agent-{agent_num}",
                now=NOW,
            )
            with lock:
                results.append((agent_num, decision.allowed, rsv))
            return decision.allowed

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(try_reserve, i) for i in range(20)]
            allowed_count = sum(f.result() for f in as_completed(futures))

        # 9 allowed (9 * $1 = 90% < 100% block threshold; the 10th would
        # project exactly 100% → blocked).
        assert allowed_count == 9, f"Expected 9 allowed, got {allowed_count}"
        assert len(results) == 20

        # Total reserved must not exceed the limit
        total_reserved = sum(
            rsv.reserved_amount_usd for _, _, rsv in results if rsv is not None
        )
        assert total_reserved <= 10.0
        assert total_reserved == pytest.approx(9.0)

        # The other 11 must be blocked
        blocked_count = sum(1 for _, allowed, _ in results if not allowed)
        assert blocked_count == 11

    def test_concurrent_mixed_costs(self, svc, temp_store):
        """10 agents reserve $0.50 each, 10 reserve $1.0 each against $5 limit.

        The protocol must prevent total committed spend from exceeding $5.
        """
        make_guardrail(temp_store, daily_limit=5.0)

        allowed = []
        lock = threading.Lock()

        def try_reserve(cost):
            decision, rsv = svc.reserve_and_check(
                estimated_cost_usd=cost, now=NOW,
            )
            with lock:
                if decision.allowed:
                    allowed.append(cost)
            return decision.allowed

        with ThreadPoolExecutor(max_workers=20) as pool:
            costs = [0.50] * 10 + [1.0] * 10
            futures = [pool.submit(try_reserve, c) for c in costs]
            list(as_completed(futures))

        # Total committed must not exceed $5
        total = sum(allowed)
        assert total <= 5.0 + 1e-9, f"Total committed ${total:.2f} exceeded $5 limit"

    def test_release_allows_new_reservation(self, svc, temp_store):
        """After releasing a reservation, a blocked call becomes allowed."""
        make_guardrail(temp_store, daily_limit=5.0)

        # Reserve $4
        d1, r1 = svc.reserve_and_check(estimated_cost_usd=4.0, now=NOW)
        assert d1.allowed

        # $3 should be blocked ($4 committed + $3 = $7 > $5)
        d2, _ = svc.reserve_and_check(estimated_cost_usd=3.0, now=NOW)
        assert not d2.allowed

        # Release the $4 reservation
        svc.release_reservation(r1.id, now=NOW)

        # Now $3 should be allowed
        d3, _ = svc.reserve_and_check(estimated_cost_usd=3.0, now=NOW)
        assert d3.allowed

    def test_settle_then_check_uses_actual_cost(self, svc, temp_store):
        """After settling, the actual (not reserved) cost counts."""
        make_guardrail(temp_store, daily_limit=5.0)

        # Reserve $4
        _, rsv = svc.reserve_and_check(estimated_cost_usd=4.0, now=NOW)

        # Settle for actual $1 (overestimate)
        svc.settle_reservation(rsv.id, actual_cost_usd=1.0, now=NOW)

        # Now $4 more should be OK (actual spend $1 + new $4 = $5)
        d, _ = svc.reserve_and_check(estimated_cost_usd=4.0, now=NOW)
        assert d.allowed is True

    def test_expired_reservation_does_not_block(self, svc, temp_store):
        """A reservation that past its TTL releases its budget."""
        make_guardrail(temp_store, daily_limit=5.0)

        # Reserve $4 at T=NOW
        _, rsv = svc.reserve_and_check(
            estimated_cost_usd=4.0, ttl_minutes=5, now=NOW,
        )

        # At T=NOW+10min, the reservation has expired
        later = NOW + timedelta(minutes=10)
        # $4 should be allowed again
        d, _ = svc.reserve_and_check(estimated_cost_usd=4.0, now=later)
        assert d.allowed is True


# ===================== Scope-specific Reservations =====================

class TestScopedReservations:

    def test_agent_scoped_reservation(self, svc, temp_store):
        """Reservations for agent-A don't block agent-B."""
        make_guardrail(temp_store, daily_limit=5.0, scope=GuardrailScope.AGENT, scope_id="agent-A")

        d_a, r_a = svc.reserve_and_check(
            estimated_cost_usd=4.0, agent_id="agent-A", now=NOW,
        )
        assert d_a.allowed

        # Agent B has their own $5 limit
        d_b, _ = svc.reserve_and_check(
            estimated_cost_usd=4.0, agent_id="agent-B", now=NOW,
        )
        assert d_b.allowed

    def test_model_scoped_reservation(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=5.0, scope=GuardrailScope.MODEL, scope_id="gpt-4o")

        d1, r1 = svc.reserve_and_check(
            estimated_cost_usd=3.0, model_id="gpt-4o", now=NOW,
        )
        assert d1.allowed

        # Another gpt-4o call should see the reservation
        d2, _ = svc.reserve_and_check(
            estimated_cost_usd=3.0, model_id="gpt-4o", now=NOW,
        )
        assert not d2.allowed  # $3 committed + $3 = $6 > $5

        # A claude call has no guardrail
        d3, _ = svc.reserve_and_check(
            estimated_cost_usd=3.0, model_id="claude-3", now=NOW,
        )
        assert d3.allowed


# ===================== Backward Compatibility =====================

class TestBackwardCompat:

    def test_check_guardrails_still_works_without_reservations(self, svc, temp_store):
        """The old check_guardrails API must still work as before."""
        make_guardrail(temp_store, daily_limit=10.0)

        # Add some settled spend
        rec = LLMUsageRecord(
            model_id="gpt-4o", cost_usd=5.0,
            recorded_at=NOW,
        )
        temp_store.save_llm_usage(rec)

        decision = svc.check_guardrails(
            estimated_cost_usd=4.0, now=NOW,
        )
        # $5 + $4 = $9 < $10, should be allowed
        assert decision.allowed is True

    def test_check_guardrails_does_not_count_reservations(self, svc, temp_store):
        """The legacy check_guardrails must NOT count reservations —
        only reserve_and_check does.  This preserves backward compatibility."""
        make_guardrail(temp_store, daily_limit=10.0)

        # Reserve $8 via the new protocol
        _, rsv = svc.reserve_and_check(estimated_cost_usd=8.0, now=NOW)

        # Legacy check should see $0 settled spend (reservation not counted)
        decision = svc.check_guardrails(
            estimated_cost_usd=5.0, now=NOW,
        )
        assert decision.allowed is True  # $0 + $5 < $10

    def test_get_spend_includes_reservations_flag(self, svc, temp_store):
        """_get_spend_for_period respects the include_reservations flag."""
        make_guardrail(temp_store, daily_limit=10.0)

        _, rsv = svc.reserve_and_check(estimated_cost_usd=3.0, now=NOW)

        day_start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        without = svc._get_spend_for_period(
            GuardrailScope.GLOBAL, None, day_start, NOW,
            include_reservations=False,
        )
        with_rsv = svc._get_spend_for_period(
            GuardrailScope.GLOBAL, None, day_start, NOW,
            include_reservations=True,
        )
        assert without == 0.0
        assert with_rsv == 3.0


# ===================== List / Query =====================

class TestListReservations:

    def test_list_all(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=100.0)
        svc.reserve_and_check(estimated_cost_usd=1.0, agent_id="a1", now=NOW)
        svc.reserve_and_check(estimated_cost_usd=2.0, agent_id="a2", now=NOW)
        all_rsv = svc.list_reservations(now=NOW)
        assert len(all_rsv) == 2

    def test_list_active_only(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=100.0)
        _, r1 = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        svc.release_reservation(r1.id, now=NOW)
        _, r2 = svc.reserve_and_check(estimated_cost_usd=2.0, now=NOW)
        actives = svc.list_reservations(active_only=True, now=NOW)
        assert len(actives) == 1
        assert actives[0].id == r2.id

    def test_get_reservation(self, svc, temp_store):
        make_guardrail(temp_store, daily_limit=100.0)
        _, rsv = svc.reserve_and_check(estimated_cost_usd=1.0, now=NOW)
        fetched = svc.get_reservation(rsv.id)
        assert fetched is not None
        assert fetched.id == rsv.id
