"""Tests for v0.7.0 Guardrail Webhooks & Smart Projection Check."""

import pytest
import tempfile
import json
import hashlib
import hmac
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from agent_budget.models import (
    WebhookConfig,
    WebhookDelivery,
    WebhookEvent,
    WebhookConfig,
    ProjectionIntegration,
    CostGuardrail,
    GuardrailScope,
    GuardrailAction,
    GuardrailDecision,
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


NOW = datetime(2025, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


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


# =========================================================================
# Model Tests
# =========================================================================

class TestWebhookModels:
    def test_webhook_config_defaults(self):
        wh = WebhookConfig(name="test", url="https://example.com/hook")
        assert wh.id.startswith("WHK-")
        assert wh.name == "test"
        assert wh.url == "https://example.com/hook"
        assert wh.enabled is True
        assert wh.max_retries == 3
        assert wh.timeout_seconds == 10.0
        assert wh.headers == {}
        assert wh.secret is None
        assert wh.scope is None
        assert wh.scope_id is None
        # Default events = all
        assert len(wh.events) == len(list(WebhookEvent))
        assert WebhookEvent.GUARDRAIL_WARN in wh.events
        assert WebhookEvent.GUARDRAIL_BLOCK in wh.events
        assert WebhookEvent.KILL_SWITCH_TRIGGERED in wh.events

    def test_webhook_config_requires_name(self):
        with pytest.raises(Exception):
            WebhookConfig(name="", url="https://example.com")

    def test_webhook_config_requires_url(self):
        with pytest.raises(Exception):
            WebhookConfig(name="test", url="")

    def test_webhook_config_with_events(self):
        wh = WebhookConfig(
            name="Slack alerts",
            url="https://hooks.slack.com/services/...",
            events=[WebhookEvent.GUARDRAIL_WARN, WebhookEvent.GUARDRAIL_BLOCK],
        )
        assert len(wh.events) == 2
        assert WebhookEvent.GUARDRAIL_WARN in wh.events
        assert WebhookEvent.GUARDRAIL_BLOCK in wh.events
        assert WebhookEvent.KILL_SWITCH_TRIGGERED not in wh.events

    def test_webhook_config_with_scope_filter(self):
        wh = WebhookConfig(
            name="Agent watcher",
            url="https://example.com",
            scope=GuardrailScope.AGENT,
            scope_id="agent-007",
        )
        assert wh.scope == GuardrailScope.AGENT
        assert wh.scope_id == "agent-007"

    def test_webhook_config_max_retries_bounds(self):
        with pytest.raises(Exception):
            WebhookConfig(name="t", url="https://e.com", max_retries=-1)
        with pytest.raises(Exception):
            WebhookConfig(name="t", url="https://e.com", max_retries=11)

    def test_webhook_config_timeout_bounds(self):
        with pytest.raises(Exception):
            WebhookConfig(name="t", url="https://e.com", timeout_seconds=0.5)
        with pytest.raises(Exception):
            WebhookConfig(name="t", url="https://e.com", timeout_seconds=121.0)

    def test_webhook_config_custom_headers(self):
        wh = WebhookConfig(
            name="t",
            url="https://e.com",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert wh.headers["Authorization"] == "Bearer secret-token"

    def test_webhook_delivery_defaults(self):
        d = WebhookDelivery(
            webhook_id="WHK-ABC123",
            event=WebhookEvent.GUARDRAIL_WARN,
            payload={"test": True},
        )
        assert d.id.startswith("WHD-")
        assert d.webhook_id == "WHK-ABC123"
        assert d.event == WebhookEvent.GUARDRAIL_WARN
        assert d.success is False
        assert d.status_code is None
        assert d.attempt == 1
        assert d.duration_ms == 0.0
        assert d.error is None
        assert d.response_body is None

    def test_webhook_delivery_with_response(self):
        d = WebhookDelivery(
            webhook_id="WHK-ABC",
            event=WebhookEvent.GUARDRAIL_BLOCK,
            payload={"spend": 100},
            success=True,
            status_code=200,
            response_body="OK",
            attempt=1,
            duration_ms=42.5,
        )
        assert d.success is True
        assert d.status_code == 200
        assert d.response_body == "OK"
        assert d.duration_ms == 42.5

    def test_webhook_event_values(self):
        assert WebhookEvent.GUARDRAIL_WARN.value == "guardrail_warn"
        assert WebhookEvent.GUARDRAIL_BLOCK.value == "guardrail_block"
        assert WebhookEvent.GUARDRAIL_KILL.value == "guardrail_kill"
        assert WebhookEvent.KILL_SWITCH_TRIGGERED.value == "kill_switch_triggered"
        assert WebhookEvent.KILL_SWITCH_RESET.value == "kill_switch_reset"
        assert WebhookEvent.PROJECTION_BREACH.value == "projection_breach"
        assert WebhookEvent.LOOP_DETECTED.value == "loop_detected"
        assert WebhookEvent.BUDGET_THRESHOLD.value == "budget_threshold"

    def test_projection_integration_defaults(self):
        pi = ProjectionIntegration()
        assert pi.enabled is False
        assert pi.projected_spend_usd is None
        assert pi.projected_percent is None
        assert pi.projected_exceeds is False
        assert pi.eta_minutes is None
        assert pi.will_breach is False
        assert pi.projection_confidence == 0.0


class TestGuardrailDecisionWebhookFields:
    def test_decision_has_webhook_fields(self):
        d = GuardrailDecision(
            allowed=True,
            action=GuardrailAction.WARN,
            reason="test",
        )
        assert d.webhooks_fired == 0
        assert d.projection is None


# =========================================================================
# Store Tests
# =========================================================================

class TestWebhookStore:
    def test_list_webhooks_empty(self, temp_store):
        assert temp_store.list_webhooks() == []

    def test_save_and_get_webhook(self, temp_store):
        wh = WebhookConfig(name="test", url="https://example.com")
        temp_store.save_webhook(wh)
        retrieved = temp_store.get_webhook(wh.id)
        assert retrieved is not None
        assert retrieved.name == "test"
        assert retrieved.url == "https://example.com"

    def test_get_nonexistent_webhook(self, temp_store):
        assert temp_store.get_webhook("WHK-NONEXISTENT") is None

    def test_save_webhook_updates_existing(self, temp_store):
        wh = WebhookConfig(name="test", url="https://example.com")
        temp_store.save_webhook(wh)
        assert len(temp_store.list_webhooks()) == 1

        # Save again with same ID — should update, not duplicate
        wh.name = "updated"
        temp_store.save_webhook(wh)
        hooks = temp_store.list_webhooks()
        assert len(hooks) == 1
        assert hooks[0].name == "updated"

    def test_delete_webhook(self, temp_store):
        wh = WebhookConfig(name="test", url="https://example.com")
        temp_store.save_webhook(wh)
        assert temp_store.delete_webhook(wh.id) is True
        assert temp_store.get_webhook(wh.id) is None
        assert len(temp_store.list_webhooks()) == 0

    def test_delete_nonexistent_webhook(self, temp_store):
        assert temp_store.delete_webhook("WHK-NONEXISTENT") is False

    def test_list_webhooks_enabled_only(self, temp_store):
        wh1 = WebhookConfig(name="enabled", url="https://e1.com", enabled=True)
        wh2 = WebhookConfig(name="disabled", url="https://e2.com", enabled=False)
        temp_store.save_webhook(wh1)
        temp_store.save_webhook(wh2)

        all_hooks = temp_store.list_webhooks(enabled_only=False)
        assert len(all_hooks) == 2

        enabled = temp_store.list_webhooks(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "enabled"

    def test_save_webhook_delivery(self, temp_store):
        d = WebhookDelivery(
            webhook_id="WHK-TEST",
            event=WebhookEvent.GUARDRAIL_WARN,
            payload={"cost": 50},
            success=True,
            status_code=200,
        )
        temp_store.save_webhook_delivery(d)
        deliveries = temp_store.list_webhook_deliveries()
        assert len(deliveries) == 1
        assert deliveries[0].webhook_id == "WHK-TEST"
        assert deliveries[0].success is True

    def test_list_deliveries_filtered_by_webhook(self, temp_store):
        d1 = WebhookDelivery(webhook_id="WHK-A", event=WebhookEvent.GUARDRAIL_WARN, payload={})
        d2 = WebhookDelivery(webhook_id="WHK-B", event=WebhookEvent.GUARDRAIL_BLOCK, payload={})
        temp_store.save_webhook_delivery(d1)
        temp_store.save_webhook_delivery(d2)

        a_deliveries = temp_store.list_webhook_deliveries(webhook_id="WHK-A")
        assert len(a_deliveries) == 1
        assert a_deliveries[0].webhook_id == "WHK-A"

    def test_list_deliveries_limit(self, temp_store):
        for i in range(10):
            d = WebhookDelivery(
                webhook_id=f"WHK-{i}",
                event=WebhookEvent.GUARDRAIL_WARN,
                payload={},
            )
            temp_store.save_webhook_delivery(d)
        deliveries = temp_store.list_webhook_deliveries(limit=5)
        assert len(deliveries) == 5

    def test_delivery_retention_cap(self, temp_store):
        """Store should cap at 1000 deliveries."""
        for i in range(1005):
            d = WebhookDelivery(
                webhook_id="WHK-TEST",
                event=WebhookEvent.GUARDRAIL_WARN,
                payload={"i": i},
            )
            temp_store.save_webhook_delivery(d)
        deliveries = temp_store.list_webhook_deliveries(limit=10000)
        assert len(deliveries) == 1000


# =========================================================================
# Service Tests — Webhook CRUD
# =========================================================================

class TestWebhookCRUD:
    def test_create_webhook_defaults(self, svc):
        wh = svc.create_webhook(name="Slack", url="https://hooks.slack.com/x")
        assert wh.id.startswith("WHK-")
        assert wh.name == "Slack"
        assert wh.enabled is True
        assert len(wh.events) == len(list(WebhookEvent))

    def test_create_webhook_with_events(self, svc):
        wh = svc.create_webhook(
            name="Slack",
            url="https://hooks.slack.com/x",
            events=["guardrail_warn", "guardrail_block"],
        )
        assert len(wh.events) == 2

    def test_create_webhook_with_invalid_event(self, svc):
        with pytest.raises(ValueError, match="Unknown event type"):
            svc.create_webhook(name="t", url="https://e.com", events=["invalid_event"])

    def test_create_webhook_with_scope(self, svc):
        wh = svc.create_webhook(
            name="Agent watcher",
            url="https://e.com",
            scope="agent",
            scope_id="agent-001",
        )
        assert wh.scope == GuardrailScope.AGENT
        assert wh.scope_id == "agent-001"

    def test_create_webhook_with_invalid_scope(self, svc):
        with pytest.raises(ValueError, match="Unknown scope"):
            svc.create_webhook(name="t", url="https://e.com", scope="invalid_scope")

    def test_create_webhook_with_secret(self, svc):
        wh = svc.create_webhook(name="t", url="https://e.com", secret="mysecret")
        assert wh.secret == "mysecret"

    def test_create_webhook_with_custom_headers(self, svc):
        wh = svc.create_webhook(
            name="t",
            url="https://e.com",
            headers={"Authorization": "Bearer xyz"},
        )
        assert wh.headers["Authorization"] == "Bearer xyz"

    def test_list_webhooks(self, svc):
        svc.create_webhook(name="hook1", url="https://e1.com")
        svc.create_webhook(name="hook2", url="https://e2.com")
        hooks = svc.list_webhooks(enabled_only=False)
        assert len(hooks) == 2

    def test_list_webhooks_enabled_only(self, svc):
        svc.create_webhook(name="hook1", url="https://e1.com")
        svc.create_webhook(name="hook2", url="https://e2.com", enabled=False)
        hooks = svc.list_webhooks(enabled_only=True)
        assert len(hooks) == 1

    def test_get_webhook(self, svc):
        wh = svc.create_webhook(name="test", url="https://e.com")
        retrieved = svc.get_webhook(wh.id)
        assert retrieved is not None
        assert retrieved.name == "test"

    def test_get_nonexistent_webhook(self, svc):
        assert svc.get_webhook("WHK-NONEXISTENT") is None

    def test_delete_webhook(self, svc):
        wh = svc.create_webhook(name="test", url="https://e.com")
        assert svc.delete_webhook(wh.id) is True
        assert svc.get_webhook(wh.id) is None

    def test_delete_nonexistent_webhook(self, svc):
        assert svc.delete_webhook("WHK-NONEXISTENT") is False

    def test_update_webhook(self, svc):
        wh = svc.create_webhook(name="test", url="https://e.com")
        updated = svc.update_webhook(wh.id, name="updated-name", enabled=False)
        assert updated.name == "updated-name"
        assert updated.enabled is False

    def test_update_webhook_events_from_strings(self, svc):
        wh = svc.create_webhook(name="test", url="https://e.com")
        updated = svc.update_webhook(wh.id, events=["guardrail_warn"])
        assert len(updated.events) == 1
        assert updated.events[0] == WebhookEvent.GUARDRAIL_WARN

    def test_update_nonexistent_webhook(self, svc):
        with pytest.raises(ValueError, match="Webhook not found"):
            svc.update_webhook("WHK-NONEXISTENT", name="new")


# =========================================================================
# Service Tests — Webhook Matching
# =========================================================================

class TestWebhookMatching:
    def test_match_all_events(self, svc):
        wh = svc.create_webhook(name="all", url="https://e.com")
        matched = svc._match_webhooks(WebhookEvent.GUARDRAIL_WARN)
        assert len(matched) == 1

    def test_match_specific_event(self, svc):
        wh = svc.create_webhook(
            name="warn-only",
            url="https://e.com",
            events=["guardrail_warn"],
        )
        matched_warn = svc._match_webhooks(WebhookEvent.GUARDRAIL_WARN)
        assert len(matched_warn) == 1

        matched_block = svc._match_webhooks(WebhookEvent.GUARDRAIL_BLOCK)
        assert len(matched_block) == 0

    def test_match_disabled_webhook_excluded(self, svc):
        svc.create_webhook(name="disabled", url="https://e.com", enabled=False)
        matched = svc._match_webhooks(WebhookEvent.GUARDRAIL_WARN)
        assert len(matched) == 0

    def test_match_scope_filtered(self, svc):
        svc.create_webhook(
            name="agent-filter",
            url="https://e.com",
            scope="agent",
            scope_id="agent-007",
        )
        # Matching scope + id
        matched = svc._match_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            scope=GuardrailScope.AGENT,
            scope_id="agent-007",
        )
        assert len(matched) == 1

        # Non-matching scope_id
        matched_wrong = svc._match_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            scope=GuardrailScope.AGENT,
            scope_id="agent-999",
        )
        assert len(matched_wrong) == 0

        # Different scope
        matched_other = svc._match_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            scope=GuardrailScope.MODEL,
            scope_id="agent-007",
        )
        assert len(matched_other) == 0

    def test_match_scope_id_case_insensitive(self, svc):
        svc.create_webhook(
            name="ci",
            url="https://e.com",
            scope="agent",
            scope_id="Agent-007",
        )
        matched = svc._match_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            scope=GuardrailScope.AGENT,
            scope_id="agent-007",
        )
        assert len(matched) == 1

    def test_match_scope_none_fires_for_all(self, svc):
        """Webhook with no scope filter fires for all scopes."""
        svc.create_webhook(name="catch-all", url="https://e.com")
        for scope in GuardrailScope:
            matched = svc._match_webhooks(
                WebhookEvent.GUARDRAIL_WARN,
                scope=scope,
                scope_id="anything",
            )
            assert len(matched) == 1


