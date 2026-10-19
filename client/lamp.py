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

def _enable_dpi_awareness() -> None:
    """Windows 高 DPI：不声明感知的话，系统会把窗口位图拉伸，字全糊。

    必须在创建 Tk 之前调用，否则无效。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # 2 = per-monitor DPI aware（多显示器不同缩放时也正确）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover
    sys.exit("需要 tkinter（Windows 官方 Python 自带）")

# 提示音：Windows 上用标准库 winsound 播 WAV，零依赖。
def _find_asset_dir(*parts) -> str:
    """素材可能在 <脚本目录>/assets 或 <上一级>/assets（仓库结构 client/ 与根并列）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.dirname(here)):
        cand = os.path.join(base, "assets", *parts)
        if os.path.isdir(cand) or os.path.isfile(cand):
            return cand
    return os.path.join(here, "assets", *parts)


SOUND_FILE = _find_asset_dir("sounds", "notify.wav")
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
from dsh_lamp.i18n import (LANGS, LANG_NAMES, normalize,  # noqa: E402
                           t)
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
    "lang": "zh",                          # zh | en
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
ASSET_DIR = _find_asset_dir("states")

LAMP_H = 160       # 灯的高度（像素）；宽度按素材比例算

# 呼吸灯：亮度在 BREATH_MIN..1.0 之间来回，走三角波
BREATH_LEVELS = 30     # 亮度档数（越多越平滑；亮度帧惰性生成，不占启动时间）
BREATH_MIN = 0.75      # 最暗 = 75%
BREATH_STEP_MS = 45    # 每档停留毫秒（30 档 × 45ms ≈ 一个方向 1.35 秒）
BREATH_HOLD_MS = 1000  # 到最亮后停留多久再往回落
MARGIN = 8         # 四周留白

# 状态 → 素材文件名
STATE_IMAGE = {
    "question":  "attention.png",
    "retrying":  "working.png",
    "tool":      "working.png",
    "thinking":  "working.png",
    "done":      "done.png",
    "idle":      "idle.png",
    "working":   "working.png",   # 兼容旧版桥
}

# 每个状态一个颜色。顺序按「越需要你注意，越靠前」。
PALETTE = {
    "question":  {"core": "#e74c3c", "glow": "#f5b7b1", "ring": "#922b21"},  # 红：需你介入（审批/提问）
    "retrying":  {"core": "#f1c40f", "glow": "#fbeaa0", "ring": "#9a7d0a"},  # 黄：重试
    "tool":      {"core": "#f1c40f", "glow": "#fbeaa0", "ring": "#9a7d0a"},  # 黄：调工具
    "thinking":  {"core": "#f1c40f", "glow": "#fbeaa0", "ring": "#9a7d0a"},  # 黄：推理/生成
    "done":      {"core": "#1abc9c", "glow": "#a3e4d7", "ring": "#117864"},  # 青：刚完成
    "idle":      {"core": "#b8b8b0", "glow": "#e8e8e2", "ring": "#8a8a82"},  # 灰白：空闲
    # 兼容旧版桥（只返回 working）
    "working":   {"core": "#f1c40f", "glow": "#fbeaa0", "ring": "#9a7d0a"},
}

LABEL = {
    "question":  "等你回答",
    "retrying":  "重试中",
    "tool":      "调工具",
    "thinking":  "思考中",
    "done":      "完成",
    "idle":      "空闲",
    "working":   "工作中",
}


