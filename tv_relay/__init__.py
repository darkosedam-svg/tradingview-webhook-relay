"""tradingview-webhook-relay — TV alert to exchange-order relay (dry-run backend only)."""

from .backends import DryrunBackend, alert_to_order, get_backend
from .config import Config
from .models import ExchangeOrder, OrderType, RiskCheckResult, Side, TVAlert
from .server import create_app

__all__ = [
    "create_app",
    "alert_to_order",
    "get_backend",
    "Config",
    "DryrunBackend",
    "ExchangeOrder",
    "OrderType",
    "RiskCheckResult",
    "Side",
    "TVAlert",
]

__version__ = "1.0.0"
