"""Pydantic models for the TradingView webhook relay.

TVAlert is the primary ingress model — it validates every incoming
webhook payload. Extra fields are allowed because TradingView alerts
often include extra context (bar time, price, indicator values, etc.).
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TVAlert(BaseModel):
    """Validated TradingView alert payload.

    Required fields:
        secret:    Shared secret for HMAC authentication (min 8 chars).
        strategy:  Strategy name/ID that fired the alert.
        symbol:    Trading pair, normalised to uppercase without separators.
        side:      "buy" or "sell".

    Optional fields:
        size_usd:   Position size in USD (default 0 = signal-only mode).
        order_type: "market" (default) or "limit".

    Extra fields are silently accepted and ignored so TV alerts with
    additional context (e.g., {{close}}, {{time}}) pass validation.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    secret: str = Field(..., min_length=8)
    strategy: str
    symbol: str
    side: Literal["buy", "sell"]
    size_usd: Decimal = Field(default=Decimal("0"))
    order_type: OrderType = Field(default=OrderType.MARKET)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalise_symbol(cls, v: str) -> str:
        """Normalise BTC/USDT, btc/usdt, btcusdt → BTCUSDT."""
        return v.upper().replace("/", "").replace("-", "").replace("_", "")

    @field_validator("size_usd", mode="before")
    @classmethod
    def coerce_size(cls, v) -> Decimal:
        return Decimal(str(v))

    @model_validator(mode="after")
    def check_size_non_negative(self) -> TVAlert:
        if self.size_usd < Decimal("0"):
            raise ValueError(f"size_usd must be non-negative; got {self.size_usd}")
        return self


class RiskCheckResult(BaseModel):
    """Result of a single risk filter evaluation."""

    accepted: bool
    rejected_by: Optional[str] = None
    reason: Optional[str] = None


class ExchangeOrder(BaseModel):
    """Normalised order ready for submission to an exchange backend."""

    symbol: str
    side: Side
    size_usd: Decimal
    order_type: OrderType
    strategy: str
    client_order_id: str
