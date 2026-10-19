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
    完成任务时播放 assets/sounds/notify.wav（Windows 用 winsound，零依赖）

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
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    sys.exit("需要 tkinter（Windows 官方 Python 自带）")

# 提示音：Windows 上用标准库 winsound 播 WAV，零依赖。
SOUND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets", "sounds", "notify.wav")
_SOUND_OK = False
if sys.platform == "win32":
    try:
        import winsound
        _SOUND_OK = os.path.isfile(SOUND_FILE)
    except ImportError:  # pragma: no cover
        _SOUND_OK = False


def play_done_sound() -> None:
    """任务完成时响一下。异步播放，不阻塞 UI。"""
    if not _SOUND_OK:
        return
    try:
        winsound.PlaySound(SOUND_FILE,
                           winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
    except (RuntimeError, OSError):
        pass


# Pillow 是可选的：有它就能显示像素风素材，没有就退回画圆。
try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:  # pragma: no cover
    _HAS_PIL = False

# 让脚本能找到 dsh_lamp 包。两种部署形态都要支持：
#   ① 仓库结构：<repo>/client/lamp.py + <repo>/dsh_lamp/
#   ② 散装结构：<dir>/lamp.py       + <dir>/dsh_lamp/
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "dsh_lamp")):
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break

from dsh_lamp.backends import (FileBackend, SseBackend, make_backend,  # noqa: E402
                               zstd_available)
from dsh_lamp.paths import (config_file, default_dsh_home,  # noqa: E402
                            dsh_home, set_dsh_home)

DEFAULT_URL = os.environ.get("DSH_LAMP_URL", "http://127.0.0.1:3099/events")

# 项目地址 —— 发布到 GitHub 后把这里换成真实地址，复制出去的提示词就会跟着变
REPO_URL = "https://github.com/<你的用户名>/dsh-status-lamp"

# 「复制给 agent」的内容：一段可以直接发给 DSH 的提示词
AGENT_PROMPT = f"""请帮我部署一个「DSH 状态灯」的服务端桥。

项目地址：{REPO_URL}

请阅读仓库根目录的 AGENTS.md，按里面的步骤：
1. 在这台机器上安装并常驻运行状态桥（bridge/state_bridge.py）
2. 放行防火墙，但只对局域网放行 3099 端口
3. 完成后告诉我一个可以填进状态灯客户端的地址，格式是 http://<局域网IP>:3099/events

注意事项：
- 不要修改 DSH 的任何配置
- 不要重启 DSH（会中断我正在进行的会话）
- 不要把 3099 暴露到公网（这个端口没有认证）
"""

DEFAULT_CONFIG = {
    "mode": "local",                       # local | lan
    "server": "http://127.0.0.1:3099/events",
    "dsh_home": "",                        # 留空 = 默认 ~/.dsh
    "sound": True,
}


