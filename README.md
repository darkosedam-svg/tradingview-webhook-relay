# tradingview-webhook-relay

[![CI](https://github.com/darkosedam-svg/tradingview-webhook-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/darkosedam-svg/tradingview-webhook-relay/actions/workflows/ci.yml)

FastAPI relay that receives TradingView alerts and routes them through
configurable risk filters to an exchange backend. Today the only working
backend is a dryrun logger — the Hyperliquid backend is a stub. See
"Backend status" below before you point real money at this.

Built for traders who run TradingView strategies and want a relay they
control instead of trusting random Discord bots or paying $50/month for a
black-box copy-trade service.

## Features

- ✅ FastAPI server
- ✅ Shared-secret authentication, compared in constant time (`hmac.compare_digest`) — not HMAC-signed request bodies, just a secret field the alert must match
- ✅ Pluggable risk filter pipeline (size cap, symbol allowlist, strategy allowlist, trading hours)
- ✅ Deterministic client order IDs — the ID is a SHA-256 hash of the alert's strategy/symbol/side/size/order_type, so a duplicate webhook delivery (TV retry) produces the same ID and an exchange can de-duplicate it
- ✅ Dryrun mode (default) — logs the order and returns a fake response; no exchange is called
- ⚠️ Hyperliquid backend is a stub — `HyperliquidBackend.submit()` raises `NotImplementedError` today

## Backend status

| Backend | Status |
|---|---|
| `dryrun` | Works. Validates the alert, runs risk filters, logs and returns the constructed order. No exchange call. |
| `hyperliquid` | Not implemented. Selecting `EXCHANGE=hyperliquid` gets you a 501 on every webhook until `HyperliquidBackend.submit()` is wired up to `hyperliquid-execution-toolkit` (which itself is early-stage — see that repo). |

## Install

Not on PyPI yet. Install from GitHub:

```bash
pip install "git+https://github.com/darkosedam-svg/tradingview-webhook-relay.git"
```

Or from source:

```bash
git clone https://github.com/darkosedam-svg/tradingview-webhook-relay
cd tradingview-webhook-relay
pip install -e .[dev]
```

## Quick start

### 1. Set up your environment

```bash
export TV_WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "Save this secret: $TV_WEBHOOK_SECRET"

# Optional but recommended
export MAX_POSITION_SIZE_USD=5000
export ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
export ALLOWED_STRATEGIES=btc-momentum-v1,eth-mean-reversion

# Start in dryrun mode (no real orders)
export EXCHANGE=dryrun
```

### 2. Run the relay

```bash
tv-relay --port 8080
```

Or programmatically:

```python
import logging
import uvicorn
from tv_relay.server import app

logging.basicConfig(level=logging.INFO)  # `tv-relay` does this for you; you must do it yourself here
uvicorn.run(app, host="0.0.0.0", port=8080)
```

### 3. Configure your TradingView alert

In TradingView, set the alert's webhook URL to:

```
https://your-server.com/webhook
```

Set the alert message to JSON:

```json
{
  "secret": "YOUR_TV_WEBHOOK_SECRET_HERE",
  "strategy": "btc-momentum-v1",
  "symbol": "BTCUSDT",
  "side": "{{strategy.order.action}}",
  "size_usd": 1000,
  "order_type": "market",
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05
}
```

TV will substitute `{{strategy.order.action}}` with `buy` or `sell` automatically. Other [TV alert variables](https://www.tradingview.com/support/solutions/43000531021) work in any field.

Note: `stop_loss_pct` and `take_profit_pct` above are accepted (the model allows extra fields) but currently **ignored** — there is no SL/TP logic in the relay or the dryrun backend yet. Include them in your alert if you want, but don't rely on them doing anything.

### 4. Test the flow

```bash
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "YOUR_SECRET",
    "strategy": "test",
    "symbol": "BTCUSDT",
    "side": "buy",
    "size_usd": 100
  }'
```

`tv-relay` configures INFO-level logging on startup, so you should see this
in the relay logs (real output, from running the exact command above with
the exact payload above):

```
INFO tv_relay.backends: DRYRUN order: buy BTCUSDT market $100 (strategy=test, cid=4ac05f69d10a52273aa07202e451de3c)
INFO tv_relay.server: Alert accepted: buy BTCUSDT $100 strategy=test cid=4ac05f69d10a52273aa07202e451de3c
```

`cid` is the first 32 hex characters of a SHA-256 hash of the order's
strategy/symbol/side/size/order_type — a bare hex string, not prefixed with
`tv-`.

In dryrun mode no real order is sent — only logged. Dryrun is the only
backend that works today; there is no real exchange backend to switch to
yet (see "Backend status" above).

## Configuration reference

All config is loaded from environment variables.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TV_WEBHOOK_SECRET` | ✅ | — | Shared secret matched against alert payload |
| `EXCHANGE` | | `dryrun` | Backend: `dryrun`, `hyperliquid` |
| `MAX_POSITION_SIZE_USD` | | `10000` | Reject alerts above this size |
| `MAX_DAILY_LOSS_USD` | | `1000` | Daily loss circuit breaker (planned) |
| `ALLOWED_SYMBOLS` | | (all) | Comma-separated symbol allowlist |
| `ALLOWED_STRATEGIES` | | (all) | Comma-separated strategy allowlist |
| `ALLOWED_HOURS_UTC_START` | | `0` | Trading window start hour (UTC) |
| `ALLOWED_HOURS_UTC_END` | | `24` | Trading window end hour (UTC) |

## Security notes

**1. The secret matters.** This is a shared secret compared with `hmac.compare_digest` (constant-time, to avoid timing attacks) — it is not a signed payload, so anyone who has the secret can fire alerts. Generate it with `openssl rand -hex 32`. Don't commit it to git.

**2. Use HTTPS in production.** Webhook URLs go through TradingView's servers; HTTP traffic is observable.

**3. Use a strict symbol/strategy allowlist.** Even if the secret leaks, the attacker can only fire alerts for whitelisted symbols/strategies.

**4. Set a reasonable size cap.** The size cap is your last line of defense. A misconfigured TV alert (or a compromised secret) trying to fire $1M orders will hit the cap and reject.

**5. Run in dryrun for at least 48 hours.** Watch the logs. Make sure every alert routes the way you expect. (There is no live backend to switch to yet — see "Backend status" above. This is future work.)

## Adding a new exchange backend

Implement the `ExchangeBackend` protocol:

```python
from tv_relay.models import ExchangeOrder

class MyExchangeBackend:
    async def submit(self, order: ExchangeOrder) -> dict:
        # Convert order to your exchange's format
        # Submit via your exchange's API
        # Return the response
        ...
```

Then register it in `get_backend` in `tv_relay/backends.py`.

## Adding a new risk filter

Filters are functions:

```python
from tv_relay.config import Config
from tv_relay.models import RiskCheckResult, TVAlert

def filter_my_check(alert: TVAlert, config: Config) -> RiskCheckResult:
    if some_condition_we_dont_like(alert):
        return RiskCheckResult(
            accepted=False,
            reason="explanation for the user",
            rejected_by="my_check",
        )
    return RiskCheckResult(accepted=True)
```

Add it to `DEFAULT_FILTERS` in `tv_relay/filters.py` (or pass a custom list to `run_filters`).

## Testing

```bash
pytest tests/
```

34 tests should pass. The test suite covers:

- TV alert validation (Pydantic models)
- Each filter in isolation
- The full filter pipeline
- Order ID determinism (idempotency on TV retries)
- Authentication
- End-to-end webhook flow
- The dryrun backend's log line
- The `tv-relay` CLI's argument parsing and entry point

## What this library is NOT

- ❌ Not a strategy. Bring your own TradingView Pine script.
- ❌ Not a backtesting tool.
- ❌ Not a copy-trading service. Run it on YOUR server with YOUR API keys.
- ❌ Not a guarantee of profit. Even with risk filters, algorithmic trading involves substantial loss risk.

## Related projects

- [`hyperliquid-execution-toolkit`](https://github.com/darkosedam-svg/hyperliquid-execution-toolkit) — the execution layer this relay is meant to route orders through when `EXCHANGE=hyperliquid`; note that both the relay's Hyperliquid backend and much of that toolkit's client are still unimplemented
- [`ict-smc-detector`](https://github.com/darkosedam-svg/ict-smc-detector) — pattern detection for ICT/SMC concepts on OHLCV data

## Hire me

I build and harden trading infrastructure: execution engines, exchange connectors, backtesting pipelines, and alert/webhook relays. Available for custom work and ongoing retainers around trading-infrastructure, execution, and backtesting engineering.

Contact: jessuskrist84@gmail.com

## License

MIT.

## Author

Darko Vlahovic — independent algo-trading systems engineer.

- 🌐 [github.com/darkosedam-svg](https://github.com/darkosedam-svg)
- ✉️ [Email](mailto:jessuskrist84@gmail.com)

Available for paid work — custom strategy implementation, backend integration, full TV-to-exchange systems.
