"""从 DSH 的落盘数据推导状态 —— 纯逻辑，不含任何网络代码。

本机模式（客户端直接读文件）和局域网模式（桥通过 HTTP 提供）共用这一份实现。
"""

from __future__ import annotations

import glob
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .event_tail import current_phase, read_tail_events
from .paths import projcache_dir, sessions_dir

# 状态文件这么久没更新，就认为 DSH 没在跑。
#
# 实测 DSH 大约每 5~7 秒写一次 projcache，所以 20 秒只有 3 倍余量 ——
# 一旦它进入「长时间空闲不写盘」的状态，就会被误判为离线 ✗
# 放宽到 60 秒：真挂了最多晚 40 秒发现，但不会在正常空闲时误报。
STALE_SECONDS = 60.0

# 「完成」状态亮多久。None 表示永不超时（只被下一次 turn 打断）。
DONE_WINDOW_MS: Optional[int] = 180_000


def _newest_session_file() -> Optional[str]:
    files = glob.glob(os.path.join(projcache_dir(), "session-*.json"))
    if not files:
        return None
    # 文件名里带 uuid，但 mtime 才是「最近活跃」的可靠依据。
    return max(files, key=os.path.getmtime)


def _newest_event_log() -> Optional[str]:
    """最新的 session.v4.jsonl.zstd —— 用来读「此刻在做什么」。"""
    best: Optional[str] = None
    best_m = 0.0
    try:
        for proj in os.listdir(sessions_dir()):
            sess_dir = os.path.join(sessions_dir(), proj)
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


def read_state(done_window_ms: "int | None | bool" = True) -> dict[str, Any]:
    """把 DSH 的落盘状态折叠成一个给客户端看的 JSON。

    done_window_ms: True = 用模块默认值；None = 永不超时；数字 = 毫秒。
    """
    window = DONE_WINDOW_MS if done_window_ms is True else done_window_ms
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

    # 等待审批：sessionStats.pendingCalls 非空 ⇒ 有工具调用卡住了。
    #
    # 这个字段只有在 approval 模式为 ask/always 时才可能有值。
    # 实测对照：
    #   approval=never 的机器  ⇒ 跑长命令时 pendingCalls 始终是 {}  ✗
    #   approval=ask 的机器    ⇒ 弹出审批框时 pendingCalls 有那个 call ✓
    # 所以它比 userQuestions 更适合表示「审批挂起」。
    stats = (rows.get("sessionStats") or {}).get("val") or {}
    pending_calls = stats.get("pendingCalls") or {}

    # 事件流尾部 → 此刻在做什么。读失败不影响主流程。
    phase: dict[str, Any] = {}
    try:
        log = _newest_event_log()
        if log:
            events = read_tail_events(log, want=24)
            phase = current_phase(events)
            if phase.get("phase") == "done" and events and window is not None:
                last_ms = events[-1].get("time") or 0
                if last_ms and (time.time() * 1000 - last_ms) > window:
                    phase = {"phase": "idle"}
    except Exception:  # 事件流只是加分项，坏了也不能拖垮状态
        phase = {}

    # 优先级：离线 > 等回答 > 重试 > 调工具 > 思考 > 完成 > 空闲
    if age > STALE_SECONDS:
        state = "offline"
    elif pending_calls:
        # 工具调用卡着不动 = 在等你点「允许」⇒ 红灯
        state = "question"
    elif q_active:
        state = "question"
    elif retry or phase.get("phase") == "retrying":
        state = "retrying"
    elif phase.get("phase") == "tool":
        state = "tool"
    # ⭐ 关键：事件日志比 projcache 写得快得多。
    # 新一轮刚开始时 projcache 里 openTurnStartSeq 可能还是 null（旧快照），
    # 但日志里已经有 turn/start / step/start 了 —— 用日志判断，别等快照。
    elif phase.get("phase") in ("thinking", "replying", "starting", "queued"):
        state = "working"
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
        "pending_calls": len(pending_calls),
        "done_age_s": (
            round((time.time() * 1000 - (events[-1].get("time") or 0)) / 1000, 1)
            if phase.get("phase") in ("done",) and events else None
        ),
        "updated_at": now,
    }


VOLATILE = ("updated_at", "age_s", "done_age_s")




# 这些字段每次读取都会变，算哈希时必须剔除，否则会「看起来一直在变」
VOLATILE_FIELDS = ("updated_at", "age_s", "done_age_s")


def state_hash(state: dict[str, Any]) -> str:
    """状态指纹 —— 只用来判断「有没有真的变化」。"""
    import hashlib
    stable = {k: v for k, v in state.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha1(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