def default_home_display() -> str:
    """把默认 DSH 目录里的真实用户名换成 <用户名> 占位符。

    既让小白看得懂（给出具体形态），又不会在截图上泄漏用户名。
    Windows:  C:\\Users\\<用户名>\\.dsh
    macOS:    /Users/<用户名>/.dsh
    Linux:    /home/<用户名>/.dsh
    """
    home = os.path.expanduser("~")
    real = dsh_home()
    if not real.startswith(home):
        return real          # 用户自己指定过目录，就照实显示
    # 不要用 os.path.dirname —— 它按【当前平台】的分隔符工作，
    # 在 Linux 上处理 "C:\Users\x" 会返回空串。按字符串里的分隔符判断更稳。
    sep = "\\" if "\\" in home else "/"
    parent = home.rsplit(sep, 1)[0]
    masked = parent + sep + "<用户名>"
    return masked + real[len(home):]


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_file(), encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    if os.environ.get("DSH_LAMP_URL"):     # 环境变量优先，方便临时覆盖
        cfg["server"] = os.environ["DSH_LAMP_URL"]
        cfg["mode"] = "lan"
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    with open(config_file(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
POLL_MS = 700          # 仅在 SSE 不可用时退化使用
# SSE 读超时：必须大于桥的心跳间隔（20 秒），否则会误判断线。
# 没有这个超时的话，如果对端静默消失（桥卡死、网线拔掉），
# recv() 会永远阻塞 —— 灯不报错也不重连，静默死掉。
SSE_READ_TIMEOUT = 45.0
RECONNECT_MIN = 1.0    # 断线重连的最短等待
RECONNECT_MAX = 30.0   # 最长等待

# 透明色键：窗口里所有这个颜色的像素会被系统抠掉，从而实现「只有灯、没有窗口」。
BG_KEY = "#ff00ff"   # 洋红：灯罩是深灰，用近黑会误吃抗锯齿边缘

# 图片素材（有的话优先用，没有就退回画圆）
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "states")

LAMP_H = 160       # 灯的高度（像素）；宽度按素材比例算
MARGIN = 8         # 四周留白

# 状态 → 素材文件名
STATE_IMAGE = {
    "question":  "attention.png",
    "retrying":  "working.png",
    "tool":      "working.png",
    "thinking":  "working.png",
    "done":      "done.png",
    "idle":      "idle.png",
    "offline":   "offline.png",
    "working":   "working.png",   # 兼容旧版桥
}

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
    """一次性查状态 —— 只作 SSE 不可用时的退路。"""
    base = url.rsplit("/", 1)[0]
    try:
        with urllib.request.urlopen(base + "/state", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "offline", "reason": f"{exc.__class__.__name__}"}


def sse_worker(url: str, q: "queue.Queue") -> None:
    """后台线程：连 SSE 长连接，收到一条处理一条。

    tkinter 不是线程安全的，所以 on_state 里必须用 root.after() 切回主线程。
    断线后指数退避重连 —— 否则桥重启一次灯就永远死了。
    """
    backoff = RECONNECT_MIN
    while True:
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=SSE_READ_TIMEOUT) as resp:
                backoff = RECONNECT_MIN          # 连上了就重置退避
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":"):
                        continue                      # 心跳，忽略
                    if not line.startswith("data:"):
                        continue
                    try:
                        state = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    q.put(state)          # 只入队，绝不碰 tkinter
        except Exception:
            pass
        # 断线：退避后重连
        time.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