# =========================================================================
# Service Tests — Webhook Firing (with mock HTTP)
# =========================================================================

class TestWebhookFiring:
    @patch('urllib.request.urlopen')
    def test_fire_webhooks_success(self, mock_urlopen, svc):
        """Test successful webhook delivery."""
        svc.create_webhook(name="test", url="https://example.com/hook")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        fired = svc._fire_webhooks(
            WebhookEvent.GUARDRAIL_WARN,
            {"cost": 50, "message": "test"},
        )
        assert fired == 1
        assert mock_urlopen.call_count == 1

        # Check delivery record
        deliveries = svc.list_webhook_deliveries()
        assert len(deliveries) == 1
        assert deliveries[0].success is True
        assert deliveries[0].status_code == 200

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_hmac_signing(self, mock_urlopen, svc):
        """Test that HMAC signature header is added when secret is set."""
        svc.create_webhook(
            name="signed",
            url="https://example.com/hook",
            secret="mysecret123",
        )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})

        # Check the request was called with the right headers
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        # urllib normalizes header names to X-Title-Case
        sig_header = req.headers.get("X-webhook-signature") or req.headers.get("X-Webhook-signature")
        assert sig_header is not None
        assert sig_header.startswith("sha256=")

        # Verify signature
        body = req.data
        expected_sig = hmac.new(b"mysecret123", body, hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected_sig}"

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_custom_headers_sent(self, mock_urlopen, svc):
        svc.create_webhook(
            name="custom",
            url="https://example.com/hook",
            headers={"X-Custom": "my-value"},
        )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.headers.get("X-custom") == "my-value"
        assert req.headers.get("Content-type") == "application/json"
        assert req.headers.get("X-webhook-event") == "guardrail_warn"

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_no_match(self, mock_urlopen, svc):
        """No webhook registered → 0 fired, no HTTP calls."""
        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 0
        mock_urlopen.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_http_error(self, mock_urlopen, svc):
        """Test webhook delivery when HTTP returns error."""
        svc.create_webhook(name="test", url="https://example.com/hook", max_retries=1)

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.read.return_value = b"Server Error"
        mock_urlopen.return_value = mock_resp

        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 0

        deliveries = svc.list_webhook_deliveries()
        assert len(deliveries) == 1
        assert deliveries[0].success is False
        assert deliveries[0].status_code == 500

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_connection_error(self, mock_urlopen, svc):
        """Test webhook delivery when connection fails."""
        import urllib.error
        svc.create_webhook(name="test", url="https://example.com/hook", max_retries=1)
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 0

        deliveries = svc.list_webhook_deliveries()
        assert deliveries[0].success is False
        assert deliveries[0].error is not None
        assert "Connection refused" in deliveries[0].error

    @patch('urllib.request.urlopen')
    def test_fire_webhooks_4xx_no_retry(self, mock_urlopen, svc):
        """4xx errors should not trigger retry."""
        svc.create_webhook(name="test", url="https://example.com/hook", max_retries=3)

        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.read.return_value = b"Not Found"
        mock_urlopen.return_value = mock_resp

        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 0
        # Should only be called once (no retry on 4xx)
        assert mock_urlopen.call_count == 1

    @patch('urllib.request.urlopen')
    @patch('time.sleep')
    def test_fire_webhooks_5xx_retries(self, mock_sleep, mock_urlopen, svc):
        """5xx errors should trigger retry."""
        svc.create_webhook(name="test", url="https://example.com/hook", max_retries=3)

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.read.return_value = b"Unavailable"
        mock_urlopen.return_value = mock_resp

        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 0
        # Should retry all 3 times
        assert mock_urlopen.call_count == 3

    @patch('urllib.request.urlopen')
    @patch('time.sleep')
    def test_fire_webhooks_retry_then_success(self, mock_sleep, mock_urlopen, svc):
        """Retry should succeed if second attempt works."""
        svc.create_webhook(name="test", url="https://example.com/hook", max_retries=3)

        error_resp = MagicMock()
        error_resp.status = 500
        error_resp.read.return_value = b"Error"

        success_resp = MagicMock()
        success_resp.status = 200
        success_resp.read.return_value = b"OK"

        mock_urlopen.side_effect = [error_resp, success_resp]

        fired = svc._fire_webhooks(WebhookEvent.GUARDRAIL_WARN, {"test": True})
        assert fired == 1
        assert mock_urlopen.call_count == 2

    @patch('urllib.request.urlopen')
    def test_test_webhook(self, mock_urlopen, svc):
        """Test the test_webhook method."""
        wh = svc.create_webhook(name="test", url="https://example.com/hook")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        result = svc.test_webhook(wh.id)
        assert result["fired"] == 1
        assert "Test event sent" in result["message"]

    def test_test_webhook_not_found(self, svc):
        with pytest.raises(ValueError, match="Webhook not found"):
            svc.test_webhook("WHK-NONEXISTENT")


