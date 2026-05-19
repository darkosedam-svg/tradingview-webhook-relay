# tradingview-webhook-relay

Production-grade FastAPI relay that receives TradingView alerts and routes them through configurable risk filters to crypto exchanges.

Built for traders who run TradingView strategies and want them to execute live without trusting random Discord bots or paying $50/month for a black-box copy-trade service.

## Features

- ✅ FastAPI server, < 100ms latency overhead per alert
- ✅ HMAC secret authentication
- ✅ Pluggable risk filter pipeline (size cap, symbol allowlist, strategy allowlist, trading hours)
- ✅ Idempotent order IDs — duplicate alerts (TV retries) don't double-fill
- ✅ Multiple exchange backends with a unified interface
- ✅ Dryrun mode (default) — test the alert flow without risking money
- ✅ Easy to extend — add new filters or backends in ~30 lines

## Install

```bash
pip install tradingview-webhook-relay
```

Or from source:

```bash
git clone https://github.com/GitBot/tradingview-webhook-relay
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
import uvicorn
from tv_relay.server import app
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

You should see in the relay logs:

```
INFO  Alert accepted: buy BTCUSDT $100 strategy=test cid=tv-...
INFO  DRYRUN order: BUY BTCUSDT market $100 (strategy=test, cid=tv-...)
```

In dryrun mode no real order is sent — only logged. When you're ready, switch `EXCHANGE` to a real backend.

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
| `TELEGRAM_BOT_TOKEN` | | — | Optional: notify on each accepted alert |
| `TELEGRAM_CHAT_ID` | | — | Optional: chat to notify |

## Security notes

**1. The secret matters.** Generate it with `openssl rand -hex 32`. Don't commit it to git.

**2. Use HTTPS in production.** Webhook URLs go through TradingView's servers; HTTP traffic is observable.

**3. Use a strict symbol/strategy allowlist.** Even if the secret leaks, the attacker can only fire alerts for whitelisted symbols/strategies.

**4. Set a reasonable size cap.** The size cap is your last line of defense. A misconfigured TV alert (or a compromised secret) trying to fire $1M orders will hit the cap and reject.

**5. Run in dryrun for at least 48 hours** before switching to a live backend. Watch the logs. Make sure every alert routes the way you expect.

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

27 tests should pass. The test suite covers:

- TV alert validation (Pydantic models)
- Each filter in isolation
- The full filter pipeline
- Order ID determinism (idempotency on TV retries)
- Authentication
- End-to-end webhook flow

## What this library is NOT

- ❌ Not a strategy. Bring your own TradingView Pine script.
- ❌ Not a backtesting tool.
- ❌ Not a copy-trading service. Run it on YOUR server with YOUR API keys.
- ❌ Not a guarantee of profit. Even with risk filters, algorithmic trading involves substantial loss risk.

## Related projects

- [`hyperliquid-execution-toolkit`](https://github.com/GitBot/hyperliquid-execution-toolkit) — the production execution layer this relay routes orders through when `EXCHANGE=hyperliquid`
- [`ict-smc-detector`](https://github.com/GitBot/ict-smc-detector) — pattern detection for ICT/SMC concepts on OHLCV data

## License

MIT.

## Author

Darko Kovačić — independent algo-trading systems engineer. I build production execution infrastructure for crypto perps and DEXs.

- 🌐 [Website](https://jessuskrist84.github.io)
- 🐦 [Twitter](https://twitter.com/jessuskrist84)
- ✉️ [Email](jessuskrist84@gmail.com)

Available for paid work — custom strategy implementation, backend integration, full TV-to-exchange systems. Free 30-min diagnosis on any existing webhook setup.
