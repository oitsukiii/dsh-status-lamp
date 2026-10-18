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

try:
    from event_tail import current_phase, read_tail_events
except ImportError:  # 作为包被导入时
    from .event_tail import current_phase, read_tail_events

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
PROJCACHE_DIR = os.path.join(DSH_HOME, "storages", "session_projcache", "sessions")
SESSIONS_DIR = os.path.join(DSH_HOME, "sessions")

# 「完成」状态只亮这么多秒，之后回到空闲。
DONE_WINDOW_MS = 8000

# 状态文件这么久没更新，就认为 DSH 没在跑。
STALE_SECONDS = 20.0


def _newest_session_file() -> Optional[str]:
    files = glob.glob(os.path.join(PROJCACHE_DIR, "session-*.json"))
    if not files:
        return None
    # 文件名里带 uuid，但 mtime 才是「最近活跃」的可靠依据。
    return max(files, key=os.path.getmtime)


def _newest_event_log() -> Optional[str]:
    """最新的 session.v4.jsonl.zstd —— 用来读「此刻在做什么」。"""
    best: Optional[str] = None
    best_m = 0.0
    try:
        for proj in os.listdir(SESSIONS_DIR):
            sess_dir = os.path.join(SESSIONS_DIR, proj)
            if not os.path.isdir(sess_dir):
                continue
            for name in os.listdir(sess_dir):
                log = os.path.join(sess_dir, name, "session.v4.jsonl.zstd")
                if os.path.isfile(log):
                    m = os.path.getmtime(log)
                    if m > best_m:
                        best_m, best = m, log
    except OSError:
        return None
    return best


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

    # 等待回答的问题（DSH 会把待答问题记在这里）
    questions = (rows.get("userQuestions") or {}).get("val") or {}
    q_active = ((questions.get("questions") or {}).get("active")) or []

    # 事件流尾部 → 此刻在做什么。读失败不影响主流程。
    phase: dict[str, Any] = {}
    try:
        log = _newest_event_log()
        if log:
            events = read_tail_events(log, want=24)
            phase = current_phase(events)
            if phase.get("phase") == "done" and events:
                last_ms = events[-1].get("time") or 0
                if last_ms and (time.time() * 1000 - last_ms) > DONE_WINDOW_MS:
                    phase = {"phase": "idle"}
    except Exception:  # 事件流只是加分项，坏了也不能拖垮状态
        phase = {}

    # 优先级：离线 > 等回答 > 重试 > 调工具 > 思考 > 完成 > 空闲
    if age > STALE_SECONDS:
        state = "offline"
    elif q_active:
        state = "question"
    elif retry or phase.get("phase") == "retrying":
        state = "retrying"
    elif phase.get("phase") == "tool":
        state = "tool"
    elif open_turn is not None:
        state = "thinking"
    elif phase.get("phase") == "done":
        state = "done"
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
        "phase": phase.get("phase"),
        "tool": phase.get("tool"),
        "pending_questions": len(q_active),
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
