"""
Payment ledger + gateway tests, with emphasis on 防漏单 (no dropped orders).

The scenarios here are the ones that actually lose money in production:
  * the process restarts between "student paid" and "we settled"
  * the aggregator's webhook never reaches us
  * the same webhook is delivered twice
  * someone forges a "paid" callback
  * we expire an order that the gateway says was actually paid

Run:  PYTHONPATH=. python tests/test_payments.py      (or: pytest tests/)
"""

from __future__ import annotations

import asyncio
import sys
import time

from app.payments.gateway import SandboxGateway
from app.payments.ledger import Ledger
from app.payments.models import ORDER_TTL_S, Channel, OrderStatus, PaymentError
from app.payments import iso

SECRET = "test-callback-secret"

# Windows cannot delete an open SQLite file, so every ledger is tracked and
# closed once its test finishes.
_OPEN: list[Ledger] = []


def make_ledger(tmp_path, name: str = "pay.db", amount_cents: int = 100) -> Ledger:
    led = Ledger(str(tmp_path / name), amount_cents=amount_cents)
    _OPEN.append(led)
    return led


def _track(led: Ledger) -> Ledger:
    _OPEN.append(led)
    return led


def _close_all() -> None:
    while _OPEN:
        try:
            _OPEN.pop().close()
        except Exception:  # noqa: BLE001 - teardown must never mask a failure
            pass


try:  # pytest is optional; the file also runs standalone
    import pytest

    @pytest.fixture(autouse=True)
    def _close_ledgers():
        yield
        _close_all()
except ImportError:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------


def test_create_is_idempotent(tmp_path):
    led = make_ledger(tmp_path)
    a = led.create(student_id="s1", term_code="2026-2027-1", idempotency_key="k1")
    b = led.create(student_id="s1", term_code="2026-2027-1", idempotency_key="k1")
    assert a.id == b.id, "same idempotency key must return the same order"
    assert led.summary()["CREATED"] == 1


def test_duplicate_callback_settles_once(tmp_path):
    """A retried webhook must never double-charge."""
    led = make_ledger(tmp_path)
    order = led.create(student_id="s1", term_code="t", idempotency_key="k1")

    first = led.mark_paid(order.id, "TRADE-1", Channel.AGGREGATE)
    second = led.mark_paid(order.id, "TRADE-1", Channel.AGGREGATE)

    assert first.status == OrderStatus.PAID
    assert second.status == OrderStatus.PAID
    assert first.paid_at == second.paid_at, "paid_at must not move on a repeat callback"
    assert led.summary()["PAID"] == 1


def test_trade_number_cannot_settle_two_orders(tmp_path):
    led = make_ledger(tmp_path)
    o1 = led.create(student_id="s1", term_code="t", idempotency_key="k1")
    o2 = led.create(student_id="s2", term_code="t", idempotency_key="k2")
    led.mark_paid(o1.id, "TRADE-SAME", Channel.AGGREGATE)

    try:
        led.mark_paid(o2.id, "TRADE-SAME", Channel.AGGREGATE)
    except PaymentError as exc:
        assert "another order" in str(exc)
    else:
        raise AssertionError("one upstream trade must not settle two orders")


def test_forged_callback_rejected(tmp_path):
    gw = SandboxGateway(secret=SECRET)
    # tampered amount
    body = gw.signed_callback("o1", "T1", 100)
    body["amount_cents"] = "1"
    assert asyncio.run(gw.verify_callback(body)) is None

    # signed with a different secret
    forged = SandboxGateway(secret="wrong-secret").signed_callback("o1", "T1", 100)
    assert asyncio.run(gw.verify_callback(forged)) is None

    # genuine callback passes
    good = gw.signed_callback("o1", "T1", 100)
    assert asyncio.run(gw.verify_callback(good)) == "T1"


def test_missing_secret_fails_closed(tmp_path):
    """No signing key => every callback is rejected, not trusted."""
    gw = SandboxGateway(secret="")
    body = {"order_id": "o1", "trade_no": "T1", "amount_cents": "0", "sign": "x"}
    assert asyncio.run(gw.verify_callback(body)) is None


def test_order_survives_restart(tmp_path):
    """CORE 漏单 FIX: a paid order must not vanish when the process dies."""
    path = str(tmp_path / "pay.db")
    led = _track(Ledger(path, amount_cents=100))
    order = led.create(student_id="s1", term_code="2026-2027-1", idempotency_key="k1")
    led.mark_paid(order.id, "TRADE-1", Channel.AGGREGATE)
    led.close()  # simulate the process dying

    rebooted = _track(Ledger(path, amount_cents=100))
    reloaded = rebooted.get(order.id)
    assert reloaded is not None, "order survived restart"
    assert reloaded.status == OrderStatus.PAID
    assert reloaded.gateway_trade_no == "TRADE-1"
    assert iso(reloaded.paid_at)


def test_reconciliation_heals_lost_callback(tmp_path):
    """Money taken, webhook never delivered — reconciliation must settle it."""
    led = make_ledger(tmp_path)
    order = led.create(student_id="s1", term_code="t", idempotency_key="k1")

    gw = SandboxGateway(secret=SECRET)
    gw.settle(order.id, "TRADE-LOST", amount_cents=100)  # gateway says paid
    # ...but no callback is ever delivered to us.

    healed = asyncio.run(led.reconcile(gw, since_s=time.time() - 60))
    assert healed == 1
    assert led.get(order.id).status == OrderStatus.PAID


def test_reconciliation_heals_expired_but_paid(tmp_path):
    """We expired it, the gateway says it was paid — honour the money, not the clock."""
    led = make_ledger(tmp_path)
    order = led.create(student_id="s1", term_code="t", idempotency_key="k1")

    # Age the order past its TTL so the sweeper expires it.
    with led._lock:  # noqa: SLF001 - test needs to backdate the row
        led._conn.execute(
            "UPDATE orders SET created_at=? WHERE id=?",
            (time.time() - ORDER_TTL_S - 10, order.id),
        )
        led._conn.commit()
    assert led.sweep_expired() == 1
    assert led.get(order.id).status == OrderStatus.EXPIRED

    gw = SandboxGateway(secret=SECRET)
    gw.settle(order.id, "TRADE-LATE", amount_cents=100)

    healed = asyncio.run(led.reconcile(gw, since_s=time.time() - 60))
    assert healed == 1
    assert led.get(order.id).status == OrderStatus.PAID


def test_reconciliation_ignores_unknown_and_already_paid(tmp_path):
    led = make_ledger(tmp_path)
    order = led.create(student_id="s1", term_code="t", idempotency_key="k1")
    led.mark_paid(order.id, "TRADE-1", Channel.AGGREGATE)

    gw = SandboxGateway(secret=SECRET)
    gw.settle(order.id, "TRADE-1", amount_cents=100)  # already settled locally
    gw.settle("no-such-order", "TRADE-GHOST", amount_cents=100)  # unknown order

    healed = asyncio.run(led.reconcile(gw, since_s=time.time() - 60))
    assert healed == 0, "already-paid and unknown orders must not be re-settled"
    assert led.summary()["PAID"] == 1


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    # Keep SQLite files inside the project tree: the sandbox forbids (and
    # silently kills on) writes to the system temp dir.
    _tmp_root = Path(__file__).resolve().parent.parent / ".tmp"
    _tmp_root.mkdir(exist_ok=True)

    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory(dir=str(_tmp_root)) as td:
            try:
                fn(Path(td))
                print(f"  PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
            finally:
                _close_all()
    print(f"\n{len(tests) - failures}/{len(tests)} payment tests passed")
    sys.exit(1 if failures else 0)
