from __future__ import annotations

from decimal import Decimal

import pytest
from eth_account import Account

from hlcopy import crypto, db
from hlcopy.config import Settings
from hlcopy.engine.loop import Engine
from hlcopy.hl.errors import HlError
from hlcopy.hl.exchange import ExchangeResponse
from hlcopy.hl.info import AccountSnapshot, CoinMeta, Meta, Position

from conftest import make_settings


def position(coin: str, size: str, leverage: int = 10) -> Position:
    return Position(
        coin=coin,
        size=Decimal(size),
        entry_px=Decimal("1000"),
        position_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        leverage=leverage,
        is_cross=True,
        liquidation_px=None,
    )


def snapshot(address: str, positions: list[Position]) -> AccountSnapshot:
    return AccountSnapshot(
        address=address,
        account_value=Decimal("1000"),
        withdrawable=Decimal("1000"),
        margin_used=Decimal("0"),
        positions=tuple(positions),
        open_orders=(),
        fetched_at_ms=1_700_000_000_000,
    )


class FakeInfo:
    def __init__(self, leader: str, leader_positions, follower_positions, fail_accounts=False):
        self.leader = leader
        self.leader_positions = leader_positions
        self.follower_positions = follower_positions
        self.fail_accounts = fail_accounts
        self.fail_for: set[str] = set()

    def meta(self) -> Meta:
        return Meta(
            coins=(
                CoinMeta(name="BTC", sz_decimals=3, max_leverage=20, asset_index=0),
                CoinMeta(name="ETH", sz_decimals=4, max_leverage=25, asset_index=1),
            ),
            fetched_at_ms=1,
        )

    def all_mids(self) -> dict[str, Decimal]:
        return {"BTC": Decimal("1000"), "ETH": Decimal("3000")}

    def account(self, address: str) -> AccountSnapshot:
        if self.fail_accounts or address.lower() in self.fail_for:
            raise HlError("leader state unavailable")
        if address.lower() == self.leader.lower():
            return snapshot(address, self.leader_positions)
        return snapshot(address, self.follower_positions)


