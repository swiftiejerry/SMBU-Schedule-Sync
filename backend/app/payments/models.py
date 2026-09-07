"""Payment data model.

Deliberately tiny: one order row, one status machine, one channel enum.
Everything that can lose money (order state) lives in SQLite — see ledger.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"


class Channel(str, Enum):
    WECHAT = "WECHAT"
    ALIPAY = "ALIPAY"
    AGGREGATE = "AGGREGATE"  # the single code both WeChat and Alipay scan


class PaymentError(Exception):
    """Raised for state-machine violations (never silently swallowed)."""


ORDER_TTL_S = 15 * 60  # unpaid orders become EXPIRED after 15 min


@dataclass
class Order:
    id: str
    idempotency_key: str
    student_id: str
    term_code: str
    amount_cents: int
    status: OrderStatus = OrderStatus.CREATED
    channel: Optional[Channel] = None
    gateway_trade_no: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    paid_at: Optional[float] = None

    @property
    def expires_at(self) -> float:
        return self.created_at + ORDER_TTL_S


def iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
