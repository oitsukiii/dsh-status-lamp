"""数据源抽象 —— 本机模式和局域网模式用同一个接口。

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

from .state import read_state, state_hash

# 本机模式：读文件很便宜，200ms 足够
FILE_INTERVAL = 0.2
# 局域网模式：断线重连的退避范围
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


def zstd_available() -> bool:
    """能不能解 zstd（决定能否读到细粒度状态）。

    三条路都算：Python 3.14 标准库 / pyzstd / zstd 命令行。
    不能解也能跑 —— 只是只能判断「忙/闲」。
    """
    from .event_tail import decompressor_available
    return decompressor_available()


class FileBackend(BaseBackend):
    """本机模式：直接读 $DSH_HOME 下的文件，不需要桥、不需要网络。"""

    def __init__(self, on_state, done_window_ms: "int | None" = None) -> None:
        super().__init__(on_state)
        # 和桥保持一致的默认值：绿灯保持 3 分钟后回退。
        # 以前默认 None（永不超时），结果本机模式下 done 之后灯永远是绿的，
        # 而且「完成」提示音也不会再响（它只在 done 的上升沿触发）。
        self.done_window_ms = 180_000 if done_window_ms is None else done_window_ms
        self._last: Optional[str] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                state = read_state(self.done_window_ms)
                h = state_hash(state)
                if h != self._last:
                    self._last = h
                    self.on_state(state)
            except Exception:
                pass
            self._stop.wait(FILE_INTERVAL)

    def force(self) -> None:
        """清掉指纹，让下一轮必定重新下发一次（供「立刻刷新」用）。"""
        self._last = None


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
    return FileBackend(on_state, cfg.get("done_seconds"))