class FakeExchange:
    def __init__(self, order_response: ExchangeResponse | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.order_response = order_response or ExchangeResponse(ok=True, raw={}, oid=7)

    def update_leverage(self, **kwargs) -> ExchangeResponse:
        self.calls.append(("leverage", kwargs))
        return ExchangeResponse(ok=True, raw={})

    def place_ioc(self, **kwargs) -> ExchangeResponse:
        self.calls.append(("order", kwargs))
        return self.order_response


@pytest.fixture
def settings(tmp_path) -> Settings:
    built = make_settings(tmp_path)
    db.init_db(built.db_path)
    return built


def add_wallet(settings: Settings, *, key_state: str = "ok") -> db.WalletRecord:
    account = Account.create()
    blob = crypto.encrypt_secret(settings.secret_enc_key, account.key.hex())
    with db.session(settings.db_path) as conn:
        wallet_id = db.insert_wallet(
            conn,
            name=f"w-{account.address[-6:]}",
            address=Account.create().address,
            api_wallet_address=account.address,
            secret_enc=blob,
        )
        if key_state != "ok":
            db.set_key_state(conn, wallet_id, key_state)
        wallet = db.get_wallet(conn, wallet_id)
    assert wallet is not None
    return wallet


def add_task(
    settings: Settings,
    wallet: db.WalletRecord,
    leader: str,
    *,
    ratio: str = "0.1",
    max_notional: str = "10000",
    slippage: str = "0.01",
    status: str = "running",
) -> db.TaskRecord:
    with db.session(settings.db_path) as conn:
        task_id = db.insert_task(
            conn,
            wallet_id=wallet.id,
            leader=leader,
            mode="sync",
            ratio=ratio,
            max_notional_usd=max_notional,
            max_leverage="10",
            max_slippage=slippage,
            close_on_stop=False,
        )
        if status != "running":
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
            conn.commit()
        task = db.get_task(conn, task_id)
    assert task is not None
    return task


def make_engine(settings: Settings, info, exchange: FakeExchange) -> Engine:
    return Engine(settings, info, exchange_factory=lambda wallet: exchange)


def logs(settings: Settings) -> list[dict]:
    with db.session(settings.db_path) as conn:
        rows, _ = db.list_exec_logs(conn, limit=100)
    return [dict(row) for row in rows]


def test_first_entry_sets_leverage_then_places_order(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    task = add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="start")

    assert [call[0] for call in exchange.calls] == ["leverage", "order"]
    _, leverage = exchange.calls[0]
    assert leverage == {"asset": 0, "is_cross": True, "leverage": 10}

    _, order = exchange.calls[1]
    assert order["asset"] == 0
    assert order["is_buy"] is True
    assert order["size"] == Decimal("0.1")
    assert order["price"] == Decimal("1010")
    assert order["reduce_only"] is False
    assert order["cloid"].startswith("0x")
    assert len(order["cloid"]) == 34

    rows = logs(settings)
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == task.id
    assert row["source"] == "start"
    assert row["reason"] == "set_leverage"
    assert row["result"] == "filled"
    assert row["side"] == "buy"
    assert row["qty"] == "0.1"
    assert row["price"] == "1010"
    assert row["oid"] == "7"
    assert row["cloid"] == order["cloid"]

    with db.session(settings.db_path) as conn:
        state = db.get_coin_state(conn, task.id, "BTC")
    assert state is not None
    assert state.leverage_set is True


def test_second_round_skips_leverage(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()
    engine = make_engine(settings, info, exchange)

    engine.reconcile_all(source="start")
    exchange.calls.clear()
    engine.reconcile_all(source="reconcile")

    assert [call[0] for call in exchange.calls] == ["order"]
    assert logs(settings)[0]["reason"] == "place"


def test_cloid_is_deterministic(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()
    engine = make_engine(settings, info, exchange)

    engine.reconcile_all(source="start")
    first = exchange.calls[-1][1]["cloid"]
    engine.reconcile_all(source="reconcile")
    second = exchange.calls[-1][1]["cloid"]

    assert first == second


def test_failed_order_is_logged_with_category(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange(
        ExchangeResponse(
            ok=False,
            raw={},
            error="Order minimum value is $10",
            category="below_min_notional",
        )
    )

    make_engine(settings, info, exchange).reconcile_all(source="start")

    row = logs(settings)[0]
    assert row["result"] == "failed"
    assert row["reason"] == "set_leverage"
    assert "minimum value" in row["raw"]
    with db.session(settings.db_path) as conn:
        state = db.get_coin_state(conn, row["task_id"], "BTC")
    assert state is not None
    assert state.leverage_set is True


def test_leader_read_failure_logs_skip_and_places_nothing(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    task = add_task(settings, wallet, leader)
    info = FakeInfo(leader, [], [], fail_accounts=True)
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="reconcile")

    assert exchange.calls == []
    rows = logs(settings)
    assert len(rows) == 1
    assert rows[0]["task_id"] == task.id
    assert rows[0]["coin"] == "*"
    assert rows[0]["result"] == "skipped"
    assert rows[0]["reason"] == "leader_state_unavailable"


def test_unreadable_key_marks_wallet_and_stops(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])

    def broken_factory(record):
        raise crypto.SecretUnreadable("cannot decrypt")

    Engine(settings, info, exchange_factory=broken_factory).reconcile_all(source="start")

    with db.session(settings.db_path) as conn:
        assert db.get_wallet(conn, wallet.id).key_state == "unreadable"
    assert logs(settings) == []


def test_wallet_already_unreadable_is_skipped(settings):
    wallet = add_wallet(settings, key_state="unreadable")
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="start")

    assert exchange.calls == []
    assert logs(settings) == []


def test_reverse_places_two_orders(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader, ratio="1", max_notional="100000")
    info = FakeInfo(leader, [position("BTC", "-2")], [position("BTC", "1")])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="start")

    orders = [call[1] for call in exchange.calls if call[0] == "order"]
    assert len(orders) == 2
    assert orders[0]["is_buy"] is False
    assert orders[0]["size"] == Decimal("1")
    assert orders[0]["reduce_only"] is True
    assert orders[1]["is_buy"] is False
    assert orders[1]["size"] == Decimal("2")
    assert orders[1]["reduce_only"] is False
    assert orders[0]["cloid"] != orders[1]["cloid"]
    assert [row["reason"] for row in logs(settings)] == ["reverse", "reverse"]


def test_paused_task_is_not_touched(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader, status="paused")
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="start")

    assert exchange.calls == []
    assert logs(settings) == []


def test_close_all_is_reduce_only(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader, ratio="1")
    info = FakeInfo(leader, [], [position("BTC", "1")])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="start")

    _, order = exchange.calls[0]
    assert order["is_buy"] is False
    assert order["size"] == Decimal("1")
    assert order["reduce_only"] is True
    assert logs(settings)[0]["reason"] == "close_all"


class Clock:
    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self.value = start

    def __call__(self) -> int:
        return self.value


def test_backoff_schedule():
    from hlcopy.engine.executor import backoff_ms

    assert backoff_ms(0) == 0
    assert [backoff_ms(count) for count in range(1, 8)] == [
        5_000,
        10_000,
        20_000,
        40_000,
        60_000,
        60_000,
        60_000,
    ]


