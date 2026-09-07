"""Aggregate payments (one QR for WeChat Pay + Alipay, no dropped orders).

Lay of the land:
  models.py  — order/status/channel value types
  ledger.py  — durable (SQLite) order store; survives restarts
  gateway.py — aggregator adapter: issuing, callback verification, reconciliation

The three things that prevent 漏单 (dropped orders):
  1. Durability   — orders are committed to SQLite before we answer, so a
                    restart cannot erase a paid-but-unsettled order.
  2. Idempotency  — callbacks de-duplicate on the *gateway's* trade number, so
                    a retried webhook settles exactly once (never double-charge).
  3. Reconciliation — a periodic sweep asks the gateway what actually settled and
                    heals anything our inbox missed, including an order we
                    prematurely expired.
"""

from __future__ import annotations

from pathlib import Path

from ..config import settings
from .gateway import PaymentGateway, SandboxGateway
from .ledger import Ledger
from .models import (
    ORDER_TTL_S,
    Channel,
    Order,
    OrderStatus,
    PaymentError,
    iso,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "payments.db"


def build_ledger(db_path: str | Path | None = None, amount_cents: int | None = None) -> Ledger:
    """Factory so tests can create isolated ledgers."""
    return Ledger(
        db_path or settings.payment_db_path or DEFAULT_DB_PATH,
        amount_cents if amount_cents is not None else settings.payment_cents,
    )


def build_gateway(secret: str | None = None) -> PaymentGateway:
    """Factory so tests can inject a sandbox gateway."""
    return PaymentGateway(
        secret if secret is not None else settings.payment_callback_secret
    )


ledger = build_ledger()
gateway = build_gateway()

__all__ = [
    "ORDER_TTL_S",
    "Channel",
    "Ledger",
    "Order",
    "OrderStatus",
    "PaymentError",
    "PaymentGateway",
    "SandboxGateway",
    "build_gateway",
    "build_ledger",
    "gateway",
    "iso",
    "ledger",
]
