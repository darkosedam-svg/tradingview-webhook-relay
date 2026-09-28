"""Configuration for the TradingView webhook relay.

All settings are read from environment variables so the relay is
12-factor compliant and trivially configurable in Docker/K8s.

Environment Variables
---------------------
TV_WEBHOOK_SECRET       Required — shared secret checked with hmac.compare_digest
                        (constant-time comparison of a plain secret field,
                        not an HMAC-signed request body).
EXCHANGE                Exchange backend to use (default: "dryrun").
MAX_POSITION_SIZE_USD   Maximum allowed position size per alert (default: 10000).
MAX_DAILY_LOSS_USD      Daily loss cap before circuit-breaker trips (default: 1000).
ALLOWED_SYMBOLS         Comma-separated allowlist (default: empty = allow all).
ALLOWED_STRATEGIES      Comma-separated allowlist (default: empty = allow all).
ALLOWED_HOURS_UTC_START Start hour for trading window, 0-23 (default: 0).
ALLOWED_HOURS_UTC_END   End hour for trading window, 0-24 (default: 24).
                        Setting both to 0 and 24 means 24/7.
                        Wrap-around is supported (e.g., start=22, end=4).
"""

from __future__ import annotations

import os
from decimal import Decimal


class Config:
    """Runtime configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.webhook_secret: str = os.getenv("TV_WEBHOOK_SECRET", "")
        self.exchange: str = os.getenv("EXCHANGE", "dryrun")

        self.max_position_size_usd: Decimal = Decimal(
            os.getenv("MAX_POSITION_SIZE_USD", "10000")
        )
        self.max_daily_loss_usd: Decimal = Decimal(
            os.getenv("MAX_DAILY_LOSS_USD", "1000")
        )

        raw_symbols = os.getenv("ALLOWED_SYMBOLS", "")
        self.allowed_symbols: set[str] = {
            s.strip().upper() for s in raw_symbols.split(",") if s.strip()
        }

        raw_strategies = os.getenv("ALLOWED_STRATEGIES", "")
        self.allowed_strategies: set[str] = {
            s.strip() for s in raw_strategies.split(",") if s.strip()
        }

        self.allowed_hours_utc_start: int = int(
            os.getenv("ALLOWED_HOURS_UTC_START", "0")
        )
        self.allowed_hours_utc_end: int = int(
            os.getenv("ALLOWED_HOURS_UTC_END", "24")
        )

    @property
    def has_symbol_allowlist(self) -> bool:
        return bool(self.allowed_symbols)

    @property
    def has_strategy_allowlist(self) -> bool:
        return bool(self.allowed_strategies)
