"""读 DSH 会话事件流的【尾部】—— 不解压整个文件。

DSH 把事件写成 `session.v4.jsonl.zstd`，这个文件是**多帧** zstd（实测 34 MB
的文件里有几千个帧）。多帧的好处是每个帧可以独立解压，所以只要从某个帧头
开始读最后一小段，就能拿到最近的几个事件 —— 而不必把 128 MB 全解出来。

为什么要读事件流
----------------
`session_projcache` 里的 `sessionStats` 是**事后汇总**的（`toolMs`/`llmMs` 只在
step 结束时更新），所以判断不了「此刻在干什么」。事件流是**即时写入**的：

    最后一条是 step/start  ⇒ 这一步的 LLM 还在推理/生成
    最后一条是 tool/call   ⇒ 工具正在执行（data.name 就是工具名）
    最后一条是 turn/end    ⇒ 这一轮结束了

这样才有「思考中」「调工具中」的区别。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from typing import Any, Optional

# 解压 zstd 的三条路，按「零依赖程度」排序：
#   ① compression.zstd —— Python 3.14+ 的标准库 ✓✓ 真正零依赖
#   ② pyzstd（pip install pyzstd）—— 3.9~3.13 的通行做法
#   ③ zstd 命令行 —— Linux/macOS 一般自带，Windows 上通常没有
try:
    from compression import zstd as _stdlib_zstd  # type: ignore  # Python 3.14+
    _HAS_STDLIB = True
except ImportError:
    _stdlib_zstd = None
    _HAS_STDLIB = False

try:
    import pyzstd  # type: ignore
    _HAS_PYZSTD = True
except ImportError:
    _HAS_PYZSTD = False

try:
    import shutil as _shutil
    _HAS_ZSTD_CLI = _shutil.which("zstd") is not None
except Exception:
    _HAS_ZSTD_CLI = False

# 往后读这么多字节去找帧头。实测 200 KB 里有 20 个帧头，够用。
TAIL_WINDOW = 400_000
FRAME_MAGIC = b"\x28\xb5\x2f\xfd"

# 只关心这些事件；其余忽略。
INTERESTING = {
    "turn/start", "turn/end",
    "step/start", "step/end",
    "assistant/message", "assistant/attempt",
    "tool/call", "tool/result",
    "user/message",
    "llm/retry", "llm/retry-started",
}


def decompressor_available() -> bool:
    """有没有能解 zstd 的办法（决定能不能读到细粒度状态）。"""
    return _HAS_STDLIB or _HAS_PYZSTD or _HAS_ZSTD_CLI


def decompressor_kind() -> str:
    """当前用的是哪种解码器 —— 给设置窗口显示。"""
    if _HAS_STDLIB:
        return "stdlib"      # Python 3.14+ 自带
    if _HAS_PYZSTD:
        return "pyzstd"
    if _HAS_ZSTD_CLI:
        return "cli"
    return "none"


def _decompress(chunk: bytes) -> Optional[str]:
    """解压一段从帧头开始的 zstd 数据；不是合法起点就返回 None。

    这堆字节可能是「几个完整帧 + 一个被截断的尾巴」，两种解码器都要能容忍：
    · pyzstd: 读到坏帧就抛异常，取它已经解出来的部分
    · zstd 命令行: 解到坏帧会返回非零码，但 stdout 里仍有前面解好的内容
    """
    if not chunk.startswith(FRAME_MAGIC):
        return None

    if _HAS_STDLIB:
        # Python 3.14+ 的标准库解码器。多帧 + 截断尾巴也要容忍。
        try:
            out = bytearray()
            with _stdlib_zstd.ZstdFile(io.BytesIO(chunk), "rb") as fh:
                while True:
                    piece = fh.read(65536)
                    if not piece:
                        break
                    out += piece
            if out:
                return bytes(out).decode("utf-8", "replace")
        except Exception:
            pass
        try:
            return _stdlib_zstd.decompress(chunk).decode("utf-8", "replace")
        except Exception:
            pass

    if _HAS_PYZSTD:
        # 流式解码：能容忍尾部被截断（跨帧连续读）
        try:
            dctx = pyzstd.ZstdDecompressor()
            out = bytearray()
            with dctx.stream_reader(io.BytesIO(chunk)) as reader:
                while True:
                    piece = reader.read(65536)
                    if not piece:
                        break
                    out += piece
            if out:
                return bytes(out).decode("utf-8", "replace")
        except Exception:
            pass
        # 退一步：当作单块解（某些版本对小输入更宽松）
        try:
            return pyzstd.decompress(chunk).decode("utf-8", "replace")
        except Exception:
            pass

    if _HAS_ZSTD_CLI:
        try:
            proc = subprocess.run(
                ["zstd", "-dc"], input=chunk, capture_output=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 and not proc.stdout:
            return None
        return proc.stdout.decode("utf-8", "replace")

    return None


def read_tail_events(path: str, want: int = 40) -> list[dict[str, Any]]:
    """返回文件末尾的至多 `want` 个事件（按 seq 升序）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size <= 0:
        return []

    window = min(TAIL_WINDOW, size)
    try:
        with open(path, "rb") as fh:
            fh.seek(size - window)
            data = fh.read()
    except OSError:
        return []

    # 从窗口里的第一个帧头开始解压。若这个偏移恰好落在帧内部（假帧头），
    # 解压会失败或产出乱码 —— 那就往后挪，最多试 8 次。
    start = data.find(FRAME_MAGIC)
    attempts = 0
    text: Optional[str] = None
    while start != -1 and attempts < 8:
        text = _decompress(data[start:])
        if text and '"seq"' in text:
            break
        attempts += 1
        start = data.find(FRAME_MAGIC, start + 1)

    if not text:
        return []

    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type"):
            events.append(ev)

    # 按 seq 去重（多试几次可能重复），保留最后 want 条
    seen: set[Any] = set()
    uniq: list[dict[str, Any]] = []
    for ev in reversed(events):
        key = ev.get("seq")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ev)
    uniq.reverse()
    return uniq[-want:]


def current_phase(events: list[dict[str, Any]]) -> dict[str, Any]:
    """从事件尾部推断「此刻在做什么」。"""
    interested = [e for e in events if e.get("type") in INTERESTING]
    if not interested:
        return {"phase": "unknown"}

    last = interested[-1]
    typ = last.get("type")
    data = last.get("data") or {}

    if typ == "tool/call":
        return {
            "phase": "tool",
            "tool": data.get("name"),
            "seq": last.get("seq"),
        }
    if typ == "step/start":
        return {"phase": "thinking", "step": data.get("step"), "seq": last.get("seq")}
    if typ == "assistant/message":
        return {"phase": "replying", "seq": last.get("seq")}
    if typ == "turn/end":
        return {"phase": "done", "seq": last.get("seq")}
    if typ == "turn/start":
        return {"phase": "starting", "seq": last.get("seq")}
    if typ == "tool/result":
        return {"phase": "thinking", "after_tool": data.get("name"), "seq": last.get("seq")}
    if typ in ("llm/retry", "llm/retry-started"):
        return {"phase": "retrying", "seq": last.get("seq")}
    if typ == "user/message":
        return {"phase": "queued", "seq": last.get("seq")}
    return {"phase": "unknown", "last_type": typ}
