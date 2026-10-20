#!/usr/bin/env python3
"""渲染 dsh-status-lamp 宣传视频的帧（纯 PIL，无需 GPU）。

只负责把画面画成 PNG 序列；编码交给别的机器（本机没有显卡，编码
放 Windows 那边用 h264_nvenc，3 秒就跑完 31 秒的片子）。

用法：
    python3 tools/make_promo.py            # 输出到 /tmp/promo/frames
    ffmpeg -framerate 30 -i frames/f%05d.png -c:v libx264 -pix_fmt yuv420p out.mp4
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path("/home/claw/PJKT/dsh-status-lamp")
OUT = Path("/home/claw/PJKT/tmp/promo/frames")
W, H, FPS = 1920, 1080, 30

SANS_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
SANS_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
SC = 2  # ttc 里 index 2 = 简体中文

BG = (16, 16, 20)
INK = (238, 238, 242)
MUTE = (140, 140, 152)
DIM = (70, 70, 80)
RED, YEL, GRN = (231, 76, 60), (241, 196, 15), (26, 188, 156)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(SANS_B if bold else SANS_R, size, index=SC)


# ---------------------------------------------------------------- 基础图层
def make_bg() -> Image.Image:
    """深色背景 + 中心微亮 + 四角压暗。"""
    base = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(base)
    # 中心柔和光晕
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    for i in range(28, 0, -1):
        r = int(W * 0.09 * i)
        a = int(4 + 26 * (1 - i / 28))
        gd.ellipse([W // 2 - r, H // 2 - r * 0.62, W // 2 + r, H // 2 + r * 0.62], fill=a)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    base = Image.composite(Image.new("RGB", (W, H), (34, 36, 46)), base, glow)
    # 四角压暗
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    for i in range(30):
        a = int(150 * (i / 30) ** 2)
        vd.rectangle([-i * 14, -i * 8, W + i * 14, H + i * 8], outline=a, width=i * 3 + 1)
    vig = vig.filter(ImageFilter.GaussianBlur(90))
    return Image.composite(Image.new("RGB", (W, H), (6, 6, 9)), base, vig)


BG_IMG = make_bg()


def ease_out(t: float) -> float:
    """0→1，先快后慢。"""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def draw_text_centered(img: Image.Image, text: str, size: int, y: int,
                       color=INK, bold: bool = False, alpha: float = 1.0,
                       rise: float = 0.0) -> None:
    """居中一行字；alpha/rise 用于统一的淡入上浮效果。"""
    if alpha <= 0.001:
        return
    f = font(size, bold)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textbbox((0, 0), text, font=f)
    x = (W - (tw[2] - tw[0])) // 2 - tw[0]
    d.text((x, y + rise), text, font=f, fill=color + (int(255 * alpha),))
    img.alpha_composite(layer)


def draw_text_left(img: Image.Image, text: str, size: int, x: int, y: int,
                   color=INK, bold: bool = False, alpha: float = 1.0,
                   rise: float = 0.0) -> None:
    if alpha <= 0.001:
        return
    f = font(size, bold)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((x, y + rise), text, font=f, fill=color + (int(255 * alpha),))
    img.alpha_composite(layer)


# ---------------------------------------------------------------- 灯泡素材
def load_sprite(name: str, scale: float) -> Image.Image:
    im = Image.open(ROOT / "assets" / "states" / f"{name}.png").convert("RGBA")
    return im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)


SPRITES = {k: load_sprite(k, 2.1) for k in ("attention", "working", "done", "idle")}


def glow_for(color, radius: int, strength: float) -> Image.Image:
    """某颜色的柔和光晕，用于点亮状态。"""
    g = Image.new("RGBA", (radius * 2, radius * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    for i in range(18, 0, -1):
        r = int(radius * i / 18)
        a = int(strength * 255 * (1 - i / 18) ** 2)
        d.ellipse([radius - r, radius - r, radius + r, radius + r], fill=color + (a,))
    return g.filter(ImageFilter.GaussianBlur(radius // 6))


def paint_lamp(img: Image.Image, state: str, cx: int, cy: int, scale: float = 1.0) -> None:
    """把某状态的灯泡画在 (cx, cy) 中心，点亮时加光晕。"""
    sp = SPRITES[state]
    if scale != 1.0:
        sp = sp.resize((int(sp.width * scale), int(sp.height * scale)), Image.LANCZOS)
    x, y = cx - sp.width // 2, cy - sp.height // 2
    color = {"attention": RED, "working": YEL, "done": GRN}.get(state)
    if color:
        g = glow_for(color, 260, 0.55)
        img.alpha_composite(g, (cx - 260, cy - 260))
    img.alpha_composite(sp, (x, y))


# ---------------------------------------------------------------- 场景 1：痛点
PAINS = [
    ("它在跑吗？", 2.1),
    ("跑完了吗？", 2.1),
    ("是不是卡在等我点什么地方？", 2.6),
    ("每次都要切回去看一眼", 2.4),
]
PAIN_FADE_IN, PAIN_FADE_OUT = 0.42, 0.36


def scene_pain(t: float) -> Image.Image:
    img = BG_IMG.convert("RGBA")
    start = 0.0
    for text, dur in PAINS:
        end = start + dur
        if start <= t < end:
            lt = t - start
            a_in = ease_out(lt / PAIN_FADE_IN)
            a_out = 1 - ease_out(max(0.0, lt - (dur - PAIN_FADE_OUT)) / PAIN_FADE_OUT)
            a = min(a_in, a_out)
            rise = (1 - ease_out(lt / PAIN_FADE_IN)) * 26
            size = 78 if len(text) <= 8 else 68
            draw_text_centered(img, text, size, H // 2 - size // 2, INK, False, a, rise)
            break
        start = end
    return img


PAIN_LEN = sum(d for _, d in PAINS)


# ---------------------------------------------------------------- 场景 2：标题
def scene_title(t: float, dur: float) -> Image.Image:
    img = BG_IMG.convert("RGBA")
    a1 = ease_out(t / 0.6)
    rise1 = (1 - ease_out(t / 0.6)) * 30
    # 标题文字（左移一点，右边给灯泡留位）
    title = "dsh-status-lamp"
    f = font(112, True)
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), title, font=f)
    tw = bbox[2] - bbox[0]
    lamp_w = 150
    total = tw + 46 + lamp_w
    x0 = (W - total) // 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    dl.text((x0, H // 2 - 96 + rise1), title, font=f, fill=INK + (int(255 * a1),))
    img.alpha_composite(layer)
    # 标题右侧的小信号灯（代替 emoji，系统没装彩色 emoji 字体）
    src = SPRITES["working"]
    small = src.resize((lamp_w, int(src.height * lamp_w / src.width)), Image.LANCZOS)
    if a1 > 0.01:
        sl = small.copy()
        alpha = sl.getchannel("A").point(lambda v: int(v * a1))
        sl.putalpha(alpha)
        # 灯泡垂直中心对齐标题那行字的中线（标题 top = H//2-96，字号 112 ⇒ 行中心 ≈ H//2-40）
        img.alpha_composite(sl, (x0 + tw + 46, H // 2 - 40 - small.height // 2 + int(rise1)))
    # 副标题
    a2 = ease_out(max(0.0, t - 0.55) / 0.7)
    rise2 = (1 - ease_out(max(0.0, t - 0.55) / 0.7)) * 18
    draw_text_centered(img, "把 DSH 的状态，放在你看得见的地方", 40, H // 2 + 74, MUTE, False, a2, rise2)
    return img


# ---------------------------------------------------------------- 场景 3：演示
WIN_W, WIN_H = 1180, 470
WIN_X, WIN_Y = 620, 300


def dsh_window(state: str, t_in: float) -> Image.Image:
    """右侧的 DSH 窗口示意。"""
    ww, wh = WIN_W, WIN_H
    win = Image.new("RGBA", (ww, wh), (0, 0, 0, 0))
    d = ImageDraw.Draw(win)
    d.rounded_rectangle([0, 0, ww - 1, wh - 1], radius=18, fill=(26, 26, 32, 255),
                        outline=(48, 48, 58, 255), width=2)
    # 标题栏
    d.rounded_rectangle([0, 0, ww - 1, 54], radius=18, fill=(33, 33, 40, 255))
    d.rectangle([0, 40, ww - 1, 54], fill=(33, 33, 40, 255))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([22 + i * 26, 21, 34 + i * 26, 33], fill=c + (255,))
    d.text((110, 18), "DSH", font=font(20), fill=(150, 150, 165, 255))

    # 正文：灰色长条模拟对话流
    y = 92
    bars = [(560, 14), (820, 14), (420, 14), (700, 14), (500, 14), (760, 14)]
    for bw, bh in bars:
        if y > wh - 110:
            break
        d.rounded_rectangle([40, y, 40 + bw, y + bh], radius=bh // 2, fill=(58, 58, 70, 255))
        y += bh + 22

    # 需要你：盖一个审批弹窗
    if state == "attention":
        dw, dh = 470, 180
        dx, dy = (ww - dw) // 2, (wh - dh) // 2
        d.rounded_rectangle([dx, dy, dx + dw, dy + dh], radius=14, fill=(46, 46, 56, 255),
                            outline=(104, 104, 122, 255), width=2)
        d.text((dx + 28, dy + 24), "允许执行这个命令？", font=font(26, True), fill=(238, 238, 242, 255))
        d.text((dx + 28, dy + 66), "rm -rf ./tmp/cache", font=font(20), fill=(150, 150, 165, 255))
        d.rounded_rectangle([dx + 28, dy + 112, dx + 148, dy + 152], radius=8, fill=(231, 76, 60, 255))
        d.text((dx + 62, dy + 120), "允许", font=font(20, True), fill=(255, 255, 255, 255))
        d.rounded_rectangle([dx + 162, dy + 112, dx + 282, dy + 152], radius=8, fill=(70, 70, 82, 255))
        d.text((dx + 196, dy + 120), "拒绝", font=font(20), fill=(220, 220, 228, 255))
    return win


DEMO = [
    ("working",   "正在干活",   "调工具 / 推理 / 生成 / 重试", YEL, 4.6),
    ("attention", "需要你介入", "审批弹窗、提问在等你",         RED, 5.0),
    ("done",      "刚完成",     "一轮结束，绿灯亮起",           GRN, 4.4),
]


def scene_demo(t: float) -> Image.Image:
    img = BG_IMG.convert("RGBA")
    start = 0.0
    for state, title, sub, color, dur in DEMO:
        end = start + dur
        if start <= t < end:
            lt = t - start
            a = ease_out(lt / 0.5) * (1 - ease_out(max(0.0, lt - (dur - 0.45)) / 0.45))
            rise = (1 - a) * 18
            # 顶部：状态标题（带一个同色小圆点）
            draw_text_centered(img, title, 76, 78 + rise, color, True, a)
            # 左：灯泡
            paint_lamp(img, state, 300, 560)
            # 右：DSH 窗口
            w = dsh_window(state, lt)
            wa = min(1.0, ease_out(lt / 0.5))
            if wa < 1.0:
                w.putalpha(w.getchannel("A").point(lambda v: int(v * wa)))
            img.alpha_composite(w, (WIN_X, WIN_Y))
            # 底部：副标题
            draw_text_centered(img, sub, 38, 930 + rise, MUTE, False, a)
            break
        start = end
    return img


DEMO_LEN = sum(d for *_, d in DEMO)


# ---------------------------------------------------------------- 场景 4：地址
def scene_url(t: float, dur: float) -> Image.Image:
    img = BG_IMG.convert("RGBA")
    a1 = ease_out(t / 0.6)
    a2 = ease_out(max(0.0, t - 0.35) / 0.7)
    # 顶部一个小灯
    sp = SPRITES["done"]
    sw = 96
    small = sp.resize((sw, int(sp.height * sw / sp.width)), Image.LANCZOS)
    sl = small.copy()
    sl.putalpha(sl.getchannel("A").point(lambda v: int(v * a1)))
    img.alpha_composite(sl, ((W - sw) // 2, H // 2 - 210))
    draw_text_centered(img, "github.com/oitsukiii/dsh-status-lamp", 62, H // 2 - 70, INK, True, a1,
                       (1 - a1) * 24)
    draw_text_centered(img, "Windows 桌面小挂件 · 配套 DSH 插件 · MIT", 34, H // 2 + 30, MUTE, False, a2,
                       (1 - a2) * 16)
    return img


URL_LEN = 4.2


# ---------------------------------------------------------------- 总时间线
SCENES = [
    ("pain", PAIN_LEN),
    ("title", 3.6),
    ("demo", DEMO_LEN),
    ("url", URL_LEN),
]
TOTAL = sum(d for _, d in SCENES)
N_FRAMES = int(round(TOTAL * FPS))


def frame_at(t: float) -> Image.Image:
    start = 0.0
    for name, dur in SCENES:
        if t < start + dur:
            lt = t - start
            if name == "pain":
                return scene_pain(lt)
            if name == "title":
                return scene_title(lt, dur)
            if name == "demo":
                return scene_demo(lt)
            return scene_url(lt, dur)
        start += dur
    return scene_url(URL_LEN, URL_LEN)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    idxs = [int(x) for x in only] if only else range(N_FRAMES)
    for i in idxs:
        t = i / FPS
        img = frame_at(t)
        # 场景之间做全局淡入淡出，避免硬切
        img.convert("RGB").save(OUT / f"f{i:05d}.png", compress_level=1)
        if i % 60 == 0:
            print(f"  {i}/{N_FRAMES}  t={t:.2f}s", flush=True)
    print(f"  done: {len(list(idxs))} frames", flush=True)


if __name__ == "__main__":
    print(f"  total {TOTAL:.1f}s  {N_FRAMES} frames  {W}x{H}@{FPS}")
    main()
