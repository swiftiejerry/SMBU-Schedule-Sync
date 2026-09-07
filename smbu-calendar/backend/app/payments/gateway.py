"""Payment gateway adapter boundary.

Two product requirements live here:

1. **Aggregate code (聚合支付)** — ONE QR that both WeChat Pay and Alipay can
   scan. We never branch on the app; the aggregator resolves the channel and
   reports it back on the callback.

2. **Trust nothing unsigned** — a callback is only honoured if it carries a
   valid HMAC over `order_id|trade_no|amount_cents`. If payments are enabled but
   no secret is configured the gateway FAILS CLOSED and rejects every callback,
   because accepting an unsigned "this student paid" is worse than a dropped
   order.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

from .models import Order

logger = logging.getLogger("smbu.pay")


class PaymentGateway:
    """Adapter interface. Swap in a real aggregator by subclassing."""

    def __init__(self, secret: str = "") -> None:
        self.secret = secret

    # ------------------------------------------------------------ signing
    def _canonical(self, order_id: str, trade_no: str, amount_cents: int) -> bytes:
        return f"{order_id}|{trade_no}|{amount_cents}".encode("utf-8")

    def sign(self, order_id: str, trade_no: str, amount_cents: int) -> str:
        return hmac.new(
            self.secret.encode("utf-8"),
            self._canonical(order_id, trade_no, amount_cents),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------- issuing
    async def create_aggregate_code(self, order: Order) -> Dict[str, str]:
        """Return the single code payload the client renders as a QR."""
        return {
            "code_type": "AGGREGATE",  # one code, WeChat + Alipay
            "url": f"https://pay.smbu.example/order/{order.id}",
        }

    # ------------------------------------------------------------ callback
    async def verify_callback(self, payload: Dict[str, Any]) -> Optional[str]:
        """Return the gateway trade no. if the callback is authentic, else None."""
        if not self.secret:
            # Fail closed: without a key there is no way to tell a real
            # settlement from a forged one.
            logger.error(
                "payment callback rejected: SMBU_PAY_CALLBACK_SECRET is not configured"
            )
            return None

        order_id = str(payload.get("order_id", ""))
        trade_no = str(payload.get("trade_no", ""))
        amount_raw = payload.get("amount_cents", "")
        signature = str(payload.get("sign", ""))
        if not (order_id and trade_no and signature):
            return None
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            return None

        expected = self.sign(order_id, trade_no, amount)
        if not hmac.compare_digest(expected, signature):
            logger.warning("payment callback signature mismatch for order %s", order_id)
            return None
        return trade_no

    # -------------------------------------------------------- reconciliation
    async def list_settled_trades(self, since_s: float) -> List[Dict[str, Any]]:
        """Trades the gateway says are settled since `since_s` (epoch seconds).

        This is the anti-漏单 backstop: the gateway's records are authoritative,
        so anything we missed (lost webhook, restart, network blip) shows up here.
        """
        return []


class SandboxGateway(PaymentGateway):
    """In-memory stand-in used by tests and local demos.

    `settle()` records a trade as settled at the gateway. Deliberately separate
    from delivering a callback, so tests can simulate the exact failure mode we
    care about — money taken, webhook never delivered.
    """

    def __init__(self, secret: str = "") -> None:
        super().__init__(secret)
        self._settled: Dict[str, Dict[str, Any]] = {}

    async def create_aggregate_code(self, order: Order) -> Dict[str, str]:
        return {
            "code_type": "AGGREGATE",
            "url": f"https://pay.sandbox.example/order/{order.id}",
            "sandbox": "true",
        }

    def settle(self, order_id: str, trade_no: str, amount_cents: int = 0) -> None:
        """Mark a trade settled at the gateway (does NOT deliver a callback)."""
        self._settled[trade_no] = {
            "order_id": order_id,
            "trade_no": trade_no,
            "amount_cents": amount_cents,
            "channel": "AGGREGATE",
            "settled_at": _now(),
        }

    def signed_callback(
        self, order_id: str, trade_no: str, amount_cents: int = 0
    ) -> Dict[str, str]:
        """Build an authentically-signed callback body (for tests)."""
        return {
            "order_id": order_id,
            "trade_no": trade_no,
            "amount_cents": str(amount_cents),
            "sign": self.sign(order_id, trade_no, amount_cents),
        }

    async def list_settled_trades(self, since_s: float) -> List[Dict[str, Any]]:
        return [t for t in self._settled.values() if t["settled_at"] >= since_s - 1e-6]


def _now() -> float:
    import time

    return time.time()


__all__ = ["PaymentGateway", "SandboxGateway", "base64"]
