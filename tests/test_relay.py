"""Tests for the TradingView webhook relay.

Covers:
- Pydantic model validation
- Each risk filter in isolation
- The full filter pipeline
- Dryrun backend
- Order ID determinism (idempotency on retries)
- End-to-end webhook flow with FastAPI TestClient
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

# Set required env var BEFORE importing the relay (Config reads at import)
os.environ.setdefault("TV_WEBHOOK_SECRET", "test-secret-12345")

from tv_relay.backends import DryrunBackend, alert_to_order
from tv_relay.config import Config
from tv_relay.filters import (
    filter_size_cap,
    filter_strategy_allowlist,
    filter_symbol_allowlist,
    filter_trading_hours,
    run_filters,
)
from tv_relay.models import OrderType, Side, TVAlert
from tv_relay.server import create_app, main, parse_args


def make_alert(**overrides) -> TVAlert:
    """Build a default valid alert with optional overrides."""
    defaults = dict(
        secret="test-secret-12345",
        strategy="test-strategy",
        symbol="BTCUSDT",
        side="buy",
        size_usd=Decimal("1000"),
        order_type=OrderType.MARKET,
    )
    defaults.update(overrides)
    return TVAlert(**defaults)


# -----------------------------------------------------------------------------
# TVAlert validation
# -----------------------------------------------------------------------------

class TestTVAlert:
    def test_minimal_valid_alert(self):
        alert = TVAlert(
            secret="test-secret-12345",
            strategy="s1",
            symbol="BTCUSDT",
            side="buy",
        )
        assert alert.size_usd == Decimal("0")
        assert alert.order_type == OrderType.MARKET

    def test_symbol_normalized(self):
        alert = make_alert(symbol="btc/usdt")
        assert alert.symbol == "BTCUSDT"

    def test_size_must_be_non_negative(self):
        with pytest.raises(ValueError):
            make_alert(size_usd=Decimal("-100"))

    def test_secret_must_be_min_length(self):
        with pytest.raises(ValueError):
            make_alert(secret="short")

    def test_extra_fields_allowed(self):
        # TV alerts often include extra context fields like price, time, etc.
        alert = TVAlert(
            secret="test-secret-12345",
            strategy="s1",
            symbol="BTCUSDT",
            side="buy",
            extra_context="some-tv-thing",
        )
        # Doesn't raise


# -----------------------------------------------------------------------------
# Individual filters
# -----------------------------------------------------------------------------

class TestFilters:
    def test_size_cap_accepts_under_limit(self):
        config = Config()
        alert = make_alert(size_usd=Decimal("500"))
        result = filter_size_cap(alert, config)
        assert result.accepted

    def test_size_cap_rejects_over_limit(self):
        config = Config()
        # Default max is 10000, send 50000
        alert = make_alert(size_usd=Decimal("50000"))
        result = filter_size_cap(alert, config)
        assert not result.accepted
        assert result.rejected_by == "size_cap"

    def test_symbol_allowlist_no_config_passes_all(self):
        config = Config()
        alert = make_alert(symbol="ANYTHING")
        result = filter_symbol_allowlist(alert, config)
        assert result.accepted

    def test_symbol_allowlist_rejects_missing(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
        config = Config()
        alert = make_alert(symbol="DOGEUSDT")
        result = filter_symbol_allowlist(alert, config)
        assert not result.accepted
        assert result.rejected_by == "symbol_allowlist"

    def test_symbol_allowlist_accepts_listed(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
        config = Config()
        alert = make_alert(symbol="ETHUSDT")
        result = filter_symbol_allowlist(alert, config)
        assert result.accepted

    def test_trading_hours_24_7_passes(self):
        config = Config()  # default 0 to 24
        alert = make_alert()
        # Pass an arbitrary timestamp
        result = filter_trading_hours(alert, config, now_utc=datetime(2024, 1, 1, 3, tzinfo=timezone.utc))
        assert result.accepted

    def test_trading_hours_outside_window_rejects(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_HOURS_UTC_START", "9")
        monkeypatch.setenv("ALLOWED_HOURS_UTC_END", "17")
        config = Config()
        alert = make_alert()
        # 03:00 UTC is outside [9, 17)
        now = datetime(2024, 1, 1, 3, tzinfo=timezone.utc)
        result = filter_trading_hours(alert, config, now_utc=now)
        assert not result.accepted
        assert result.rejected_by == "trading_hours"

    def test_trading_hours_inside_window_accepts(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_HOURS_UTC_START", "9")
        monkeypatch.setenv("ALLOWED_HOURS_UTC_END", "17")
        config = Config()
        alert = make_alert()
        now = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        result = filter_trading_hours(alert, config, now_utc=now)
        assert result.accepted

    def test_trading_hours_wraparound_window(self, monkeypatch):
        # 22:00 to 04:00 UTC (overnight)
        monkeypatch.setenv("ALLOWED_HOURS_UTC_START", "22")
        monkeypatch.setenv("ALLOWED_HOURS_UTC_END", "4")
        config = Config()
        alert = make_alert()

        # 23:00 should be in window
        result = filter_trading_hours(
            alert, config, now_utc=datetime(2024, 1, 1, 23, tzinfo=timezone.utc),
        )
        assert result.accepted

        # 02:00 should be in window (other side of midnight)
        result = filter_trading_hours(
            alert, config, now_utc=datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
        )
        assert result.accepted

        # 12:00 should NOT be in window
        result = filter_trading_hours(
            alert, config, now_utc=datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
        )
        assert not result.accepted


# -----------------------------------------------------------------------------
# Filter pipeline
# -----------------------------------------------------------------------------

class TestPipeline:
    def test_accepts_when_all_pass(self):
        config = Config()
        alert = make_alert(size_usd=Decimal("500"))
        result = run_filters(alert, config)
        assert result.accepted

    def test_short_circuits_on_first_rejection(self):
        config = Config()
        # Size too large; should reject at size_cap, not check later filters
        alert = make_alert(size_usd=Decimal("99999999"))
        result = run_filters(alert, config)
        assert not result.accepted
        assert result.rejected_by == "size_cap"


# -----------------------------------------------------------------------------
# Order construction & idempotency
# -----------------------------------------------------------------------------

class TestOrderConstruction:
    def test_alert_to_order_basic_fields(self):
        alert = make_alert()
        order = alert_to_order(alert)
        assert order.symbol == "BTCUSDT"
        assert order.side == Side.BUY
        assert order.size_usd == Decimal("1000")

    def test_client_order_id_is_deterministic(self):
        """Identical alerts should produce identical client_order_ids.

        This ensures duplicate webhook deliveries from TV (which retries on
        timeouts) don't create duplicate orders.
        """
        alert_a = make_alert()
        alert_b = make_alert()
        order_a = alert_to_order(alert_a)
        order_b = alert_to_order(alert_b)
        assert order_a.client_order_id == order_b.client_order_id

    def test_client_order_id_differs_for_different_alerts(self):
        order_a = alert_to_order(make_alert(side="buy"))
        order_b = alert_to_order(make_alert(side="sell"))
        assert order_a.client_order_id != order_b.client_order_id


# -----------------------------------------------------------------------------
# Dryrun backend
# -----------------------------------------------------------------------------

class TestDryrunBackend:
    async def test_dryrun_returns_dryrun_status(self):
        backend = DryrunBackend()
        alert = make_alert()
        order = alert_to_order(alert)
        response = await backend.submit(order)
        assert response["status"] == "dryrun"
        assert response["client_order_id"] == order.client_order_id

    async def test_dryrun_logs_one_clear_line_per_order(self, caplog):
        """DryrunBackend.submit must log exactly one INFO line documenting
        the accepted order, in the format the README promises."""
        backend = DryrunBackend()
        alert = make_alert()
        order = alert_to_order(alert)
        with caplog.at_level("INFO", logger="tv_relay.backends"):
            await backend.submit(order)

        relay_records = [r for r in caplog.records if r.name == "tv_relay.backends"]
        assert len(relay_records) == 1
        message = relay_records[0].getMessage()
        assert message.startswith("DRYRUN order:")
        assert order.side.value in message
        assert order.symbol in message
        assert order.order_type.value in message
        assert str(order.size_usd) in message
        assert order.strategy in message
        assert f"cid={order.client_order_id}" in message


# -----------------------------------------------------------------------------
# End-to-end webhook flow
# -----------------------------------------------------------------------------

class TestWebhookEndpoint:
    @pytest.fixture
    def client(self):
        app = create_app()
        with TestClient(app) as c:
            yield c

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_webhook_rejects_invalid_json(self, client):
        response = client.post(
            "/webhook",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_webhook_rejects_missing_secret(self, client):
        response = client.post(
            "/webhook",
            json={"strategy": "s1", "symbol": "BTC", "side": "buy"},
        )
        # Pydantic rejects before secret check
        assert response.status_code == 400

    def test_webhook_rejects_wrong_secret(self, client):
        response = client.post(
            "/webhook",
            json={
                "secret": "wrong-secret-here",
                "strategy": "s1",
                "symbol": "BTCUSDT",
                "side": "buy",
            },
        )
        assert response.status_code == 401

    def test_webhook_accepts_valid_alert(self, client):
        response = client.post(
            "/webhook",
            json={
                "secret": "test-secret-12345",
                "strategy": "s1",
                "symbol": "BTCUSDT",
                "side": "buy",
                "size_usd": 500,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert "client_order_id" in body
        assert body["backend_response"]["status"] == "dryrun"

    def test_webhook_rejects_oversized_alert(self, client):
        response = client.post(
            "/webhook",
            json={
                "secret": "test-secret-12345",
                "strategy": "s1",
                "symbol": "BTCUSDT",
                "side": "buy",
                "size_usd": 99999999,
            },
        )
        assert response.status_code == 200  # 200 so TV doesn't retry
        body = response.json()
        assert body["status"] == "rejected"
        assert body["rejected_by"] == "size_cap"

    def test_webhook_idempotent_on_duplicate_delivery(self, client):
        """Same alert sent twice should produce the same client_order_id."""
        payload = {
            "secret": "test-secret-12345",
            "strategy": "s1",
            "symbol": "BTCUSDT",
            "side": "buy",
            "size_usd": 500,
        }
        r1 = client.post("/webhook", json=payload)
        r2 = client.post("/webhook", json=payload)
        assert r1.json()["client_order_id"] == r2.json()["client_order_id"]


# -----------------------------------------------------------------------------
# CLI entry point (`tv-relay` console script -> tv_relay.server:main)
# -----------------------------------------------------------------------------

class TestCLI:
    def test_parse_args_defaults(self):
        args = parse_args([])
        assert args.host == "0.0.0.0"
        assert args.port == 8080

    def test_parse_args_overrides(self):
        args = parse_args(["--host", "127.0.0.1", "--port", "9000"])
        assert args.host == "127.0.0.1"
        assert args.port == 9000

    def test_parse_args_port_is_int(self):
        args = parse_args(["--port", "3000"])
        assert isinstance(args.port, int)
        assert args.port == 3000

    def test_main_runs_uvicorn_with_parsed_args(self, monkeypatch):
        """Smoke test of the `tv-relay` entry point itself: this is the
        console script pyproject.toml points at, so it must actually be
        callable with no arguments and must not crash the way the old
        `tv_relay.server:app` target did (TypeError on ASGI __call__).
        """
        calls = []

        def fake_run(app, *, host, port):
            calls.append({"app": app, "host": host, "port": port})

        monkeypatch.setattr("uvicorn.run", fake_run)

        main(["--host", "127.0.0.1", "--port", "9001"])

        assert len(calls) == 1
        assert calls[0]["host"] == "127.0.0.1"
        assert calls[0]["port"] == 9001

    def test_main_defaults_with_no_args(self, monkeypatch):
        calls = []

        def fake_run(app, *, host, port):
            calls.append({"app": app, "host": host, "port": port})

        monkeypatch.setattr("uvicorn.run", fake_run)

        main([])

        assert calls[0]["host"] == "0.0.0.0"
        assert calls[0]["port"] == 8080
