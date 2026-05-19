"""Risk filter pipeline for incoming TradingView alerts.

Each filter is a callable that takes an alert + config and returns a
RiskCheckResult. Filters run in order; the first rejection short-circuits.

Filters are independent and easy to add. To add a new one:
  1. Implement a function with signature `(alert, config) -> RiskCheckResult`
  2. Add it to the `DEFAULT_FILTERS` list below

This is the layer that prevents you from waking up to a destroyed account
because a misconfigured TV alert fired 50 times in 2 minutes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .models import RiskCheckResult, TVAlert

RiskFilter = Callable[[TVAlert, Config], RiskCheckResult]


def filter_size_cap(alert: TVAlert, config: Config) -> RiskCheckResult:
    """Reject alerts that exceed the maximum allowed position size."""
    if alert.size_usd > config.max_position_size_usd:
        return RiskCheckResult(
            accepted=False,
            reason=f"Size ${alert.size_usd} exceeds max ${config.max_position_size_usd}",
            rejected_by="size_cap",
        )
    return RiskCheckResult(accepted=True)


def filter_symbol_allowlist(alert: TVAlert, config: Config) -> RiskCheckResult:
    """Reject alerts for symbols not on the allowlist (if one is configured)."""
    if not config.has_symbol_allowlist:
        return RiskCheckResult(accepted=True)
    if alert.symbol not in config.allowed_symbols:
        return RiskCheckResult(
            accepted=False,
            reason=f"Symbol {alert.symbol} not in allowlist",
            rejected_by="symbol_allowlist",
        )
    return RiskCheckResult(accepted=True)


def filter_strategy_allowlist(alert: TVAlert, config: Config) -> RiskCheckResult:
    """Reject alerts from strategies not on the allowlist (if configured)."""
    if not config.has_strategy_allowlist:
        return RiskCheckResult(accepted=True)
    if alert.strategy not in config.allowed_strategies:
        return RiskCheckResult(
            accepted=False,
            reason=f"Strategy {alert.strategy} not in allowlist",
            rejected_by="strategy_allowlist",
        )
    return RiskCheckResult(accepted=True)


def filter_trading_hours(
    alert: TVAlert,
    config: Config,
    *,
    now_utc: datetime | None = None,
) -> RiskCheckResult:
    """Reject alerts outside of allowed UTC trading hours.

    `now_utc` is overrideable for testing.
    """
    now = now_utc or datetime.now(timezone.utc)
    hour = now.hour

    start = config.allowed_hours_utc_start
    end = config.allowed_hours_utc_end

    if start == 0 and end == 24:
        return RiskCheckResult(accepted=True)  # 24/7 trading

    # Handle wrap-around (e.g., start=22, end=4 means 22:00 UTC through 04:00 UTC)
    if start <= end:
        in_window = start <= hour < end
    else:
        in_window = hour >= start or hour < end

    if not in_window:
        return RiskCheckResult(
            accepted=False,
            reason=f"Current UTC hour {hour} outside trading window [{start}, {end})",
            rejected_by="trading_hours",
        )
    return RiskCheckResult(accepted=True)


# Default filter pipeline — runs in this order
DEFAULT_FILTERS: list[RiskFilter] = [
    filter_size_cap,
    filter_symbol_allowlist,
    filter_strategy_allowlist,
    filter_trading_hours,
]


def run_filters(
    alert: TVAlert,
    config: Config,
    filters: list[RiskFilter] | None = None,
) -> RiskCheckResult:
    """Run an alert through the filter pipeline.

    Returns the first rejection, or accepted if all pass.
    """
    pipeline = filters if filters is not None else DEFAULT_FILTERS
    for f in pipeline:
        result = f(alert, config)
        if not result.accepted:
            return result
    return RiskCheckResult(accepted=True)