class Lamp:
    def __init__(self, cfg: dict, topmost: bool = True) -> None:
        self.cfg = cfg
        self.url = cfg["server"]
        self._backend = None
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

        self._q: queue.Queue = queue.Queue()
        self._n_push = 0
        self._photos: dict[str, object] = {}
        self._assets = self._load_assets()
        self.w, self.h = self._window_size()

        self.root.geometry(f"{self.w}x{self.h}+{self._saved_pos()}")

        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h,
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
        # 启动顺序优化：
        # 旧版先同步 fetch_state()，最坏会卡 2 秒才出窗口，而这时 SSE 还没连 ✗
        # 现在直接连 SSE（首条推送就是当前状态），窗口立刻出来。
        self._start_backend()
        self.root.after(80, self._drain)            # 主线程排空队列
        self.root.after(2500, self._fallback_fetch)  # 兜底：2.5s 还没收到就主动查一次

    def _start_backend(self) -> None:
        """按配置起后端：本机读文件 / 局域网连桥。"""
        if self._backend is not None:
            self._backend.stop()
        self._backend = make_backend(self.cfg, self._q.put)
        self._backend.start()

    def _fallback_fetch(self) -> None:
        """SSE 首条推送迟迟没来时的退路（比如桥没起、网络慢）。"""
        if self._n_push == 0:
            self._apply(fetch_state(self.url))

    # ---------- 位置记忆 ----------

    def _pos_file(self) -> str:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dsh-status-lamp.pos")

    # ---------- 素材 ----------

    def _load_assets(self) -> dict:
        """把状态图读进来，合成到色键背景上 —— 这样 tkinter 的透明色键才能生效。

        没有 Pillow 或没有素材时返回空字典，调用方会退回画圆。
        """
        if not _HAS_PIL:
            return {}
        want = set(STATE_IMAGE.values())
        out: dict = {}
        for fn in want:
            path = os.path.join(ASSET_DIR, fn)
            if not os.path.isfile(path):
                continue
            try:
                im = Image.open(path).convert("RGBA")
            except OSError:
                continue
            # 等比缩放到目标高度
            ratio = LAMP_H / im.size[1]
            size = (max(1, int(im.size[0] * ratio)), LAMP_H)
            im = im.resize(size, Image.LANCZOS)
            # 合成到色键底色上（RGBA 直接给 PhotoImage 会丢掉 alpha）
            bg = Image.new("RGB", size, BG_KEY)
            bg.paste(im, (0, 0), im)
            out[fn] = bg
        return out

    def _window_size(self) -> tuple:
        """窗口尺寸 = 素材尺寸 + 留白。"""
        if self._assets:
            any_im = next(iter(self._assets.values()))
            return (any_im.size[0] + MARGIN * 2, any_im.size[1] + MARGIN * 2)
        # 退回圆点：正方形
        side = 64 + 14 * 2
        return (side, side)

    # ---------- 位置记忆 ----------

    def _pos_file(self) -> str:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dsh-status-lamp.pos")

    def _saved_pos(self) -> str:
        try:
            with open(self._pos_file(), encoding="utf-8") as fh:
                x, y = (int(v) for v in fh.read().split(","))
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                if 0 <= x <= sw - 40 and 0 <= y <= sh - 40:
                    return f"{x}+{y}"
        except (OSError, ValueError):
            pass
        return (f"{self.root.winfo_screenwidth() - self.w - 40}+"
                f"{self.root.winfo_screenheight() - self.h - 90}")

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

        # 有素材就用素材（像素风红绿灯）
        fn = STATE_IMAGE.get(self.state)
        im = self._assets.get(fn) if fn else None
        if im is not None:
            photo = ImageTk.PhotoImage(im)
            self._photos[self.state] = photo      # 必须留引用，否则会被回收成空白
            c.create_image(self.w // 2, self.h // 2, image=photo)
            return

        # 没素材：退回画一个圆点
        pal = PALETTE.get(self.state, PALETTE["offline"])
        r = 32
        cx = cy = self.w / 2
        c.create_oval(cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8,
                      fill=pal["glow"], outline="", stipple="gray12")
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=pal["core"], outline=pal["ring"], width=2)
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
        self.menu.add_separator()
        self.menu.add_command(label="设置…", command=self.open_settings)
        self.menu.add_separator()
        self.var_top = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label="始终置顶", variable=self.var_top,
                                  command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.root.destroy)

    def _on_menu(self, ev) -> None:
        self.menu.tk_popup(ev.x_root, ev.y_root)

    def open_settings(self) -> None:
        """设置窗口：选模式后只显示该模式的配置。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("DSH 状态灯 — 设置")
        dlg.geometry("600x560")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)

        mode = tk.StringVar(value=self.cfg.get("mode", "local"))
        server = tk.StringVar(value=self.cfg.get("server", DEFAULT_CONFIG["server"]))
        # 输入框直接显示【当前生效的目录】，而不是空白 —— 否则用户不知道到底用的是哪
        dsh_home_var = tk.StringVar(value=self.cfg.get("dsh_home") or dsh_home())
        sound = tk.BooleanVar(value=bool(self.cfg.get("sound", True)))

        # ── 数据来源 ──
        tk.Label(dlg, text="数据来源", font=("", 10, "bold")).pack(
            anchor="w", padx=14, pady=(14, 4))
        tk.Radiobutton(dlg, text="本机模式 —— 直接读这台机器上的 DSH 数据（零配置）",
                       variable=mode, value="local",
                       command=lambda: _apply_mode()).pack(anchor="w", padx=24)
        tk.Radiobutton(dlg, text="局域网模式 —— 连接另一台机器上的状态桥",
                       variable=mode, value="lan",
                       command=lambda: _apply_mode()).pack(anchor="w", padx=24)

        # 两个模式的配置都挂在同一个容器里，一次只 pack 一个
        mode_area = tk.Frame(dlg)
        mode_area.pack(fill="x", padx=0, pady=(8, 0))

        # ── 本机模式配置 ──
        local_box = tk.LabelFrame(mode_area, text=" 本机模式 ")
        tk.Label(local_box, text="DSH 数据目录").pack(
            anchor="w", padx=10, pady=(8, 2))
        row = tk.Frame(local_box)
        row.pack(anchor="w", padx=12)
        home_ent = tk.Entry(row, textvariable=dsh_home_var, width=48)
        home_ent.pack(side="left")

        def _browse() -> None:
            from tkinter import filedialog
            picked = filedialog.askdirectory(
                title="选择 DSH 数据目录（里面有 storages 和 sessions 的那个）",
                initialdir=dsh_home_var.get() or dsh_home())
            if picked:
                dsh_home_var.set(picked)

        tk.Button(row, text="浏览…", command=_browse).pack(side="left", padx=(6, 0))
        home_hint = tk.Label(local_box, justify="left", wraplength=540, fg="#888",
                             font=("", 8), text="")
        home_hint.pack(anchor="w", padx=12, pady=(3, 8))

        # ── 局域网模式配置 ──
        lan_box = tk.LabelFrame(mode_area, text=" 局域网模式 ")
        tk.Label(lan_box, text="桥地址").pack(anchor="w", padx=10, pady=(8, 2))
        ent = tk.Entry(lan_box, textvariable=server, width=58)
        ent.pack(anchor="w", padx=12)

        guide = tk.LabelFrame(lan_box, text=" 还没有桥？ ")
        guide.pack(fill="x", padx=12, pady=(8, 8))
        tk.Label(guide, justify="left", wraplength=500, fg="#555",
                 text="点下面的按钮复制一段指令，发给你的 DSH，"
                      "让它配好服务器端软件并把地址回给你。").pack(
            anchor="w", padx=10, pady=(6, 4))

        def _copy_prompt() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(AGENT_PROMPT)
            status.configure(text="✓ 已复制，发给你的 DSH 即可", fg="#2a7")

        tk.Button(guide, text="复制配置指令", command=_copy_prompt).pack(
            anchor="w", padx=10, pady=(0, 4))
        tk.Label(guide, justify="left", wraplength=500, fg="#888", font=("", 8),
                 text=f"项目地址：{REPO_URL}").pack(anchor="w", padx=10, pady=(0, 6))

        # ── 通用选项 ──
        tk.Checkbutton(dlg, text="任务完成时播放提示音", variable=sound).pack(
            anchor="w", padx=14, pady=(10, 0))

        # 解码器状态
        dep = tk.Frame(dlg)
        dep.pack(anchor="w", fill="x", padx=14, pady=(8, 0))
        dep_label = tk.Label(dep, justify="left", wraplength=420, font=("", 8))
        dep_label.pack(side="left", anchor="w")
        why_label = tk.Label(dlg, justify="left", wraplength=560, fg="#999", font=("", 8),
            text="为什么要装：DSH 的详细日志是 zstd 压缩存的，\n"
                 "装个解码器才能读出「思考中 / 调工具」这类细粒度状态。\n"
                 "装一次即可，约 1 MB。")

        def _refresh_dep() -> None:
            """按钮和说明【始终可见】—— 有解码器时按钮变成「重新安装」。"""
            from dsh_lamp.event_tail import decompressor_kind
            kind = decompressor_kind()
            if kind == "none":
                dep_label.pack_forget()
                install_btn.configure(text="一键安装解码器")
                install_btn.pack(side="left", anchor="w")
            else:
                dep_label.configure(fg="#888", text=f"zstd 解码：{kind} ✓")
                dep_label.pack(side="left", anchor="w")
                install_btn.configure(text="重新安装解码器")
                install_btn.pack(side="left", anchor="w", padx=(10, 0))
            # 说明文字任何情况下都保留
            why_label.pack(anchor="w", padx=14, pady=(4, 0))

        def _one_click_install() -> None:
            install_btn.configure(state="disabled", text="安装中…")
            dep_label.configure(fg="#888", text="正在安装 pyzstd…")
            dep_label.pack(side="left", anchor="w")

            def work() -> None:
                ok, msg = False, ""
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "pyzstd"],
                        capture_output=True, text=True, timeout=240)
                    ok = proc.returncode == 0
                    if not ok:
                        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                        msg = tail[-1][:160] if tail else f"退出码 {proc.returncode}"
                except Exception as exc:
                    msg = exc.__class__.__name__
                self.root.after(0, lambda: done(ok, msg))

            def done(ok: bool, msg: str) -> None:
                install_btn.configure(state="normal", text="一键安装解码器")
                if ok:
                    _refresh_dep()
                    status.configure(text="✓ 装好了——重启软件生效", fg="#2a7")
                else:
                    dep_label.configure(fg="#c33", text=f"✗ 安装失败：{msg}")
                    dep_label.pack(side="left", anchor="w")
                    install_btn.configure(text="重新安装解码器")
                    install_btn.pack(side="left", anchor="w", padx=(10, 0))
                    status.configure(text="可以手动执行：pip install pyzstd", fg="#c33")

            threading.Thread(target=work, daemon=True).start()

        install_btn = tk.Button(dep, text="一键安装解码器", command=_one_click_install)

        status = tk.Label(dlg, text="", fg="#2a7", anchor="w")
        status.pack(anchor="w", padx=14, pady=(6, 0))

        # ── 模式切换 ──
        def _apply_mode() -> None:
            local = mode.get() == "local"
            if local:
                lan_box.pack_forget()
                local_box.pack(fill="x", padx=14, pady=(6, 0))
                home_hint.configure(
                    text=f"默认是 {default_home_display()}，"
                         f"里面应该有 storages/ 和 sessions/ 两个子目录。")
            else:
                local_box.pack_forget()
                lan_box.pack(fill="x", padx=14, pady=(6, 0))

        # ── 按钮 ──
        def _test() -> None:
            if mode.get() == "local":
                try:
                    from dsh_lamp.state import read_state
                    set_dsh_home(dsh_home_var.get().strip())
                    st = read_state(None)
                    status.configure(
                        text=f"✓ 读到了：{st.get('state')}（轮次 {st.get('turn')}）", fg="#2a7")
                except Exception as e:
                    # 只报异常类型，不把完整路径打到界面上（截图会泄漏用户名）
                    status.configure(text=f"✗ 读取失败：{e.__class__.__name__}", fg="#c33")
                return
            base = server.get().strip().rsplit("/", 1)[0]
            try:
                with urllib.request.urlopen(base + "/state", timeout=4) as r:
                    data = json.loads(r.read().decode("utf-8"))
                status.configure(text=f"✓ 连上了：{data.get('state')}", fg="#2a7")
            except Exception as e:
                status.configure(text=f"✗ 连不上：{e.__class__.__name__}", fg="#c33")

        def _save() -> None:
            # 和默认目录一致就不写死 —— 否则以后换机器/换用户名会失效
            picked = dsh_home_var.get().strip()
            try:
                same_as_default = (os.path.normcase(os.path.normpath(picked))
                                   == os.path.normcase(os.path.normpath(default_dsh_home())))
            except Exception:
                same_as_default = False
            self.cfg.update({"mode": mode.get(), "server": server.get().strip(),
                             "dsh_home": "" if same_as_default else picked,
                             "sound": bool(sound.get())})
            self.url = self.cfg["server"]
            set_dsh_home(self.cfg["dsh_home"])
            try:
                save_config(self.cfg)
            except OSError as e:
                status.configure(text=f"✗ 保存失败：{e}", fg="#c33")
                return
            self._n_push = 0
            self._start_backend()
            status.configure(text="✓ 已保存并重连", fg="#2a7")

        bar = tk.Frame(dlg)
        bar.pack(side="bottom", fill="x", padx=14, pady=12)
        tk.Button(bar, text="测试连接", command=_test).pack(side="left")
        tk.Button(bar, text="保存并重连", command=_save).pack(side="right")
        tk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=8)

        _apply_mode()
        _refresh_dep()

    def _reset_pos(self) -> None:
        self.root.geometry(f"+{self.root.winfo_screenwidth() - self.w - 40}"
                           f"+{self.root.winfo_screenheight() - self.h - 90}")
        self._save_pos()

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.var_top.get())

    # ---------- 轮询 ----------

    def _drain(self) -> None:
        """主线程排空队列 —— 这是 tkinter + 线程唯一安全的方式。"""
        got = 0
        try:
            while got < 20:
                data = self._q.get_nowait()
                self._n_push += 1
                self._apply(data)
                got += 1
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def poll(self) -> None:
        """退路：SSE 没连上时的一次性查询。"""
        self._apply(fetch_state(self.url))

    def _apply(self, data: dict) -> None:
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

        if os.environ.get("DSH_LAMP_DEBUG"):
            print(f"[lamp] push#{self._n_push} state={state} phase={data.get('phase')}",
                  flush=True)
        if state != self._last_state:
            prev = self._last_state
            self.state = state
            self._last_state = state
            self._draw()
            # 只在【进入】完成态时响，且不是从离线恢复过来的噪声
            if state == "done" and prev not in (None, "offline"):
                if self.cfg.get("sound", True):
                    play_done_sound()

        self.root.title(f"DSH — {self.detail}  [推送 {self._n_push}]")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser(description="DSH status lamp")
    ap.add_argument("--url", default=None, help="强制使用局域网模式并指定桥地址")
    ap.add_argument("--local", action="store_true", help="强制本机模式")
    ap.add_argument("--no-topmost", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    set_dsh_home(cfg.get("dsh_home"))
    if args.url:
        cfg["mode"], cfg["server"] = "lan", args.url
    if args.local:
        cfg["mode"] = "local"
    Lamp(cfg, topmost=not args.no_topmost).run()


if __name__ == "__main__":
    main()
