"""FastAPI application — the webhook relay server."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .backends import alert_to_order, get_backend
from .config import Config
from .filters import run_filters
from .models import TVAlert

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize state on startup; clean up on shutdown."""
    config = Config()
    backend = get_backend(config.exchange)
    app.state.config = config
    app.state.backend = backend
    logger.info(
        "TV relay starting: exchange=%s, max_size=$%s, daily_loss_cap=$%s",
        config.exchange,
        config.max_position_size_usd,
        config.max_daily_loss_usd,
    )
    yield
    logger.info("TV relay shutting down")


def create_app() -> FastAPI:
    """Construct the FastAPI app. Used by tests and the CLI."""
    app = FastAPI(
        title="TradingView Webhook Relay",
        version="1.0.0",
        description="Production-grade relay from TV alerts to crypto exchanges",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        """Receive a TradingView alert and route it through risk filters.

        Authentication: alerts must include a `secret` field matching the
        TV_WEBHOOK_SECRET env var. Compared with hmac.compare_digest to
        prevent timing attacks.
        """
        # Lazy state init in case lifespan didn't run (e.g., direct ASGI
        # invocation without context manager). Idempotent.
        if not hasattr(request.app.state, "config"):
            request.app.state.config = Config()
            request.app.state.backend = get_backend(request.app.state.config.exchange)

        config: Config = request.app.state.config
        backend = request.app.state.backend

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON body",
            )

        # Validate the alert shape
        try:
            alert = TVAlert.model_validate(payload)
        except Exception as e:
            logger.warning("Alert validation failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Alert validation failed: {e}",
            )

        # Authentication
        if not hmac.compare_digest(alert.secret, config.webhook_secret):
            logger.warning(
                "Webhook auth failed: strategy=%s symbol=%s",
                alert.strategy, alert.symbol,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid secret",
            )

        # Risk filters
        result = run_filters(alert, config)
        if not result.accepted:
            logger.info(
                "Alert rejected by %s: %s | strategy=%s symbol=%s",
                result.rejected_by, result.reason, alert.strategy, alert.symbol,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,  # 200 so TV doesn't retry
                content={
                    "status": "rejected",
                    "rejected_by": result.rejected_by,
                    "reason": result.reason,
                },
            )

        # Convert to order and submit
        order = alert_to_order(alert)
        try:
            response = await backend.submit(order)
        except NotImplementedError as e:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=str(e),
            )
        except Exception:
            logger.exception("Backend submit failed for cid=%s", order.client_order_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Exchange backend error",
            )

        logger.info(
            "Alert accepted: %s %s $%s strategy=%s cid=%s",
            order.side.value, order.symbol, order.size_usd,
            order.strategy, order.client_order_id,
        )
        return JSONResponse(content={
            "status": "accepted",
            "client_order_id": order.client_order_id,
            "backend_response": response,
        })

    return app


app = create_app()
