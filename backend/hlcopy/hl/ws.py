from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

import websockets

from hlcopy.engine.state import WsState
from hlcopy.hl.errors import mask_secrets
from hlcopy.hl.info import WS_URLS, Fill

log = logging.getLogger("hlcopy.hl.ws")

RECONNECT_DELAYS = (1.0, 2.0, 5.0)


def parse_wire_message(raw: str | bytes) -> list[Fill]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("channel") != "userFills":
        return []
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("isSnapshot"):
        return []
    fills: list[Fill] = []
    for entry in data.get("fills") or []:
        if not isinstance(entry, dict):
            continue
        coin = entry.get("coin")
        ts = entry.get("time")
        if not isinstance(coin, str) or ts is None:
            continue
        fills.append(
            Fill(
                coin=coin,
                ts=int(ts),
                start_position=_dec(entry.get("startPosition")),
                size=_dec(entry.get("sz")),
            )
        )
    return fills


def _dec(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


class LeaderWatcher(threading.Thread):
    def __init__(
        self,
        *,
        url: str,
        leader: str,
        on_fill: Callable[[str, Fill], bool],
        backfill: Callable[[str, int], list[Fill]],
        on_state: Callable[[], None],
    ) -> None:
        super().__init__(daemon=True, name=f"ws-{leader[:10]}")
        self.url = url
        self.leader = leader
        self.on_fill = on_fill
        self.backfill = backfill
        self.on_state = on_state
        self.connected = False
        self.last_seen_ms: int | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        ws = self._ws
        if loop is not None and ws is not None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(ws.close()))

    def handle_message(self, raw: str | bytes) -> None:
        for fill in parse_wire_message(raw):
            if self.last_seen_ms is None or fill.ts > self.last_seen_ms:
                self.last_seen_ms = fill.ts
            self.on_fill(self.leader, fill)

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("ws thread crashed for %s", self.leader)
        finally:
            self.connected = False
            self.on_state()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ping_interval=20, open_timeout=10, close_timeout=2
                ) as ws:
                    self._ws = ws
                    await ws.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "subscription": {"type": "userFills", "user": self.leader},
                            }
                        )
                    )
                    self.connected = True
                    self.on_state()
                    attempt = 0
                    self._backfill_gap()
                    async for raw in ws:
                        self.handle_message(raw)
                        if self._stop.is_set():
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("ws error for %s: %s", self.leader, mask_secrets(str(exc)))
            finally:
                self._ws = None
                self.connected = False
                self.on_state()

            if self._stop.is_set():
                break
            await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            attempt += 1

    def _backfill_gap(self) -> None:
        if self.last_seen_ms is None:
            return
        try:
            fills = self.backfill(self.leader, self.last_seen_ms + 1)
        except Exception as exc:
            log.warning("ws backfill failed for %s: %s", self.leader, mask_secrets(str(exc)))
            return
        for fill in fills:
            if fill.ts > self.last_seen_ms:
                self.last_seen_ms = fill.ts
            self.on_fill(self.leader, fill)


class WsManager:
    def __init__(
        self,
        network: str,
        *,
        on_fill: Callable[[str, Fill], bool],
        backfill: Callable[[str, int], list[Fill]],
        watcher_factory: Callable[..., LeaderWatcher] | None = None,
    ) -> None:
        if network not in WS_URLS:
            raise ValueError(f"unknown network {network!r}")
        self.url = WS_URLS[network]
        self.on_fill = on_fill
        self.backfill = backfill
        self.state = WsState()
        self._factory = watcher_factory or LeaderWatcher
        self._watchers: dict[str, LeaderWatcher] = {}
        self._lock = threading.Lock()

    def sync(self, leaders: set[str]) -> None:
        desired = {leader.lower() for leader in leaders if leader}
        with self._lock:
            for leader in list(self._watchers):
                if leader not in desired:
                    self._watchers.pop(leader).stop()
            for leader in desired - set(self._watchers):
                watcher = self._factory(
                    url=self.url,
                    leader=leader,
                    on_fill=self.on_fill,
                    backfill=self.backfill,
                    on_state=self._refresh_state,
                )
                self._watchers[leader] = watcher
                watcher.start()
            self.state.subscriptions = len(self._watchers)
        self._refresh_state()

    def _refresh_state(self) -> None:
        self.state.connected = any(watcher.connected for watcher in self._watchers.values())

    def stop(self) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            self._watchers.clear()
        for watcher in watchers:
            watcher.stop()
        self.state.connected = False
        self.state.subscriptions = 0
