from __future__ import annotations

import json

from hlcopy.hl.info import Fill
from hlcopy.hl.ws import LeaderWatcher, WsManager, parse_wire_message


def fill_message(fills: list[dict], snapshot: bool = False) -> str:
    return json.dumps(
        {
            "channel": "userFills",
            "data": {"user": "0xabc", "isSnapshot": snapshot, "fills": fills},
        }
    )


def test_parse_wire_message_returns_fills():
    raw = fill_message(
        [
            {"coin": "BTC", "time": 100, "sz": "0.1"},
            {"coin": "ETH", "time": 101, "sz": "1"},
        ]
    )

    parsed = parse_wire_message(raw)
    assert [(fill.coin, fill.ts) for fill in parsed] == [("BTC", 100), ("ETH", 101)]
    assert parse_wire_message(raw.encode()) == parsed


def test_parse_wire_message_skips_snapshots():
    raw = fill_message([{"coin": "BTC", "time": 100}], snapshot=True)
    assert parse_wire_message(raw) == []


def test_parse_wire_message_ignores_other_channels_and_garbage():
    assert parse_wire_message(json.dumps({"channel": "subscriptionResponse"})) == []
    assert parse_wire_message("{not json") == []
    assert parse_wire_message(json.dumps({"channel": "userFills", "data": None})) == []


def test_parse_wire_message_skips_malformed_fills():
    raw = fill_message([{"coin": "BTC"}, {"time": 5}, "nope", {"coin": "SOL", "time": 6}])
    assert [fill.coin for fill in parse_wire_message(raw)] == ["SOL"]


class FakeWatcher:
    def __init__(self, **kwargs) -> None:
        self.leader = kwargs["leader"]
        self.url = kwargs["url"]
        self.connected = False
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True
        self.connected = True

    def stop(self) -> None:
        self.stopped = True
        self.connected = False


def test_ws_manager_syncs_subscriptions():
    created: list[FakeWatcher] = []

    def factory(**kwargs) -> FakeWatcher:
        watcher = FakeWatcher(**kwargs)
        created.append(watcher)
        return watcher

    manager = WsManager(
        "mainnet",
        on_fill=lambda leader, fill: True,
        backfill=lambda leader, since: [],
        watcher_factory=factory,
    )

    manager.sync({"0xAAA", "0xBBB"})
    assert {watcher.leader for watcher in created} == {"0xaaa", "0xbbb"}
    assert all(watcher.started for watcher in created)
    assert manager.state.subscriptions == 2
    assert manager.state.connected is True

    watcher_a = next(watcher for watcher in created if watcher.leader == "0xaaa")
    watcher_b = next(watcher for watcher in created if watcher.leader == "0xbbb")

    manager.sync({"0xAAA"})
    assert watcher_b.stopped is True
    assert manager.state.subscriptions == 1

    manager.sync(set())
    assert watcher_a.stopped is True
    assert manager.state.subscriptions == 0
    assert manager.state.connected is False


def test_watcher_handles_messages_and_backfills():
    events: list[tuple[str, str, int]] = []

    watcher = LeaderWatcher(
        url="ws://localhost",
        leader="0xabc",
        on_fill=lambda leader, fill: events.append((leader, fill.coin, fill.ts)) or True,
        backfill=lambda leader, since: [Fill(coin="BTC", ts=200), Fill(coin="ETH", ts=300)],
        on_state=lambda: None,
    )

    watcher.handle_message(fill_message([{"coin": "BTC", "time": 100}]))
    assert events == [("0xabc", "BTC", 100)]
    assert watcher.last_seen_ms == 100

    events.clear()
    watcher._backfill_gap()
    assert events == [("0xabc", "BTC", 200), ("0xabc", "ETH", 300)]
    assert watcher.last_seen_ms == 300
