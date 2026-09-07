"""Durable order ledger.

Why SQLite and not a dict: the product brief demands **no dropped orders (漏单)**.
A paid-but-not-yet-settled order that only exists in RAM dies with the process —
that is the single easiest way to lose a student's money. Every state transition
below is therefore committed to disk before the call returns.

Durability knobs:
  * WAL  — readers never block the (single) writer.
  * synchronous=FULL — commit() really fsyncs, so a power cut cannot roll back
    a settle that we already told the gateway succeeded.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import ORDER_TTL_S, Channel, Order, OrderStatus, PaymentError

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    idempotency_key   TEXT UNIQUE NOT NULL,
    student_id        TEXT NOT NULL,
    term_code         TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL,
    status            TEXT NOT NULL,
    channel           TEXT,
    gateway_trade_no  TEXT,
    created_at        REAL NOT NULL,
    paid_at           REAL
);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_student ON orders(student_id);

-- Global de-duplication by the *gateway's* trade number. Keying only on our own
-- order id would let one upstream trade settle two orders if the aggregator
-- ever reused/replayed it.
CREATE TABLE IF NOT EXISTS processed_trades (
    trade_no    TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    applied_at  REAL NOT NULL
);
"""

_COLS = (
    "id, idempotency_key, student_id, term_code, amount_cents, status, "
    "channel, gateway_trade_no, created_at, paid_at"
)


