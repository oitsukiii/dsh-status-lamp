"""数据源抽象 —— 从状态桥读状态。

客户端只调 start()，后端自己在后台线程里把状态推给 on_state 回调。
回调必须线程安全（客户端用 queue.put）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional


# 断线重连的退避范围
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0
# SSE 读超时必须大于桥的心跳间隔（20 秒），否则误判断线
SSE_READ_TIMEOUT = 45.0


class BaseBackend:
    """所有后端的最小接口。"""

    def __init__(self, on_state: Callable[[dict], None]) -> None:
        self.on_state = on_state
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError




class SseBackend(BaseBackend):
    """局域网模式：连桥的 /events 长连接，断线指数退避重连。"""

    def __init__(self, on_state, url: str) -> None:
        super().__init__(on_state)
        self.url = url

    def _run(self) -> None:
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(self.url, headers={
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                })
                with urllib.request.urlopen(req, timeout=SSE_READ_TIMEOUT) as resp:
                    backoff = RECONNECT_MIN          # 连上了就重置退避
                    for raw in resp:
                        if self._stop.is_set():
                            return
                        line = raw.decode("utf-8", "replace").strip()
                        if not line or line.startswith(":"):
                            continue                   # 心跳，忽略
                        if not line.startswith("data:"):
                            continue
                        try:
                            self.on_state(json.loads(line[5:].strip()))
                        except ValueError:
                            continue
            except Exception:
                pass
            # 断线：退避后重连。不能无限快重试，否则桥重启时几十个客户端同时猛连。
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, RECONNECT_MAX)


def make_backend(cfg: dict, on_state) -> BaseBackend:
    """按配置造后端。mode = "local" | "lan"。"""
    if cfg.get("mode", "local") == "lan":
        return SseBackend(on_state, cfg.get("server", "http://127.0.0.1:3099/events"))