def failing_engine(settings, info, exchange, clock):
    return Engine(settings, info, exchange_factory=lambda wallet: exchange, clock_ms=clock)


def test_failure_enters_backoff_without_repeat_logs(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    task = add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange(ExchangeResponse(ok=False, raw={}, error="Insufficient margin", category="insufficient_margin"))
    clock = Clock()
    engine = failing_engine(settings, info, exchange, clock)

    engine.reconcile_all(source="reconcile")
    assert len(logs(settings)) == 1
    with db.session(settings.db_path) as conn:
        state = db.get_coin_state(conn, task.id, "BTC")
    assert state is not None
    assert state.fail_count == 1
    assert state.next_retry_at == clock.value + 5_000

    calls_after_first = len(exchange.calls)
    engine.reconcile_all(source="reconcile")

    assert len(exchange.calls) == calls_after_first
    assert len(logs(settings)) == 1


def test_retry_after_backoff_window(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    task = add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange(ExchangeResponse(ok=False, raw={}, error="Insufficient margin", category="insufficient_margin"))
    clock = Clock()
    engine = failing_engine(settings, info, exchange, clock)

    engine.reconcile_all(source="reconcile")
    clock.value += 5_001
    engine.reconcile_all(source="reconcile")

    with db.session(settings.db_path) as conn:
        state = db.get_coin_state(conn, task.id, "BTC")
    assert state is not None
    assert state.fail_count == 2
    assert state.next_retry_at == clock.value + 10_000
    assert len(logs(settings)) == 2


def test_success_clears_failure_state(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    task = add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange(ExchangeResponse(ok=False, raw={}, error="Insufficient margin", category="insufficient_margin"))
    clock = Clock()
    engine = failing_engine(settings, info, exchange, clock)

    engine.reconcile_all(source="reconcile")
    clock.value += 5_001
    exchange.order_response = ExchangeResponse(ok=True, raw={}, oid=9)
    engine.reconcile_all(source="reconcile")

    with db.session(settings.db_path) as conn:
        state = db.get_coin_state(conn, task.id, "BTC")
    assert state is not None
    assert state.fail_count == 0
    assert state.next_retry_at is None
    assert state.last_error is None


def test_one_task_failure_does_not_block_another(settings):
    wallet_a = add_wallet(settings)
    wallet_b = add_wallet(settings)
    leader_a = Account.create().address
    leader_b = Account.create().address
    add_task(settings, wallet_a, leader_a)
    task_b = add_task(settings, wallet_b, leader_b)
    info = FakeInfo(leader_b, [position("BTC", "1")], [])
    info.fail_for = {leader_a.lower()}
    exchange = FakeExchange()

    make_engine(settings, info, exchange).reconcile_all(source="reconcile")

    sources = {row["task_id"] for row in logs(settings)}
    assert task_b.id in sources
    assert any(row["coin"] == "*" for row in logs(settings))
    orders = [call for call in exchange.calls if call[0] == "order"]
    assert len(orders) == 1


def test_tick_uses_reconcile_source(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader)
    info = FakeInfo(leader, [position("BTC", "1")], [])
    exchange = FakeExchange()

    make_engine(settings, info, exchange).tick()

    assert logs(settings)[0]["source"] == "reconcile"
    assert logs(settings)[0]["result"] == "filled"


def test_add_reduce_and_close_converge(settings):
    wallet = add_wallet(settings)
    leader = Account.create().address
    add_task(settings, wallet, leader, ratio="1", max_notional="1000000")
    info = FakeInfo(leader, [position("BTC", "3")], [position("BTC", "1")])
    exchange = FakeExchange()
    engine = make_engine(settings, info, exchange)

    engine.reconcile_all(source="reconcile")
    _, add = [call for call in exchange.calls if call[0] == "order"][0]
    assert add["is_buy"] is True
    assert add["size"] == Decimal("2")
    assert add["reduce_only"] is False

    info.leader_positions = [position("BTC", "0.5")]
    info.follower_positions = [position("BTC", "3")]
    engine.reconcile_all(source="reconcile")
    _, reduce = exchange.calls[-1]
    assert reduce["is_buy"] is False
    assert reduce["size"] == Decimal("2.5")
    assert reduce["reduce_only"] is True

    info.leader_positions = []
    info.follower_positions = [position("BTC", "0.5")]
    engine.reconcile_all(source="reconcile")
    _, close = exchange.calls[-1]
    assert close["size"] == Decimal("0.5")
    assert close["reduce_only"] is True
