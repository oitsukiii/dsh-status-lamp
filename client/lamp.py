#!/usr/bin/env python3
"""DSH 状态灯 —— 一个浮在所有窗口之上的小圆灯，显示 DSH 正在干什么。

纯标准库（tkinter + urllib），Windows 上双击即可运行，不需要 pip install。

它从「状态桥」拉取状态（默认 http://192.168.2.102:3099/state），
因为 DSH 可能跑在另一台机器（比如 NAS）上。

状态与颜色
----------
    question  黄   有等你回答的问题
    retrying  红   正在重试 LLM 请求
    tool      蓝   正在执行工具（标题里带工具名）
    thinking  绿   LLM 正在推理/生成
    done      青   刚完成一轮（8 秒内）
    idle      灰白 没有进行中的 turn
    offline   暗灰 状态桥连不上，或状态文件太久没更新

交互
----
    左键拖动   移动位置（松手后记住）
    右键       菜单：重新定位 / 置顶开关 / 退出
    双击       立刻刷新一次

用法
----
    pythonw client/lamp.py                 # 无控制台窗口
    python  client/lamp.py --url http://192.168.2.102:3099/state
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    sys.exit("需要 tkinter（Windows 官方 Python 自带）")

DEFAULT_URL = os.environ.get("DSH_LAMP_URL", "http://192.168.2.102:3099/state")
POLL_MS = 700

# 透明色键：窗口里所有这个颜色的像素会被系统抠掉，从而实现「只有灯、没有窗口」。
BG_KEY = "#010203"

SIZE = 64          # 灯的直径（像素）
MARGIN = 14        # 留白，给光晕

# 每个状态一个颜色。顺序按「越需要你注意，越靠前」。
PALETTE = {
    "question":  {"core": "#f1c40f", "glow": "#fbeaa0", "ring": "#9a7d0a"},  # 黄：等你回答
    "retrying":  {"core": "#e74c3c", "glow": "#f5b7b1", "ring": "#922b21"},  # 红：重试
    "tool":      {"core": "#3498db", "glow": "#aed6f1", "ring": "#1f618d"},  # 蓝：调工具
    "thinking":  {"core": "#2ecc71", "glow": "#a8f0c6", "ring": "#1e8449"},  # 绿：推理/生成
    "done":      {"core": "#1abc9c", "glow": "#a3e4d7", "ring": "#117864"},  # 青：刚完成
    "idle":      {"core": "#b8b8b0", "glow": "#e8e8e2", "ring": "#8a8a82"},  # 灰白：空闲
    "offline":   {"core": "#4a4a48", "glow": "#6e6e6a", "ring": "#2a2a28"},  # 暗灰：离线
    # 兼容旧版桥（只返回 working）
    "working":   {"core": "#2ecc71", "glow": "#a8f0c6", "ring": "#1e8449"},
}

LABEL = {
    "question":  "等你回答",
    "retrying":  "重试中",
    "tool":      "调工具",
    "thinking":  "思考中",
    "done":      "完成",
    "idle":      "空闲",
    "offline":   "离线",
    "working":   "工作中",
}


def fetch_state(url: str, timeout: float = 2.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "offline", "reason": f"{exc.__class__.__name__}"}


class Lamp:
    def __init__(self, url: str, topmost: bool = True) -> None:
        self.url = url
        self.state = "offline"
        self.detail = ""
        self._last_state = None

        self.root = tk.Tk()
        self.root.title("DSH Status Lamp")
        self.root.overrideredirect(True)          # 无边框
        self.root.configure(bg=BG_KEY)
        self.root.attributes("-topmost", topmost)

        # Windows：用色键实现真透明背景（灯之外完全不挡视线）
        try:
            self.root.attributes("-transparentcolor", BG_KEY)
        except tk.TclError:
            # 非 Windows 平台没有这个属性，退化成半透明整窗
            self.root.attributes("-alpha", 0.92)

        side = SIZE + MARGIN * 2
        self.root.geometry(f"{side}x{side}+{self._saved_pos(side)}")

        self.canvas = tk.Canvas(self.root, width=side, height=side,
                                bg=BG_KEY, highlightthickness=0)
        self.canvas.pack()

        self._draw()

        # 交互
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.poll())
        self.canvas.bind("<Button-3>", self._on_menu)

        self._build_menu()
        self.poll()

    # ---------- 位置记忆 ----------

    def _pos_file(self) -> str:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dsh-status-lamp.pos")

    def _saved_pos(self, side: int) -> str:
        try:
            with open(self._pos_file(), encoding="utf-8") as fh:
                x, y = (int(v) for v in fh.read().split(","))
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                if 0 <= x <= sw - 40 and 0 <= y <= sh - 40:
                    return f"{x}+{y}"
        except (OSError, ValueError):
            pass
        # 默认右下角
        return f"{self.root.winfo_screenwidth() - side - 40}+" \
               f"{self.root.winfo_screenheight() - side - 90}"

    def _save_pos(self) -> None:
        try:
            with open(self._pos_file(), "w", encoding="utf-8") as fh:
                fh.write(f"{self.root.winfo_x()},{self.root.winfo_y()}")
        except OSError:
            pass

    # ---------- 绘制 ----------

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        pal = PALETTE[self.state]
        r = SIZE / 2
        cx = cy = MARGIN + r

        # 光晕：两层虚点圆 + 一层实心描边，纯 tkinter 也能做出柔光感
        c.create_oval(cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8,
                      fill=pal["glow"], outline="", stipple="gray12")
        c.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
                      fill=pal["glow"], outline="", stipple="gray25")
        # 主体
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=pal["core"], outline=pal["ring"], width=2)
        # 高光（左上一点，增加立体感）
        c.create_oval(cx - r * 0.55, cy - r * 0.6,
                      cx - r * 0.15, cy - r * 0.2,
                      fill="#ffffff", outline="", stipple="gray50")

    # ---------- 拖拽 ----------

    def _on_press(self, ev) -> None:
        self._dx, self._dy = ev.x, ev.y

    def _on_drag(self, ev) -> None:
        self.root.geometry(f"+{self.root.winfo_x() + ev.x - self._dx}"
                           f"+{self.root.winfo_y() + ev.y - self._dy}")

    def _on_release(self, _ev) -> None:
        self._save_pos()

    # ---------- 菜单 ----------

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立刻刷新", command=self.poll)
        self.menu.add_command(label="回到右下角", command=self._reset_pos)
        self.var_top = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label="始终置顶", variable=self.var_top,
                                  command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.root.destroy)

    def _on_menu(self, ev) -> None:
        self.menu.tk_popup(ev.x_root, ev.y_root)

    def _reset_pos(self) -> None:
        side = SIZE + MARGIN * 2
        self.root.geometry(f"+{self.root.winfo_screenwidth() - side - 40}"
                           f"+{self.root.winfo_screenheight() - side - 90}")
        self._save_pos()

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.var_top.get())

    # ---------- 轮询 ----------

    def poll(self) -> None:
        data = fetch_state(self.url)
        state = data.get("state", "offline")
        if state not in PALETTE:
            state = "offline"

        bits = [LABEL[state]]
        # 调工具时把工具名也显示出来，这是信息量最大的一项
        if state == "tool" and data.get("tool"):
            bits[-1] = f"{LABEL[state]} · {data['tool']}"
        if data.get("phase") and data.get("phase") != "unknown":
            bits.append(f"[{data['phase']}]")
        if data.get("turn") is not None:
            bits.append(f"轮次 {data['turn']}")
        if data.get("context_ratio") is not None:
            bits.append(f"上下文 {data['context_ratio'] * 100:.0f}%")
        if data.get("pending_questions"):
            bits.append(f"待答 {data['pending_questions']}")
        if data.get("reason"):
            bits.append(str(data["reason"]))
        self.detail = " · ".join(bits)

        if state != self._last_state:
            self.state = state
            self._last_state = state
            self._draw()

        self.root.title(f"DSH — {self.detail}")
        self.root.after(POLL_MS, self.poll)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser(description="DSH status lamp")
    ap.add_argument("--url", default=DEFAULT_URL, help="状态桥地址")
    ap.add_argument("--no-topmost", action="store_true")
    args = ap.parse_args()
    Lamp(args.url, topmost=not args.no_topmost).run()


if __name__ == "__main__":
    main()