# =========================================================================
# Service Tests — Webhook Integration with Guardrails
# =========================================================================

class TestGuardrailWebhookIntegration:
    @patch('urllib.request.urlopen')
    def test_guardrail_warn_fires_webhook(self, mock_urlopen, svc):
        """Guardrail WARN should fire webhook."""
        svc.create_webhook(
            name="Slack",
            url="https://hooks.slack.com/x",
            events=["guardrail_warn"],
        )

        # Setup guardrail with warn threshold
        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            warn_at_percent=50.0,
            block_at_percent=100.0,
        )

        # Add spend to trigger warn (5/10 = 50%)
        add_llm_usage(svc.store, 5.0)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        decision = svc.check_guardrails(estimated_cost_usd=0.1, now=NOW)
        assert decision.action == GuardrailAction.WARN
        assert decision.webhooks_fired >= 1
        assert mock_urlopen.call_count >= 1

    @patch('urllib.request.urlopen')
    def test_guardrail_block_fires_webhook(self, mock_urlopen, svc):
        """Guardrail BLOCK should fire webhook."""
        svc.create_webhook(
            name="Slack",
            url="https://hooks.slack.com/x",
            events=["guardrail_block"],
        )

        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=10.0,
            warn_at_percent=80.0,
            block_at_percent=100.0,
        )

        # Exceed limit
        add_llm_usage(svc.store, 11.0)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        decision = svc.check_guardrails(estimated_cost_usd=0.1, now=NOW)
        assert decision.action == GuardrailAction.BLOCK
        assert decision.webhooks_fired >= 1

    @patch('urllib.request.urlopen')
    def test_guardrail_allow_no_webhook(self, mock_urlopen, svc):
        """When guardrail allows (all clear), no webhook fires."""
        svc.create_webhook(
            name="Slack",
            url="https://hooks.slack.com/x",
            events=["guardrail_warn", "guardrail_block"],
        )

        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )

        # Low spend, no trigger
        add_llm_usage(svc.store, 1.0)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        decision = svc.check_guardrails(estimated_cost_usd=0.1, now=NOW)
        assert decision.allowed is True
        assert decision.webhooks_fired == 0
        mock_urlopen.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_kill_switch_trigger_fires_webhook(self, mock_urlopen, svc):
        """Triggering kill switch should fire webhook."""
        svc.create_webhook(
            name="PagerDuty",
            url="https://events.pagerduty.com/x",
            events=["kill_switch_triggered"],
        )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        svc.trigger_kill_switch(reason="Manual emergency stop")
        assert mock_urlopen.call_count >= 1

    @patch('urllib.request.urlopen')
    def test_kill_switch_reset_fires_webhook(self, mock_urlopen, svc):
        """Resetting kill switch should fire webhook."""
        svc.create_webhook(
            name="PagerDuty",
            url="https://events.pagerduty.com/x",
            events=["kill_switch_reset"],
        )

        # First trigger
        ks = svc.trigger_kill_switch(reason="test")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        svc.reset_kill_switch(override_token=ks.override_token)
        assert mock_urlopen.call_count >= 1


