#!/usr/bin/env python3
"""DSH 状态灯 —— 一个浮在所有窗口之上的小圆灯，显示 DSH 正在干什么。

纯标准库（tkinter + urllib），Windows 上双击即可运行，不需要 pip install。

它从「状态桥」拉取状态（默认 http://127.0.0.1:3099/events），
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
    python  client/lamp.py --url http://192.168.1.50:3099/events
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
def _resource_root() -> str:
    """资源根目录。

    PyInstaller 打成的 exe 会把 --add-data 的内容解到 sys._MEIPASS，
    源码运行时就是脚本所在目录。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _find_asset_dir(*parts) -> str:
    """素材可能在 <脚本目录>/assets 或 <上一级>/assets（仓库结构 client/ 与根并列）。"""
    here = _resource_root()
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


def play_alert_sound() -> None:
    """需要你注意的时候响一下 —— 任务完成，或者需要你介入。

    异步播放，不阻塞 UI。
    """
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

from dsh_lamp.backends import (FileBackend, SseBackend, make_backend)  # noqa: E402
from dsh_lamp.i18n import (LANGS, LANG_NAMES, normalize,  # noqa: E402
                           t)

def _ensure_std_streams() -> None:
    """把 sys.stdout / sys.stderr 接上，哪怕是 None。

    ⚠️ 用 PyInstaller 的 console=False 打包成 exe、或者用 pythonw 运行时，
    这两个流都是 None。此时任何一次 print(...) 都会抛 AttributeError，
    而这段代码正好在异常处理里 —— 于是「处理异常」本身把进程打死了。
    表现就是：窗口版 exe 双击后什么都没有，控制台版却一切正常。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


_ensure_std_streams()

from dsh_lamp.paths import (config_file, default_dsh_home,  # noqa: E402
                            dsh_home, set_dsh_home)

DEFAULT_URL = os.environ.get("DSH_LAMP_URL", "http://127.0.0.1:3099/events")

# 项目地址 —— 发布到 GitHub 后把这里换成真实地址，复制出去的提示词就会跟着变
REPO_URL = "https://github.com/<你的用户名>/dsh-status-lamp"

# 用户在 DSH 插件页要粘贴的东西
PROJECT_ADDR = "github:oitsukiii/dsh-status-lamp#path:/packages/dsh-lamp"

# 给 agent 的提示词 —— 让它在 DSH 那台机器上把插件装好并回报地址
AGENT_PROMPT = """请帮我在这台机器上装好「DSH 状态灯」的桥接插件，并把配置地址告诉我：

1. git clone --depth 1 https://github.com/oitsukiii/dsh-status-lamp /tmp/dsl
2. 把 /tmp/dsl/packages/dsh-lamp 拷到 <profile>/vendor/dsh-lamp-bridge
   （profile 一般在 $DSH_HOME/profiles/web/）
3. 改 <profile>/package.json：
   - dsh.profile.bundles 里加上 "dsh-lamp-bridge"
   - dependencies 里加 "dsh-lamp-bridge": "file:./vendor/dsh-lamp-bridge"
4. 在 <profile> 里跑 pnpm install
⚠️ 两个坑：插件的 package.json 必须声明 dsh.bundle.patch，
   且它的 cordis.patch.yml 必须用 - insert: 包裹条目（裸 - id: 只会「修改已有」，
   新增无效）。缺任何一条 DSH 都会静默跳过，日志里连插件名都不出现。

装好后需要重启 DSH 才生效 —— 这会影响当前会话，提醒我重启，不要自己动手。

重启后确认这行能返回 JSON：
    curl -s http://127.0.0.1:3099/state

最后告诉我插件配置地址：
    http://<这台机器的局域网 IP>:3099/events

