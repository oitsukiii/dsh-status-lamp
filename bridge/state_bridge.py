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
import glob
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
PROJCACHE_DIR = os.path.join(DSH_HOME, "storages", "session_projcache", "sessions")

# 状态文件这么久没更新，就认为 DSH 没在跑。
STALE_SECONDS = 20.0


def _newest_session_file() -> Optional[str]:
    files = glob.glob(os.path.join(PROJCACHE_DIR, "session-*.json"))
    if not files:
        return None
    # 文件名里带 uuid，但 mtime 才是「最近活跃」的可靠依据。
    return max(files, key=os.path.getmtime)


def _ratio(ctx: dict[str, Any]) -> Optional[float]:
    used = ctx.get("surfaceTokens")
    total = ctx.get("contextWindow")
    if not used or not total:
        return None
    return round(used / total, 3)


def read_state() -> dict[str, Any]:
    """把 DSH 的落盘状态折叠成一个给客户端看的 JSON。"""
    path = _newest_session_file()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if path is None:
        return {"state": "offline", "reason": "no-session-file", "updated_at": now}

    age = time.time() - os.path.getmtime(path)
    session = os.path.basename(path).removeprefix("session-").removesuffix(".json")

    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "state": "offline",
            "reason": f"read-error: {exc.__class__.__name__}",
            "age_s": round(age, 1),
            "session": session[:12],
            "updated_at": now,
        }

    rows = (doc.get("record") or {}).get("rows") or {}
    turn_boundary = (rows.get("turnBoundary") or {}).get("val") or {}
    retry = (rows.get("llmRetry") or {}).get("val") or {}
    subagent = (rows.get("subagent") or {}).get("val") or {}
    context = (rows.get("contextPressure") or {}).get("val") or {}

    open_turn = turn_boundary.get("openTurnStartSeq")
    last_step = (turn_boundary.get("lastStepBoundary") or {}).get("kind")

    # 判据优先级：文件太旧 > 在重试 > 有 turn 开着 > 空闲
    if age > STALE_SECONDS:
        state = "offline"
    elif retry:
        state = "retrying"
    elif open_turn is not None:
        state = "working"
    else:
        state = "idle"

    return {
        "state": state,
        "age_s": round(age, 1),
        "session": session[:12],
        "turn": turn_boundary.get("lastTurn"),
        "open_turn": open_turn is not None,
        "last_step": last_step,
        "retry": bool(retry),
        "subagent": bool(subagent),
        "context_ratio": _ratio(context),
        "updated_at": now,
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "dsh-state-bridge/1.0"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 的命名约定)
        if self.path.split("?")[0] not in ("/state", "/"):
            self.send_error(404, "only /state is served")
            return
        try:
            payload = json.dumps(read_state(), ensure_ascii=False).encode("utf-8")
        except Exception as exc:  # 桥本身绝不能因为读状态而崩
            payload = json.dumps(
                {"state": "offline", "reason": f"bridge-error: {exc.__class__.__name__}"}
            ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        # 客户端是本地小程序，明确禁掉缓存，避免读到旧状态。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默，别刷屏
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="DSH status bridge")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3099)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"dsh-state-bridge listening on http://{args.host}:{args.port}/state")
    print(f"  DSH_HOME       = {DSH_HOME}")
    print(f"  projcache dir  = {PROJCACHE_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