# =========================================================================
# Service Tests — Smart Projection Check
# =========================================================================

class TestSmartProjectionCheck:
    def test_check_smart_no_projection_needed(self, svc):
        """When spend is low and projection is fine, decision stays ALLOW."""
        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )
        add_llm_usage(svc.store, 5.0)

        decision = svc.check_guardrails_with_projection(
            estimated_cost_usd=0.1,
            use_projection=True,
        )
        assert decision.allowed is True
        # Projection should be populated
        assert decision.projection is not None
        assert decision.projection.enabled is True

    def test_check_smart_without_projection(self, svc):
        """use_projection=False should not populate projection."""
        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )
        add_llm_usage(svc.store, 5.0)

        decision = svc.check_guardrails_with_projection(
            estimated_cost_usd=0.1,
            use_projection=False,
        )
        assert decision.projection is None

    @patch('urllib.request.urlopen')
    def test_check_smart_projection_breach_warns(self, mock_urlopen, svc):
        """High spend rate should trigger projection warning even within limit."""
        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )

        # Add a lot of recent spend to create high rate
        for i in range(10):
            add_llm_usage(svc.store, 8.0, minutes_ago=i * 5)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        decision = svc.check_guardrails_with_projection(
            estimated_cost_usd=0.1,
            now=NOW,
        )

        # Projection should be populated
        assert decision.projection is not None
        assert decision.projection.enabled is True
        # Should have projected spend
        assert decision.projection.projected_spend_usd is not None

    def test_check_smart_projection_silent_failure(self, svc):
        """Projection failure should not break guardrail check."""
        svc.create_guardrail(
            name="daily-limit",
            scope=GuardrailScope.GLOBAL,
            daily_limit_usd=100.0,
        )

        # Mock project_spend to raise an exception
        with patch.object(svc, 'project_spend', side_effect=Exception("projection failed")):
            decision = svc.check_guardrails_with_projection(
                estimated_cost_usd=0.1,
                use_projection=True,
            )
        # Should still return a valid decision
        assert decision is not None
        assert decision.allowed is True