def fetch_state(url: str, timeout: float = 2.0) -> dict:
    """一次性查状态 —— 只作 SSE 不可用时的退路。"""
    base = url.rsplit("/", 1)[0]
    try:
        with urllib.request.urlopen(base + "/state", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "idle", "reason": f"{exc.__class__.__name__}"}


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
        self.lang = normalize(cfg.get("lang"))
        self.url = cfg["server"]
        self._backend = None
        self.state = "idle"
        self.detail = ""
        self._last_state = None

        self.root = tk.Tk()
        # 让 tk 的「点」按真实 DPI 换算 —— 字体才不会又小又糊
        try:
            dpi = self.root.winfo_fpixels("1i")
            self.root.tk.call("tk", "scaling", dpi / 72.0)
            self.dpi_scale = dpi / 96.0
        except Exception:
            self.dpi_scale = 1.0
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
        self._ui_q: queue.Queue = queue.Queue()   # 后台线程请求主线程做的事
        self._n_push = 0
        self._frame_cache: dict = {}        # 状态 → 亮度帧（惰性生成）
        self._level = BREATH_LEVELS - 1     # 当前亮度档（从最亮开始）
        self._breath_pos = 0
        self._item = None
        self._last_rx = time.time()      # 最后一次收到数据的时间（含心跳）
        self._watchdog_ms = 60000        # 局域网模式超过这么久没数据 ⇒ 判离线
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
        self.root.after(80, self._drain)            # 主线程排空数据队列
        self.root.after(100, self._drain_ui)        # 主线程执行 UI 请求
        self.root.after(2500, self._fallback_fetch)  # 兜底：2.5s 还没收到就主动查一次
        self.root.after(BREATH_STEP_MS, self._breath_tick)

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

    def _sprite_thumb(self, name: str, height: int):
        """把某张状态图做成缩略图，合成到白底上（Tk 对 RGBA 支持不好）。"""
        if not _HAS_PIL:
            return None
        path = os.path.join(ASSET_DIR, f"{name}.png")
        if not os.path.isfile(path):
            return None
        try:
            im = Image.open(path).convert("RGBA")
            ratio = height / im.size[1]
            size = (max(1, int(im.size[0] * ratio)), height)
            im = im.resize(size, Image.LANCZOS)
            alpha = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            im.putalpha(alpha)
            bg = Image.new("RGB", size, "white")
            bg.paste(im, (0, 0), im)
            return ImageTk.PhotoImage(bg)
        except Exception:
            return None

    @staticmethod
    def _center(win) -> None:
        """把窗口挪到屏幕中间（略偏上，视觉更舒服）。

        对话框默认出现在左上角。居中到父窗口不行 —— 灯本身只有一百多像素宽，
        对话框比它大得多，会被推到屏幕外。
        """
        try:
            win.update_idletasks()
            w = win.winfo_reqwidth()
            h = win.winfo_reqheight()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def px(self, n: int) -> int:
        """把 96 DPI 下的像素值换算到当前缩放。"""
        return max(1, int(round(n * getattr(self, "dpi_scale", 1.0))))

    def _load_assets(self) -> dict:
        """载入素材，只做「缩放 + alpha 二值化」，不预生成亮度帧。

        30 档 × 5 个状态 = 150 张，全预生成会让启动明显变慢，
        所以亮度帧按需生成并缓存（见 _frames_for）。
        """
        out: dict = {}
        if not _HAS_PIL:
            return out
        for fn in set(STATE_IMAGE.values()):
            path = os.path.join(ASSET_DIR, fn)
            if not os.path.isfile(path):
                continue
            try:
                im = Image.open(path).convert("RGBA")
            except OSError:
                continue
            target_h = self.px(LAMP_H)
            ratio = target_h / im.size[1]
            size = (max(1, int(im.size[0] * ratio)), target_h)
            im = im.resize(size, Image.LANCZOS)
            # alpha 二值化：否则缩放产生的半透明像素合成到洋红色键上会留一圈粉边
            alpha = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            im.putalpha(alpha)
            out[fn] = im
        return out

    def _frames_for(self, fn):
        """取某个状态的亮度帧列表；第一次用到才生成，之后缓存。"""
        if not _HAS_PIL or not self._assets:
            return None
        cache = self._frame_cache
        if fn in cache:
            return cache[fn]
        src = self._assets.get(fn)
        if src is None:
            return None

        from PIL import ImageEnhance
        size = src.size
        frames = []
        for lv in range(BREATH_LEVELS):
            factor = BREATH_MIN + (1.0 - BREATH_MIN) * (lv / (BREATH_LEVELS - 1))
            if factor >= 1.0:
                dim = src
            else:
                dim = ImageEnhance.Brightness(src).enhance(factor)
                dim.putalpha(src.getchannel("A"))    # 亮度不能改动 alpha
            bg = Image.new("RGB", size, BG_KEY)
            bg.paste(dim, (0, 0), dim)
            frames.append(bg)

        # 只留最近两个状态：5 个全留会白占十几 MB
        if len(cache) >= 2:
            cache.pop(next(iter(cache)))
        cache[fn] = frames
        return frames


    def _window_size(self) -> tuple:
        """窗口尺寸 = 素材尺寸 + 留白。"""
        margin = self.px(MARGIN)
        if self._assets:
            any_im = next(iter(self._assets.values()))
            return (any_im.size[0] + margin * 2, any_im.size[1] + margin * 2)
        # 退回圆点：正方形
        side = self.px(64) + self.px(14) * 2
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
        self._item = None

        fn = STATE_IMAGE.get(self.state)
        frames = self._frames_for(fn) if fn else None
        if frames:
            im = frames[self._level]
            photo = ImageTk.PhotoImage(im)
            self._photos[self.state] = photo      # 必须留引用，否则会被回收成空白
            self._item = c.create_image(self.w // 2, self.h // 2, image=photo)
            return

        # 没素材：退回画一个圆点
        pal = PALETTE.get(self.state, PALETTE["idle"])
        r = self.px(32)
        cx = cy = self.w / 2
        c.create_oval(cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8,
                      fill=pal["glow"], outline="", stipple="gray12")
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      fill=pal["core"], outline=pal["ring"], width=2)
        c.create_oval(cx - r * 0.55, cy - r * 0.6,
                      cx - r * 0.15, cy - r * 0.2,
                      fill="#ffffff", outline="", stipple="gray50")

    def _refresh_frame(self) -> None:
        """只换当前帧的图片，不重建 canvas item。"""
        fn = STATE_IMAGE.get(self.state)
        frames = self._frames_for(fn) if fn else None
        if not frames:
            return
        photo = ImageTk.PhotoImage(frames[self._level])
        self._photos[self.state] = photo
        if self._item is not None:
            self.canvas.itemconfigure(self._item, image=photo)

    def _breath_tick(self) -> None:
        """呼吸灯：三角波在 70%..100% 之间来回。

        只换 canvas 上的图片引用，不重建 item，开销很低。
        """
        try:
            if not self.cfg.get("breath", True):
                # 关掉呼吸 ⇒ 固定在最亮那档
                if self._level != BREATH_LEVELS - 1:
                    self._level = BREATH_LEVELS - 1
                    self._refresh_frame()
            else:
                n = BREATH_LEVELS
                # 0,1,2..n-1, n-2..,1 的往返序列
                self._breath_pos = (self._breath_pos + 1) % max(1, 2 * n - 2)
                pos = self._breath_pos
                self._level = pos if pos < n else 2 * n - 2 - pos
                fn = STATE_IMAGE.get(self.state)
                frames = self._frames_for(fn) if fn else None
                if frames and self._item is not None:
                    im = frames[self._level]
                    photo = ImageTk.PhotoImage(im)
                    self._photos[self.state] = photo
                    self.canvas.itemconfigure(self._item, image=photo)
                    # 到最亮就停一会儿，让"最亮"这个状态有存在感
                    if self._level == BREATH_LEVELS - 1:
                        delay = BREATH_HOLD_MS
                    else:
                        delay = BREATH_STEP_MS
                else:
                    delay = BREATH_STEP_MS
        except Exception:
            delay = BREATH_STEP_MS
        self.root.after(delay, self._breath_tick)

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

    def _rebuild_menu(self) -> None:
        """界面语言变了，菜单文字要跟着换 —— 直接重建一遍。"""
        try:
            self.menu.destroy()
        except Exception:
            pass
        self.menu = tk.Menu(self.root, tearoff=0)
        self._build_menu()

    def _on_menu(self, ev) -> None:
        self.menu.tk_popup(ev.x_root, ev.y_root)

    def open_settings(self) -> None:
        """设置窗口：选模式后只显示该模式的配置，测试按钮也各在各的面板里。"""
        dlg = tk.Toplevel(self.root)
        L = self.lang
        dlg.title(t("settings_title", L))
        dlg.geometry(f"{self.px(620)}x1")   # 高度稍后按内容算
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        # 模态：不关掉它就不能操作别的窗口（否则能同时开出好几个设置窗）
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: (dlg.grab_release(), dlg.destroy()))

        mode = tk.StringVar(value=self.cfg.get("mode", "local"))
        server = tk.StringVar(value=self.cfg.get("server", DEFAULT_CONFIG["server"]))
        dsh_home_var = tk.StringVar(value=self.cfg.get("dsh_home") or dsh_home())
        sound = tk.BooleanVar(value=bool(self.cfg.get("sound", True)))
        breath = tk.BooleanVar(value=bool(self.cfg.get("breath", True)))

        OK, BAD, WARN = "#2a7", "#c33", "#c80"

        # ── 语言 ──
        lang_row = tk.Frame(dlg)
        lang_row.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(lang_row, text=t("language", L)).pack(side="left")
        lang_var = tk.StringVar(value=LANG_NAMES.get(L, LANG_NAMES["zh"]))
        lang_cb = ttk.Combobox(lang_row, textvariable=lang_var, state="readonly",
                               width=12, values=[LANG_NAMES[k] for k in LANGS])
        lang_cb.pack(side="left", padx=(8, 0))

        def _on_lang(_evt=None) -> None:
            """换语言：存起来、重建菜单、把这个窗口重开一遍（不然文字换不掉）。"""
            picked = [k for k in LANGS if LANG_NAMES[k] == lang_var.get()]
            if not picked or picked[0] == self.lang:
                return
            self.lang = picked[0]
            self.cfg["lang"] = self.lang
            try:
                save_config(self.cfg)
            except OSError:
                pass
            self._rebuild_menu()
            dlg.destroy()
            self.open_settings()

        lang_cb.bind("<<ComboboxSelected>>", _on_lang)

        # ── 数据来源 ──
        tk.Label(dlg, text=t("data_source", L), font=("", 10, "bold")).pack(
            anchor="w", padx=14, pady=(12, 4))
        tk.Radiobutton(dlg, text=t("mode_local", L),
                       variable=mode, value="local",
                       command=lambda: _apply_mode()).pack(anchor="w", padx=24)
        tk.Radiobutton(dlg, text=t("mode_lan", L),
                       variable=mode, value="lan",
                       command=lambda: _apply_mode()).pack(anchor="w", padx=24)

        mode_area = tk.Frame(dlg)
        mode_area.pack(fill="x", pady=(8, 0))

        # ── 本机模式 ──
        local_box = tk.LabelFrame(mode_area, text=t("frame_local", L))
        tk.Label(local_box, text=t("dsh_dir", L)).pack(anchor="w", padx=10, pady=(8, 2))
        row = tk.Frame(local_box)
        row.pack(anchor="w", padx=12)
        home_ent = tk.Entry(row, textvariable=dsh_home_var, width=46)
        home_ent.pack(side="left")

        def _browse() -> None:
            from tkinter import filedialog
            picked = filedialog.askdirectory(
                title=t("browse_title", L),
                initialdir=dsh_home_var.get() or dsh_home())
            if picked:
                dsh_home_var.set(picked)

        tk.Button(row, text=t("browse", L), command=_browse).pack(side="left", padx=(6, 0))
        tk.Label(local_box, justify="left", wraplength=540, fg="#888", font=("", 8),
                 text=t("default_hint", L, path=default_home_display())).pack(
            anchor="w", padx=12, pady=(3, 6))

        local_test_row = tk.Frame(local_box)
        local_test_row.pack(anchor="w", padx=12, pady=(0, 10))
        # width 8 + anchor=w：预先占好位置，文字出现/消失都不会挤动按钮
        local_result = tk.Label(local_test_row, text="", font=("", 9),
                                width=8, anchor="w")
        # 注意顺序：按钮先 pack ⇒ 在左；结果后 pack ⇒ 在右

        def _test_read_local() -> None:
            try:
                set_dsh_home(dsh_home_var.get().strip())
                from dsh_lamp.state import read_state
                st = read_state(None)
                if st.get("state"):
                    local_result.configure(text=t("read_ok", L), fg=OK)
                else:
                    local_result.configure(text=t("read_fail", L), fg=BAD)
            except Exception:
                local_result.configure(text=t("read_fail", L), fg=BAD)

        tk.Button(local_test_row, text=t("test_read", L), command=_test_read_local).pack(side="left")
        local_result.pack(side="left", padx=(10, 0))

        # ── 局域网模式 ──
        lan_box = tk.LabelFrame(mode_area, text=t("frame_lan", L))
        tk.Label(lan_box, text=t("bridge_addr", L)).pack(anchor="w", padx=10, pady=(8, 2))
        ent = tk.Entry(lan_box, textvariable=server, width=56)
        ent.pack(anchor="w", padx=12)

        lan_test_row = tk.Frame(lan_box)
        lan_test_row.pack(anchor="w", padx=12, pady=(8, 2))
        conn_result = tk.Label(lan_test_row, text="", font=("", 9),
                               width=8, anchor="w")
        read_result = tk.Label(lan_test_row, text="", font=("", 9),
                               width=8, anchor="w")

        def _lan_base() -> str:
            return server.get().strip().rsplit("/", 1)[0]

        def _test_conn() -> None:
            try:
                with urllib.request.urlopen(_lan_base() + "/state", timeout=4) as r:
                    r.read(1)
                conn_result.configure(text=t("conn_ok", L), fg=OK)
            except Exception:
                conn_result.configure(text=t("conn_fail", L), fg=BAD)

        def _test_read_lan() -> None:
            try:
                with urllib.request.urlopen(_lan_base() + "/state", timeout=4) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if data.get("state"):
                    read_result.configure(text=t("read_ok", L), fg=OK)
                else:
                    read_result.configure(text=t("read_fail", L), fg=BAD)
            except Exception:
                read_result.configure(text=t("read_fail", L), fg=BAD)

        tk.Button(lan_test_row, text=t("test_conn", L), command=_test_conn).pack(side="left")
        conn_result.pack(side="left", padx=(8, 4))
        tk.Button(lan_test_row, text=t("test_read", L), command=_test_read_lan).pack(side="left")
        read_result.pack(side="left", padx=(8, 0))

        guide = tk.LabelFrame(lan_box, text=t("no_bridge", L))
        guide.pack(fill="x", padx=12, pady=(8, 10))
        tk.Label(guide, justify="left", wraplength=500, fg="#555",
                 text=t("copy_hint", L)).pack(
            anchor="w", padx=10, pady=(6, 4))

        def _copy_prompt() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(AGENT_PROMPT)
            status.configure(text=t("copied", L), fg=OK)

        tk.Button(guide, text=t("copy_btn", L), command=_copy_prompt).pack(
            anchor="w", padx=10, pady=(0, 8))


        # ── 解码器 ──
        dep_box = tk.LabelFrame(dlg, text=t("dep_frame", L))
        dep_box.pack(fill="x", padx=14, pady=(10, 0))
        dep = tk.Frame(dep_box)
        dep.pack(anchor="w", fill="x", padx=8, pady=(6, 0))
        dep_label = tk.Label(dep, justify="left", wraplength=400, font=("", 9))
        dep_label.pack(side="left", anchor="w")
        # tkinter 的 Label 不能调行距，所以拆成两行 Label 分别控 pady
        why_box = tk.Frame(dep_box)
        why_line1 = tk.Label(why_box, justify="left", fg="#999", font=("", 9),
                             text=t("why1", L))
        why_line1.pack(anchor="w", pady=(0, 5))
        why_line2 = tk.Label(why_box, justify="left", fg="#999", font=("", 9),
                             text=t("why2", L))
        why_line2.pack(anchor="w", pady=(0, 0))

        def _refresh_dep() -> None:
            from dsh_lamp.event_tail import decompressor_kind
            kind = decompressor_kind()
            installed = kind != "none"
            install_btn.pack_forget()
            dep_label.pack_forget()
            install_btn.configure(
                text=t("dep_reinstall", L) if installed else t("dep_install", L))
            install_btn.pack(side="left", anchor="w")
            dep_label.configure(fg=OK if installed else WARN,
                                text=t("dep_installed", L) if installed else t("dep_missing", L))
            dep_label.pack(side="left", anchor="w", padx=(10, 0))
            why_box.pack(anchor="w", padx=8, pady=(8, 10))

        def _one_click_install() -> None:
            install_btn.configure(state="disabled", text="安装中…")
            dep_label.configure(fg="#888", text=t("dep_installing", L))

            def work() -> None:
                ok, msg = False, ""
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "pyzstd"],
                        capture_output=True, text=True, timeout=240)
                    ok = proc.returncode == 0
                    if not ok:
                        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                        msg = tail[-1][:140] if tail else f"退出码 {proc.returncode}"
                except Exception as exc:
                    msg = exc.__class__.__name__
                self.post(lambda: done(ok, msg))

            def done(ok: bool, msg: str) -> None:
                install_btn.configure(state="normal")
                if ok:
                    _refresh_dep()
                    status.configure(text=t("dep_done", L), fg=OK)
                else:
                    dep_label.configure(fg=BAD, text=t("dep_failed", L, msg=msg))
                    status.configure(text=t("dep_manual", L), fg=BAD)

            threading.Thread(target=work, daemon=True).start()

        install_btn = tk.Button(dep, text=t("dep_install", L), command=_one_click_install)

        # ── 最下面 ──
        tk.Checkbutton(dlg, text=t("sound", L), variable=sound).pack(
            anchor="w", padx=14, pady=(12, 0))
        tk.Checkbutton(dlg, text=t("breath", L), variable=breath).pack(
            anchor="w", padx=14, pady=(2, 0))
        status = tk.Label(dlg, text="", fg=OK, anchor="w", font=("", 9))
        status.pack(anchor="w", padx=14, pady=(6, 0))

        def _fit_height() -> None:
            """把窗口高度收到刚好包住内容。

            模式切换会改变内容高度（局域网多一个「复制指令」框），
            所以每次切换都要重算。
            """
            try:
                dlg.update_idletasks()
                dlg.geometry(f"{self.px(620)}x{dlg.winfo_reqheight()}")
            except Exception:
                pass

        # ── 模式切换 ──
        def _apply_mode() -> None:
            if mode.get() == "local":
                lan_box.pack_forget()
                local_box.pack(fill="x", padx=14, pady=(6, 0))
            else:
                local_box.pack_forget()
                lan_box.pack(fill="x", padx=14, pady=(6, 0))
            dlg.after_idle(_fit_height)

        # ── 保存 ──
        def _save() -> None:
            """保存配置、重启后端，然后【异步】验证一次。

            ⚠️ 「保存成功」和「连接成功」是两件事 —— 配置写进磁盘一定成功，
            但地址可能填错、桥可能没起。两者必须分开报告，否则用户会以为
            自己已经连上了。
            """
            picked = dsh_home_var.get().strip()
            try:
                same = (os.path.normcase(os.path.normpath(picked))
                        == os.path.normcase(os.path.normpath(default_dsh_home())))
            except Exception:
                same = False
            self.cfg.update({"mode": mode.get(), "server": server.get().strip(),
                             "dsh_home": "" if same else picked,
                             "sound": bool(sound.get()),
                             "breath": bool(breath.get())})
            self.url = self.cfg["server"]
            set_dsh_home(self.cfg["dsh_home"])
            try:
                save_config(self.cfg)
            except OSError as e:
                status.configure(text=t("save_fail", L, e=e), fg=BAD)
                return

            # 配置已落盘 ⇒ 这一步是确定的
            self._n_push = 0
            self._start_backend()
            status.configure(text=t("saved_verifying", L), fg="#888")

            use_local = mode.get() == "local"
            target = self.cfg["server"]

            def verify() -> None:
                ok, detail = False, ""
                if use_local:
                    try:
                        from dsh_lamp.state import read_state
                        st = read_state(None)
                        ok = bool(st.get("state"))
                        detail = str(st.get("state", ""))
                    except Exception as exc:
                        detail = exc.__class__.__name__
                else:
                    try:
                        base = target.rsplit("/", 1)[0]
                        with urllib.request.urlopen(base + "/state", timeout=5) as r:
                            data = json.loads(r.read().decode("utf-8"))
                        ok = bool(data.get("state"))
                        detail = str(data.get("state", ""))
                    except Exception as exc:
                        detail = exc.__class__.__name__
                self.post(lambda: report(ok, detail))

            def report(ok: bool, detail: str) -> None:
                if ok:
                    status.configure(text=t("saved_ok", L, s=detail), fg=OK)
                else:
                    where = t("where_dir", L) if use_local else t("where_addr", L)
                    status.configure(
                        text=t("saved_fail", L, e=detail, where=where), fg=BAD)

            threading.Thread(target=verify, daemon=True).start()

        def _show_lamp_language() -> None:
            """灯语说明 —— 四色各是什么意思。"""
            d = tk.Toplevel(dlg)
            d.title(t("lang_title", L))
            # 宽高都交给内容决定（下面 pack 完再量一次）
            d.attributes("-topmost", True)
            d.resizable(False, False)
            # 父窗口已经 grab 了，子窗口必须自己再 grab 一次，否则点不动
            d.transient(dlg)
            d.grab_set()

            def _close() -> None:
                try:
                    d.grab_release()
                    dlg.grab_set()      # 把模态交还给设置窗口
                except Exception:
                    pass
                d.destroy()
            tk.Label(d, text=t("lang_intro", L), font=("", 11, "bold")).pack(
                anchor="w", padx=18, pady=(16, 10))

            rows = [
                ("attention", "#e74c3c", t("lang_red", L),    t("lang_red_h", L),
                 [t("lang_red_1", L), t("lang_red_2", L)]),
                ("working",   "#f1c40f", t("lang_yellow", L), t("lang_yellow_h", L),
                 [t("lang_yellow_1", L), t("lang_yellow_2", L), t("lang_yellow_3", L)]),
                ("done",      "#1abc9c", t("lang_green", L),  t("lang_green_h", L),
                 [t("lang_green_1", L), t("lang_green_2", L)]),
                ("idle",      "#9a9a94", t("lang_off", L),    t("lang_off_h", L),
                 [t("lang_off_1", L), t("lang_off_2", L)]),
            ]
            # 缩略图要保留引用，否则会被 GC 回收成空白
            d._thumbs = []
            for sprite, color, label, head, details in rows:
                row = tk.Frame(d)
                row.pack(anchor="w", fill="x", padx=18, pady=(6, 10))

                # 左侧：灯的实际样子（合成到白色背景上）
                thumb = self._sprite_thumb(sprite, self.px(68))
                if thumb is not None:
                    tk.Label(row, image=thumb, bg="white",
                             borderwidth=1, relief="solid").pack(
                        side="left", anchor="n", padx=(0, 14))
                    d._thumbs.append(thumb)

                col = tk.Frame(row)
                col.pack(side="left", anchor="n", fill="x", expand=True)
                head_row = tk.Frame(col)
                head_row.pack(anchor="w")
                tk.Label(head_row, text="●", fg=color, font=("", 13)).pack(side="left")
                tk.Label(head_row, text=f" {label} · {head}",
                         font=("", 10, "bold")).pack(side="left")
                for line in details:
                    tk.Label(col, text=f"· {line}", fg="#555", font=("", 9)).pack(anchor="w")
            tk.Button(d, text=t("close", L), command=_close).pack(
                side="bottom", pady=14)

            # 宽高都按内容收（写死任何一个都会在别的 DPI / 字体 / 语言下留空白）
            d.protocol("WM_DELETE_WINDOW", _close)
            d.update_idletasks()
            d.geometry(f"{d.winfo_reqwidth()}x{d.winfo_reqheight()}")
            self._center(d)

        bar = tk.Frame(dlg)
        bar.pack(side="bottom", fill="x", padx=14, pady=12)
        tk.Button(bar, text=t("lang_btn", L),
                  command=_show_lamp_language).pack(side="left")
        tk.Button(bar, text=t("cancel", L),
                  command=lambda: (dlg.grab_release(), dlg.destroy())).pack(side="right")
        tk.Button(bar, text=t("save", L), command=_save).pack(side="right", padx=8)

        _apply_mode()
        _refresh_dep()
        dlg.after_idle(_fit_height)
        dlg.after(20, lambda: self._center(dlg))

    def _reset_pos(self) -> None:
        self.root.geometry(f"+{self.root.winfo_screenwidth() - self.w - 40}"
                           f"+{self.root.winfo_screenheight() - self.h - 90}")
        self._save_pos()

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.var_top.get())

    # ---------- 轮询 ----------

    def post(self, fn) -> None:
        """后台线程要碰 UI 时，用这个排队，别直接调 root.after（跨线程不安全）。"""
        self._ui_q.put(fn)

    def _drain_ui(self) -> None:
        try:
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception as exc:
                    print(f"[lamp] ui task failed: {exc.__class__.__name__}: {exc}",
                          file=sys.stderr, flush=True)
        finally:
            self.root.after(100, self._drain_ui)

    def _drain(self) -> None:
        """主线程排空队列 —— 这是 tkinter + 线程唯一安全的方式。

        ⚠️ 这里的 after() 必须放在 finally 里：_apply 一旦抛异常，
        没有 finally 的话这个循环就断了，灯从此静默不动（pythonw 下还看不到）。
        """
        try:
            got = 0
            while got < 20:
                try:
                    data = self._q.get_nowait()
                except queue.Empty:
                    break
                self._last_rx = time.time()
                self._n_push += 1
                try:
                    self._apply(data)
                except Exception as exc:
                    print(f"[lamp] apply failed: {exc.__class__.__name__}: {exc}",
                          file=sys.stderr, flush=True)
                got += 1
        finally:
            self.root.after(100, self._drain)

    def poll(self) -> None:
        """退路：SSE 没连上时的一次性查询。

        ⚠️ 本机模式下不能走 HTTP —— self.url 还是局域网桥的地址，
        桥没起就会失败并把自己判成 idle；而 FileBackend 只在指纹变化时才推，
        于是灯会一直停在错误状态上。本机模式直接让后端立刻重读。
        """
        if self.cfg.get("mode") == "local":
            be = getattr(self, "_backend", None)
            if be is not None and hasattr(be, "force"):
                be.force()
            return
        self._apply(fetch_state(self.url))

    def _apply(self, data: dict) -> None:
        state = data.get("state", "idle")
        if state not in PALETTE:
            state = "idle"          # 未知状态一律按空闲处理（灯全灭）

        bits = [LABEL[state]]
        # 红灯要区分「等你允许（审批）」和「等你回答（提问）」
        if state == "question":
            bits[0] = ("等你允许" if data.get("attention_reason") == "approval"
                       else "等你回答")
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
            if state == "done" and prev is not None:
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
