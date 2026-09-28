"""Exchange backend implementations for the webhook relay.

Each backend must implement `async submit(order) -> dict`. The dict
is returned to the caller and logged — its shape is backend-specific.

Adding a new backend:
  1. Create a class with `async def submit(self, order: ExchangeOrder) -> dict`
  2. Register it in `get_backend()`

The DryrunBackend is always available and is the safe default for
testing, CI, and initial deployment before an exchange is wired up.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

from .models import ExchangeOrder, OrderType, Side, TVAlert

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Order construction
# ---------------------------------------------------------------------------

def alert_to_order(alert: TVAlert) -> ExchangeOrder:
    """Convert a validated TVAlert into an ExchangeOrder.

    The ``client_order_id`` is a deterministic hash of the alert's key
    fields. This means duplicate webhook deliveries (TradingView retries
    on timeout) produce the *same* client_order_id, letting the exchange
    de-duplicate them safely.
    """
    # Canonical payload — sorted keys for determinism
    payload = json.dumps(
        {
            "strategy": alert.strategy,
            "symbol": alert.symbol,
            "side": alert.side,
            "size_usd": str(alert.size_usd),
            "order_type": alert.order_type.value,
        },
        sort_keys=True,
    )
    client_order_id = hashlib.sha256(payload.encode()).hexdigest()[:32]

    return ExchangeOrder(
        symbol=alert.symbol,
        side=Side.BUY if alert.side == "buy" else Side.SELL,
        size_usd=alert.size_usd,
        order_type=alert.order_type,
        strategy=alert.strategy,
        client_order_id=client_order_id,
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class DryrunBackend:
    """Safe no-op backend — logs the order, touches no exchange.

    Use this in staging, CI, or when you want to validate the full
    pipeline without risking real funds.

    Logs exactly one INFO line per accepted order, in this format::

        DRYRUN order: <side> <symbol> <order_type> $<size_usd> (strategy=<strategy>, cid=<client_order_id>)

    `client_order_id` is the bare hex string produced by `alert_to_order`
    (see its docstring) — it has no "tv-" prefix.
    """

    async def submit(self, order: ExchangeOrder) -> dict:
        logger.info(
            "DRYRUN order: %s %s %s $%s (strategy=%s, cid=%s)",
            order.side.value,
            order.symbol,
            order.order_type.value,
            order.size_usd,
            order.strategy,
            order.client_order_id,
        )
        return {
            "status": "dryrun",
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "size_usd": str(order.size_usd),
            "order_type": order.order_type.value,
        }


class HyperliquidBackend:
    """Hyperliquid DEX backend (stub — implement with hl_exec client).

    Requires:
        HL_PRIVATE_KEY   Wallet private key for signing orders.
        HL_TESTNET       Set to "1" to use testnet (default: mainnet).
    """

    async def submit(self, order: ExchangeOrder) -> dict:
        raise NotImplementedError(
            "HyperliquidBackend.submit() is not yet implemented. "
            "Install hyperliquid-execution-toolkit and wire up ExecutionClient."
        )


def get_backend(exchange: str) -> DryrunBackend | HyperliquidBackend:
    """Return the backend instance for the given exchange name.

    Args:
        exchange: One of "dryrun", "hyperliquid".

    Returns:
        Backend instance.

    Raises:
        ValueError: If the exchange name is not recognised.
    """
    match exchange.lower():
        case "dryrun" | "":
            return DryrunBackend()
        case "hyperliquid" | "hl":
            return HyperliquidBackend()
        case _:
            raise ValueError(
                f"Unknown exchange backend: {exchange!r}. "
                "Supported: dryrun, hyperliquid"
            )