注意：
1. 不要把 3099 暴露到公网，这个端口没有认证。
2. git clone 时配置一个计时器，超时的话寻找国内镜像，或询问我是否有代理。
"""

# 「复制给 agent」的内容：一段可以直接发给 DSH 的提示词


DEFAULT_CONFIG = {
    "lang": "zh",                          # zh | en
    "mode": "local",                       # local | lan
    "server": "http://127.0.0.1:3099/events",
    "dsh_home": "",                        # 留空 = 默认 ~/.dsh
    "sound": True,
    "size": "medium",
    "breath": True,
}


def default_home_display(mask: str = "<用户名>") -> str:
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
    masked = parent + sep + mask
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

LAMP_H = 160       # 默认高度（像素）；宽度按素材比例算
# 三档尺寸（高度，96 DPI 下的像素值；实际会再乘 DPI 倍率）
LAMP_SIZES = {"small": 110, "medium": 160, "large": 220}

# 呼吸灯：亮度在 BREATH_MIN..1.0 之间来回，走三角波
BREATH_LEVELS = 30     # 亮度档数（越多越平滑；亮度帧惰性生成，不占启动时间）
BREATH_MIN = 0.60      # 最暗 = 60%
BREATH_STEP_MS = 45    # 每档停留毫秒（30 档 × 45ms ≈ 一个方向 1.35 秒）
BREATH_HOLD_MS = 1000  # 到最亮后停留多久再往回落

# 每张状态图里【点亮的那颗灯泡】的圆心（素材原生 140x320 坐标）；
# None 表示这张图没有亮着的灯。呼吸只调这颗灯泡，不动灯罩。
LIT_BULB = {
    "attention.png": (70, 59),     # 顶灯
    "working.png":   (70, 141),    # 中灯
    "done.png":      (70, 222),    # 底灯
    "idle.png":      None,
    "offline.png":   None,
}
BULB_R = 33
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

# 状态标签在 i18n 里，用 t(f"state_{state}", lang) 取（之前在模块级写死中文，# 英文模式下会漏出来）


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
        self.lamp_h = LAMP_SIZES.get(cfg.get("size"), LAMP_H)
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
        # 现在只有「连桥」一种数据源：本机直读文件那套已经去掉。
        self.cfg["mode"] = "lan"
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
            target_h = self.px(self.lamp_h)
            ratio = target_h / im.size[1]
            size = (max(1, int(im.size[0] * ratio)), target_h)
            im = im.resize(size, Image.LANCZOS)
            # alpha 二值化：否则缩放产生的半透明像素合成到洋红色键上会留一圈粉边
            alpha = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            im.putalpha(alpha)
            out[fn] = im
        return out

    def apply_size(self, key: str) -> None:
        """换尺寸：重载素材、清缓存、重设窗口。"""
        height = LAMP_SIZES.get(key)
        if height is None or height == self.lamp_h:
            return
        self.lamp_h = height
        self._assets = self._load_assets()
        self._frame_cache.clear()
        self._photos.clear()
        self._item = None
        self.w, self.h = self._window_size()
        self.canvas.configure(width=self.w, height=self.h)
        self.root.geometry(f"{self.w}x{self.h}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._draw()

    def _frames_for(self, fn):
        """取某个状态的亮度帧列表；第一次用到才生成，之后缓存。

        ⚠️ 只调【点亮那颗灯泡】的亮度，绝不动灯罩。
        整张图一起调暗的话，深灰外壳会跟着闪，看起来像整个挂件在抖。

        ⚠️ 只用 Pillow，不引 numpy —— numpy 不是这个项目的依赖，
        在没装它的机器上会直接 ModuleNotFoundError，整个程序起不来。
        """
        if not _HAS_PIL or not self._assets:
            return None
        cache = self._frame_cache
        if fn in cache:
            return cache[fn]
        src = self._assets.get(fn)
        if src is None:
            return None

        from PIL import ImageDraw, ImageEnhance

        size = src.size
        centre = LIT_BULB.get(fn)
        mask_img = None
        if centre is not None:
            sc = size[1] / 320.0          # 素材原生 320 高，换算到当前显示高度
            cx, cy, r = centre[0] * sc, centre[1] * sc, BULB_R * sc
            mask_img = Image.new("L", size, 0)
            ImageDraw.Draw(mask_img).ellipse(
                [cx - r, cy - r, cx + r, cy + r], fill=255)

        frames = []
        for lv in range(BREATH_LEVELS):
            factor = BREATH_MIN + (1.0 - BREATH_MIN) * (lv / (BREATH_LEVELS - 1))
            if factor >= 1.0 or mask_img is None:
                dim = src
            else:
                # 先整图调暗，再把「灯泡以外」的部分贴回原样 —— 等价于只调灯泡
                darker = ImageEnhance.Brightness(src).enhance(factor)
                dim = src.copy()
                dim.paste(darker, (0, 0), mask_img)
                dim.putalpha(src.getchannel("A"))
            bg = Image.new("RGB", size, BG_KEY)
            bg.paste(dim, (0, 0), dim)
            frames.append(bg)

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
        """呼吸灯：三角波在 BREATH_MIN..100% 之间来回，最亮处停一下。

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
        L = self.lang
        self.menu = tk.Menu(self.root, tearoff=0)

        self.menu.add_command(label=t("menu_refresh", L), command=self.poll)
        self.menu.add_separator()

        self.menu.add_command(label=t("menu_reset", L), command=self._reset_pos)
        self.menu.add_separator()

        # 置顶 + 三个配置项算一组
        self.var_top = tk.BooleanVar(value=True)
        self.menu.add_checkbutton(label=t("menu_topmost", L), variable=self.var_top,
                                  command=self._toggle_topmost)
        self.var_sound = tk.BooleanVar(value=bool(self.cfg.get("sound", True)))
        self.menu.add_checkbutton(label=t("sound", L), variable=self.var_sound,
                                  command=self._toggle_sound)
        self.var_breath = tk.BooleanVar(value=bool(self.cfg.get("breath", True)))
        self.menu.add_checkbutton(label=t("breath", L), variable=self.var_breath,
                                  command=self._toggle_breath)

        size_menu = tk.Menu(self.menu, tearoff=0)
        self.var_size = tk.StringVar(value=self.cfg.get("size", "medium"))
        for key, label_key in (("small", "size_small"),
                               ("medium", "size_medium"),
                               ("large", "size_large")):
            size_menu.add_radiobutton(
                label=t(label_key, L), variable=self.var_size, value=key,
                command=lambda k=key: self._change_size(k))
        self.menu.add_cascade(label=t("size", L), menu=size_menu)
        self.menu.add_separator()

        self.menu.add_command(label=t("menu_settings", L), command=self.open_settings)
        self.menu.add_command(label=t("menu_quit", L), command=self.root.destroy)

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
        """设置窗口：只需要一个桥地址，其余都是开关。"""
        existing = getattr(self, "_settings_dlg", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
            self._settings_dlg = None

        dlg = tk.Toplevel(self.root)
        # 先藏起来再搭界面：Toplevel 一创建就会以默认位置出现在屏幕上，
        # 等我们算完尺寸、居中之后再显示，就不会先闪在角落再跳到中间。
        dlg.withdraw()
        L = self.lang
        dlg.title(t("settings_title", L))
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        # ⚠️ 不能用 grab_set()：它会把整个应用的输入都截给这个对话框，
        # 灯就既拖不动、也点不出右键菜单了。
        self._settings_dlg = dlg
        dlg.transient(self.root)

        def _close_settings() -> None:
            self._settings_dlg = None
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _close_settings)

        OK, BAD = "#2a7", "#c33"

        server = tk.StringVar(value=self.cfg.get("server", DEFAULT_CONFIG["server"]))

        # ── 语言 ──
        lang_row = tk.Frame(dlg)
        lang_row.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(lang_row, text=t("language", L)).pack(side="left")
        lang_var = tk.StringVar(value=LANG_NAMES.get(L, LANG_NAMES["zh"]))
        lang_cb = ttk.Combobox(lang_row, textvariable=lang_var, state="readonly",
                               width=12, values=[LANG_NAMES[k] for k in LANGS])
        lang_cb.pack(side="left", padx=(8, 0))

        def _on_lang(_evt=None) -> None:
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
            _close_settings()
            self.open_settings()

        lang_cb.bind("<<ComboboxSelected>>", _on_lang)

        # ── 桥接插件地址 ──
        box = tk.LabelFrame(dlg, text=t("frame_bridge", L))
        box.pack(fill="x", padx=14, pady=(12, 0))

        # 标题（粗体，框内第一行）
        tk.Label(box, text=t("bridge_title", L), font=("", 11, "bold")).pack(
            anchor="w", padx=12, pady=(8, 4))

        # 地址栏
        # fill="x" 让它撑满父容器的宽度 —— 父容器的宽度由下面那些
        # wraplength 文字决定，所以输入框会和说明文字一样宽。
        ent = tk.Entry(box, textvariable=server, width=52)
        ent.pack(fill="x", padx=12)

        # 测试连接
        test_row = tk.Frame(box)
        test_row.pack(anchor="w", padx=12, pady=(6, 10))
        conn_result = tk.Label(test_row, text="", font=("", 9), width=10, anchor="w")

        def _base() -> str:
            return server.get().strip().rsplit("/", 1)[0]

        def _test_conn() -> None:
            try:
                with urllib.request.urlopen(_base() + "/state", timeout=4) as r:
                    r.read(1)
                conn_result.configure(text=t("conn_ok", L), fg=OK)
            except Exception:
                conn_result.configure(text=t("conn_fail", L), fg=BAD)

        tk.Button(test_row, text=t("test_conn", L), command=_test_conn).pack(side="left")
        conn_result.pack(side="left", padx=(10, 0))

        tk.Frame(box, height=1, bg="#d8d8d3").pack(fill="x", padx=12)

        # 这是什么？
        tk.Label(box, text=t("bridge_intro_title", L), font=("", 10, "bold")).pack(
            anchor="w", padx=12, pady=(10, 2))
        tk.Label(box, justify="left", wraplength=620, fg="#555", font=("", 9),
                 text=t("bridge_intro", L)).pack(anchor="w", padx=12)

        # 连本机 / 连其他设备 —— 标签和 URL 各自成行
        tk.Label(box, justify="left", fg="#555", font=("", 9),
                 text=t("bridge_local_label", L)).pack(anchor="w", padx=12, pady=(6, 0))
        tk.Label(box, text=t("bridge_local_url", L), font=("Consolas", 9)).pack(
            anchor="w", padx=26)
        tk.Label(box, justify="left", fg="#555", font=("", 9),
                 text=t("bridge_remote_label", L)).pack(anchor="w", padx=12, pady=(4, 0))
        tk.Label(box, text=t("bridge_remote_url", L), font=("Consolas", 9)).pack(
            anchor="w", padx=26, pady=(0, 10))

        tk.Frame(box, height=1, bg="#d8d8d3").pack(fill="x", padx=12)

        # 如何获得？
        tk.Label(box, text=t("bridge_auto_title", L), font=("", 10, "bold")).pack(
            anchor="w", padx=12, pady=(10, 2))

        tk.Label(box, justify="left", wraplength=620, fg="#555", font=("", 9),
                 text=t("bridge_auto", L)).pack(anchor="w", padx=12, pady=(0, 4))

        def _copy_prompt() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(AGENT_PROMPT)
            status.configure(text=t("copied_prompt", L), fg=OK)

        tk.Button(box, text=t("bridge_auto_btn", L), command=_copy_prompt).pack(
            anchor="w", padx=12, pady=(0, 10))

        # 手动配置
        tk.Label(box, justify="left", wraplength=620, fg="#555", font=("", 9),
                 text=t("bridge_manual", L)).pack(anchor="w", padx=12, pady=(0, 0))
        tk.Label(box, text=t("bridge_manual_url", L), font=("Consolas", 9)).pack(
            anchor="w", padx=26, pady=(2, 6))

        def _copy_addr() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(PROJECT_ADDR)
            status.configure(text=t("copied_addr", L), fg=OK)

        tk.Button(box, text=t("bridge_manual_btn", L), command=_copy_addr).pack(
            anchor="w", padx=12, pady=(0, 10))


        status = tk.Label(dlg, text="", fg=OK, anchor="w", font=("", 9))
        status.pack(anchor="w", padx=14, pady=(6, 0))

        # ── 灯语说明 ──
        def _show_lamp_language() -> None:
            d = tk.Toplevel(dlg)
            d.withdraw()
            d.title(t("lang_title", L))
            d.attributes("-topmost", True)
            d.resizable(False, False)
            d.transient(dlg)
            prev = getattr(self, "_lang_dlg", None)
            if prev is not None:
                try:
                    if prev.winfo_exists():
                        prev.lift()
                        prev.focus_force()
                        d.destroy()
                        return
                except Exception:
                    pass
            self._lang_dlg = d

            def _close() -> None:
                self._lang_dlg = None
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
            d._thumbs = []
            for sprite, color, label, head, details in rows:
                row = tk.Frame(d)
                row.pack(anchor="w", fill="x", padx=18, pady=(6, 10))
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

            tk.Button(d, text=t("close", L), command=_close).pack(side="bottom", pady=14)
            d.protocol("WM_DELETE_WINDOW", _close)
            d.update_idletasks()
            d.geometry(f"{d.winfo_reqwidth()}x{d.winfo_reqheight()}")
            self._center(d)
            d.deiconify()

        # ── 保存 ──
        def _save() -> None:
            picked_url = server.get().strip()
            if not picked_url:
                status.configure(text=t("need_addr", L), fg=BAD)
                return
            # 提示音 / 呼吸灯 / 大小 都在右键菜单里改，这里只管地址
            self.cfg.update({"server": picked_url, "mode": "lan"})
            self.url = picked_url
            try:
                save_config(self.cfg)
            except OSError as e:
                status.configure(text=t("save_fail", L, e=e), fg=BAD)
                return

            self._n_push = 0
            self._start_backend()
            status.configure(text=t("saved_verifying", L), fg="#888")

            target = picked_url

            def verify() -> None:
                ok, detail = False, ""
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
                    status.configure(text=t("saved_fail", L, e=detail,
                                            where=t("where_addr", L)), fg=BAD)

            threading.Thread(target=verify, daemon=True).start()

        bar = tk.Frame(dlg)
        bar.pack(side="bottom", fill="x", padx=14, pady=12)
        tk.Button(bar, text=t("lang_btn", L),
                  command=_show_lamp_language).pack(side="left")
        tk.Button(bar, text=t("cancel", L), command=_close_settings).pack(side="right")
        tk.Button(bar, text=t("save", L), command=_save).pack(side="right", padx=8)

        def _fit_and_show() -> None:
            try:
                dlg.update_idletasks()
                dlg.geometry(f"{dlg.winfo_reqwidth()}x{dlg.winfo_reqheight()}")
                self._center(dlg)
            except Exception:
                pass
            dlg.deiconify()

        dlg.after_idle(_fit_and_show)


    def _reset_pos(self) -> None:
        self.root.geometry(f"+{self.root.winfo_screenwidth() - self.w - 40}"
                           f"+{self.root.winfo_screenheight() - self.h - 90}")
        self._save_pos()

    def _save_cfg_quiet(self) -> None:
        try:
            save_config(self.cfg)
        except OSError:
            pass

    def _toggle_sound(self) -> None:
        self.cfg["sound"] = bool(self.var_sound.get())
        self._save_cfg_quiet()

    def _toggle_breath(self) -> None:
        self.cfg["breath"] = bool(self.var_breath.get())
        self._save_cfg_quiet()

    def _change_size(self, key: str) -> None:
        self.cfg["size"] = key
        self._save_cfg_quiet()
        self.apply_size(key)

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

        bits = [t(f"state_{state}", self.lang)]
        # 红灯要区分「等你允许（审批）」和「等你回答（提问）」
        if state == "question":
            bits[0] = t("attention_approval" if data.get("attention_reason") == "approval"
                        else "attention_question", self.lang)
        # 调工具时把工具名也显示出来，这是信息量最大的一项
        if state == "tool" and data.get("tool"):
            bits[-1] = t(f"state_{state}", self.lang) + " · " + str(data["tool"])
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
            # 进入「完成」或「需要你介入」时响一下。
            # prev is not None 是为了跳过第一帧 —— 否则启动时会把当前状态当成一次切换。
            # 红灯两个子原因（审批/提问）都算 question，state 没变就不会重复响。
            if state in ("done", "question") and prev is not None:
                if self.cfg.get("sound", True):
                    play_alert_sound()

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