def _row_to_order(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"],
        idempotency_key=row["idempotency_key"],
        student_id=row["student_id"],
        term_code=row["term_code"],
        amount_cents=row["amount_cents"],
        status=OrderStatus(row["status"]),
        channel=Channel(row["channel"]) if row["channel"] else None,
        gateway_trade_no=row["gateway_trade_no"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
    )


class Ledger:
    """SQLite-backed, idempotent order store."""

    def __init__(self, db_path: str | Path, amount_cents: int = 0) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.amount_cents = amount_cents
        # Reentrant: mark_paid() reads through helpers while already holding it.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ read
    def _fetch(self, order_id: str) -> Optional[Order]:
        """Unlocked row read; callers must already hold `self._lock`."""
        row = self._conn.execute(
            f"SELECT {_COLS} FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        return _row_to_order(row) if row else None

    def get(self, order_id: str) -> Optional[Order]:
        with self._lock:
            return self._fetch(order_id)

    def by_student(self, student_id: str) -> List[Order]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM orders WHERE student_id=? ORDER BY created_at DESC",
                (student_id,),
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def summary(self) -> Dict[str, int]:
        out: Dict[str, int] = {s.value: 0 for s in OrderStatus}
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"
            ).fetchall()
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    def unsettled(self) -> List[Order]:
        """Orders a reconciliation pass should re-check with the gateway."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM orders WHERE status IN (?,?)",
                (OrderStatus.CREATED.value, OrderStatus.EXPIRED.value),
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    # ----------------------------------------------------------------- write
    def create(
        self, *, student_id: str, term_code: str, idempotency_key: str
    ) -> Order:
        """Idempotent create: the same key always yields the same order."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLS} FROM orders WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row:
                return _row_to_order(row)

            order = Order(
                id=uuid.uuid4().hex,
                idempotency_key=idempotency_key,
                student_id=student_id,
                term_code=term_code,
                amount_cents=self.amount_cents,
            )
            try:
                self._conn.execute(
                    "INSERT INTO orders "
                    "(id,idempotency_key,student_id,term_code,amount_cents,status,"
                    " channel,gateway_trade_no,created_at,paid_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        order.id,
                        order.idempotency_key,
                        order.student_id,
                        order.term_code,
                        order.amount_cents,
                        order.status.value,
                        None,
                        None,
                        order.created_at,
                        None,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Lost a race on the same idempotency key — adopt the winner.
                row = self._conn.execute(
                    f"SELECT {_COLS} FROM orders WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row:
                    return _row_to_order(row)
                raise
        return order

    def mark_paid(
        self,
        order_id: str,
        gateway_trade_no: str,
        channel: Channel,
        *,
        allow_expired: bool = False,
    ) -> Order:
        """Idempotent settle.

        Repeat callbacks for the same trade number are safe no-ops, so a
        retried/duplicated 漏单 acknowledgement can never double-charge.

        `allow_expired` exists for reconciliation only: if the gateway swears a
        trade settled, an order we prematurely expired must still be honoured —
        refusing it would be a dropped order.
        """
        with self._lock:
            seen = self._conn.execute(
                "SELECT order_id FROM processed_trades WHERE trade_no=?",
                (gateway_trade_no,),
            ).fetchone()
            if seen:
                if seen["order_id"] != order_id:
                    raise PaymentError("trade already applied to another order")
                order = self._fetch(order_id)  # already holding the lock
                if order is not None and order.status == OrderStatus.PAID:
                    return order  # duplicate callback — safe no-op
                raise PaymentError("trade already processed for a non-paid order")

            row = self._conn.execute(
                f"SELECT {_COLS} FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if not row:
                raise PaymentError("unknown order")
            order = _row_to_order(row)

            if order.gateway_trade_no and order.gateway_trade_no != gateway_trade_no:
                raise PaymentError("trade number mismatch — possible forgery")
            if order.status == OrderStatus.PAID:
                return order
            if order.status == OrderStatus.REFUNDED:
                raise PaymentError("cannot settle a refunded order")
            if order.status == OrderStatus.EXPIRED and not allow_expired:
                raise PaymentError("cannot settle an expired order")
            if order.status not in (OrderStatus.CREATED, OrderStatus.EXPIRED):
                raise PaymentError(f"cannot settle order in {order.status.value}")

            now = time.time()
            self._conn.execute(
                "UPDATE orders SET status=?, channel=?, gateway_trade_no=?, paid_at=? "
                "WHERE id=?",
                (OrderStatus.PAID.value, channel.value, gateway_trade_no, now, order_id),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_trades (trade_no, order_id, applied_at) "
                "VALUES (?,?,?)",
                (gateway_trade_no, order_id, now),
            )
            self._conn.commit()

        order.status = OrderStatus.PAID
        order.channel = channel
        order.gateway_trade_no = gateway_trade_no
        order.paid_at = now
        return order

    def sweep_expired(self) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE orders SET status=? WHERE status=? AND ? > created_at + ?",
                (
                    OrderStatus.EXPIRED.value,
                    OrderStatus.CREATED.value,
                    now,
                    ORDER_TTL_S,
                ),
            )
            self._conn.commit()
            return cur.rowcount

    # --------------------------------------------------------- reconciliation
    async def reconcile(self, gateway: Any, since_s: Optional[float] = None) -> int:
        """Heal orders whose callback never arrived.

        The gateway is the source of truth for money, not our inbox. If it says a
        trade settled, we settle it locally — this is the backstop that turns a
        lost webhook from a dropped order into a late-but-correct settlement.
        """
        trades: Iterable[Dict[str, Any]] = await gateway.list_settled_trades(
            since_s if since_s is not None else time.time() - 3_600
        )
        healed = 0
        for t in trades:
            order_id = t.get("order_id")
            trade_no = t.get("trade_no")
            if not order_id or not trade_no:
                continue
            existing = self.get(order_id)
            if existing is not None and existing.status == OrderStatus.PAID:
                continue  # already correct — not a recovery, don't double-count
            try:
                channel = Channel(t.get("channel", Channel.AGGREGATE.value))
            except ValueError:
                channel = Channel.AGGREGATE
            try:
                self.mark_paid(order_id, trade_no, channel, allow_expired=True)
                healed += 1
            except PaymentError:
                # Unknown / refunded / mismatched — nothing to heal.
                continue
        return healed

    def close(self) -> None:
        with self._lock:
            self._conn.close()