# =========================================================================
# API Server Tests
# =========================================================================

class TestWebhookAPI:
    @pytest.fixture
    def client(self):
        from agent_budget.api_server import create_app, set_service, _service as old_svc
        from agent_budget.service import BudgetService
        from agent_budget.store import BudgetStore
        from fastapi.testclient import TestClient
        import tempfile
        tmpdir = tempfile.mkdtemp()
        store = BudgetStore(data_dir=tmpdir)
        svc = BudgetService(store=store)
        set_service(svc)
        app = create_app()
        client = TestClient(app)
        yield client
        import shutil
        # Restore original service
        import agent_budget.api_server as api_mod
        api_mod._service = None
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_create_webhook_api(self, client):
        resp = client.post("/webhooks", json={
            "name": "Slack alerts",
            "url": "https://hooks.slack.com/services/...",
            "events": ["guardrail_warn", "guardrail_block"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Slack alerts"
        assert data["id"].startswith("WHK-")
        assert len(data["events"]) == 2

    def test_create_webhook_api_default_events(self, client):
        resp = client.post("/webhooks", json={
            "name": "catch-all",
            "url": "https://example.com",
        })
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == len(list(WebhookEvent))

    def test_list_webhooks_api(self, client):
        client.post("/webhooks", json={"name": "hook1", "url": "https://e1.com"})
        client.post("/webhooks", json={"name": "hook2", "url": "https://e2.com"})
        resp = client.get("/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_get_webhook_api(self, client):
        create_resp = client.post("/webhooks", json={"name": "test", "url": "https://e.com"})
        wh_id = create_resp.json()["id"]

        resp = client.get(f"/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test"

    def test_get_webhook_not_found_api(self, client):
        resp = client.get("/webhooks/WHK-NONEXISTENT")
        assert resp.status_code == 404

    def test_delete_webhook_api(self, client):
        create_resp = client.post("/webhooks", json={"name": "test", "url": "https://e.com"})
        wh_id = create_resp.json()["id"]

        resp = client.delete(f"/webhooks/{wh_id}")
        assert resp.status_code == 200

        # Verify gone
        resp = client.get(f"/webhooks/{wh_id}")
        assert resp.status_code == 404

    def test_delete_webhook_not_found_api(self, client):
        resp = client.delete("/webhooks/WHK-NONEXISTENT")
        assert resp.status_code == 404

    def test_webhook_deliveries_api(self, client):
        create_resp = client.post("/webhooks", json={"name": "test", "url": "https://e.com"})
        wh_id = create_resp.json()["id"]

        resp = client.get(f"/webhooks/{wh_id}/deliveries")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @patch('urllib.request.urlopen')
    def test_test_webhook_api(self, mock_urlopen, client):
        create_resp = client.post("/webhooks", json={"name": "test", "url": "https://e.com"})
        wh_id = create_resp.json()["id"]

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_urlopen.return_value = mock_resp

        resp = client.post(f"/webhooks/{wh_id}/test")
        assert resp.status_code == 200
        assert resp.json()["fired"] >= 1

    def test_check_smart_api(self, client):
        resp = client.post("/guardrails/check-smart", json={
            "estimated_cost_usd": 0.05,
            "agent_id": "agent-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data
        assert "action" in data
        assert "projection" in data


# =========================================================================
# MCP Server Tests
# =========================================================================

class TestWebhookMCP:
    def test_mcp_create_webhook(self):
        from agent_budget.mcp_server import create_webhook as mcp_create_webhook
        result = mcp_create_webhook(name="test-mcp", url="https://example.com/hook")
        data = json.loads(result)
        assert data["name"] == "test-mcp"
        assert data["id"].startswith("WHK-")

    def test_mcp_create_webhook_with_events(self):
        from agent_budget.mcp_server import create_webhook as mcp_create_webhook
        result = mcp_create_webhook(
            name="test-mcp",
            url="https://example.com/hook",
            events="guardrail_warn,guardrail_block",
        )
        data = json.loads(result)
        assert len(data["events"]) == 2

    def test_mcp_list_webhooks(self):
        from agent_budget.mcp_server import (
            create_webhook as mcp_create_webhook,
            list_webhooks as mcp_list_webhooks,
        )
        mcp_create_webhook(name="mcp-hook", url="https://e.com")
        result = mcp_list_webhooks(enabled_only=False)
        data = json.loads(result)
        assert len(data) >= 1
        assert any(h["name"] == "mcp-hook" for h in data)

    def test_mcp_delete_webhook(self):
        from agent_budget.mcp_server import (
            create_webhook as mcp_create_webhook,
            delete_webhook as mcp_delete_webhook,
        )
        create_result = mcp_create_webhook(name="to-delete", url="https://e.com")
        wh_id = json.loads(create_result)["id"]

        result = mcp_delete_webhook(webhook_id=wh_id)
        data = json.loads(result)
        assert data["deleted"] is True

    def test_mcp_check_guardrails_smart(self):
        from agent_budget.mcp_server import check_guardrails_smart
        result = check_guardrails_smart(estimated_cost_usd=0.05, agent_id="agent-1")
        data = json.loads(result)
        assert "allowed" in data
        assert "action" in data

    def test_mcp_list_webhook_deliveries(self):
        from agent_budget.mcp_server import list_webhook_deliveries
        result = list_webhook_deliveries(limit=10)
        data = json.loads(result)
        assert isinstance(data, list)
