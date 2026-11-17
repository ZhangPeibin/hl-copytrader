from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineState:
    running: bool = False
    last_tick_ms: int | None = None
    queue_depth: int = 0


@dataclass
class WsState:
    connected: bool = False
    subscriptions: int = 0
