#!/usr/bin/env python3
"""DSH 状态桥 —— 读 DSH 落盘的会话状态，用 HTTP 暴露给桌面客户端。

设计原则
--------
* **只读**：不写 DSH 的任何文件，不调用 DSH 的任何接口。
* **零依赖**：只用标准库，任何有 Python 3.9+ 的机器都能跑。
* **不打扰**：不经过 agent，因此不消耗 token、不污染上下文。

数据来源
--------
`<DSH_HOME>/storages/session_projcache/sessions/session-*.json` 里
`record.rows.turnBoundary.val` 记录着「有没有一个 turn 还开着」——
这正是「正在干活」与「空闲」的权威判据，而且**包含模型推理期间**，
这是让 agent 自己上报时永远拿不到的状态。

用法
----
    python3 bridge/state_bridge.py              # 默认 0.0.0.0:3099
    python3 bridge/state_bridge.py --port 3099 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dsh_lamp.state import read_state, state_hash  # noqa: E402

# ── SSE 推送：每个客户端一条长连接，服务器维护连接列表 ──
_clients: list = []
_clients_lock = threading.Lock()

HEARTBEAT_SECONDS = 20      # 心跳间隔（防止中间设备清理空闲连接）
WATCH_INTERVAL = 0.2        # 服务器内部检查状态的间隔（本地读文件，很便宜）

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
PROJCACHE_DIR = os.path.join(DSH_HOME, "storages", "session_projcache", "sessions")
SESSIONS_DIR = os.path.join(DSH_HOME, "sessions")

# 「完成」状态亮多久。

# DSH 没有「用户已读」信号（没有 markRead / visibility 上报），所以只能用近似：
#   · 用户发下一条消息（user/message）⇒ turn/start ⇒ 状态自然变成 thinking ✓
#   · 用户看了但不回复 ⇒ 只能靠超时兜底
# 默认 3 分钟：够你看清结果，又不至于一直亮着。
# 传 --done-seconds 0 表示永不超时（绿灯只被下一次 turn 打断）。
# 不管设多少，下一次 turn 开始时绿灯总会立刻被黄色打断。
DONE_WINDOW_MS: Optional[int] = 180_000

# 状态文件这么久没更新，就认为 DSH 没在跑。
STALE_SECONDS = 20.0


def _state_hash(state: dict) -> str:
    """状态指纹 —— 直接用共享实现，别再自己抄一份。

    之前这里引用了重构时删掉的 VOLATILE 常量，每次调用都抛 NameError，
    被 _watch_loop 的 except 吞掉 ⇒ 桥连上后只发一条快照，之后永不推送。
    """
    return state_hash(state)


def broadcast(payload: bytes) -> None:
    """往所有连着的客户端写一份；写失败的就从列表里剔除。"""
    with _clients_lock:
        dead = []
        for handler in _clients:
            try:
                handler.wfile.write(payload)
                handler.wfile.flush()
            except Exception:
                dead.append(handler)
        for handler in dead:
            try:
                _clients.remove(handler)
            except ValueError:
                pass


def _watch_loop() -> None:
    """状态一变就推。这是服务器内部的检查，和客户端轮询无关。"""
    last = None
    err_shown = False
    while True:
        try:
            state = read_state(DONE_WINDOW_MS)
            h = _state_hash(state)
            if h != last:
                last = h
                broadcast(("data: " + json.dumps(state, ensure_ascii=False)
                           + "\n\n").encode("utf-8"))
            err_shown = False
        except Exception as exc:
            # ⚠️ 绝不能静默：之前这里一个 NameError 被吞掉，
            # 导致桥连上后只发一条快照就再也不推，看起来像"灯卡住了"。
            if not err_shown:
                print(f"[bridge] watch error: {exc.__class__.__name__}: {exc}",
                      file=sys.stderr, flush=True)
                err_shown = True
        time.sleep(WATCH_INTERVAL)


def _heartbeat_loop() -> None:
    """SSE 注释行当心跳，客户端会忽略，但网络设备看到有流量就不会断。"""
    while True:
        time.sleep(HEARTBEAT_SECONDS)
        broadcast(b": ping\n\n")


class _Handler(BaseHTTPRequestHandler):
    server_version = "dsh-state-bridge/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/events":
            self._serve_events()
            return
        if path not in ("/state", "/"):
            self.send_error(404, "only /state and /events are served")
            return
        try:
            payload = json.dumps(read_state(), ensure_ascii=False).encode("utf-8")
        except Exception as exc:  # 桥本身绝不能因为读状态而崩
            payload = json.dumps(
                {"state": "idle", "reason": f"bridge-error: {exc.__class__.__name__}"}
            ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        # 客户端是本地小程序，明确禁掉缓存，避免读到旧状态。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_events(self) -> None:
        """SSE：连接建立后不返回，一直挂着，由 _watch_loop 往里推。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            # 先推一条当前状态，客户端不用等下一次变化
            self.wfile.write(("data: " + json.dumps(read_state(),
                               ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return
        with _clients_lock:
            _clients.append(self)
        print(f"[bridge] client connected (total {len(_clients)})")
        try:
            # 挂住这条连接。写操作全部由 broadcast 在别的线程完成。
            while True:
                time.sleep(3600)
        except Exception:
            pass
        finally:
            with _clients_lock:
                if self in _clients:
                    _clients.remove(self)
                print(f"[bridge] client gone (total {len(_clients)})")

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默，别刷屏
        pass


def main() -> None:
    global DONE_WINDOW_MS

    ap = argparse.ArgumentParser(description="DSH status bridge")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3099)
    ap.add_argument("--done-seconds", type=float, default=DONE_WINDOW_MS / 1000,
                    help="完成状态保持多少秒（默认 300）")
    args = ap.parse_args()

    # 0（或负数）表示永不超时：绿灯只被下一次 turn 打断
    DONE_WINDOW_MS = int(args.done_seconds * 1000) if args.done_seconds > 0 else None

    threading.Thread(target=_watch_loop, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"dsh-state-bridge listening on http://{args.host}:{args.port}/state")
    print(f"  DSH_HOME       = {DSH_HOME}")
    print(f"  projcache dir  = {PROJCACHE_DIR}")
    print("  done window    = " + ("never expires (只被下一次 turn 打断)"
                                   if DONE_WINDOW_MS is None
                                   else f"{DONE_WINDOW_MS / 1000:.0f}s"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
