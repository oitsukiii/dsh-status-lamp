#!/usr/bin/env python3
"""以「红灯图」为唯一基准，合成其余状态。

为什么要这样做
--------------
Qwen-Image 每生成一张就会把灯罩重新设计一遍（宽度差 45px、侧边凸起不同），
五个状态放在一起切换时会明显跳动。所以只保留一张图作为**唯一的灯罩来源**，
其余状态都是在这张图上替换灯珠得到的 —— 灯罩像素级完全一致。

    attention  红图原样（顶部红灯）
    working    顶灯→暗，中灯→黄（黄灯珠从黄图裁下来）
    done       顶灯→暗，底灯→绿（绿灯珠从绿图裁下来）
    idle       顶灯→暗
    offline    idle + 红色「离线」二字

去光晕
------
红图的顶灯有一圈红色光晕，换掉灯泡后必须清掉，否则会留一圈红边。
灯罩色是 (36,31,30)（r-g=5），所以任何 r-g>10 且 r<130 的暗红都算光晕，
在半径 220 内替换成灯罩色。半径 220 小于中灯距离 238，不会碰到中灯。

用法：python3 tools/make-states.py
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

RAW = "assets/raw"
OUT = "assets/composite"

RED_IMG = os.path.join(RAW, "02_red_try.png")
YEL_IMG = os.path.join(RAW, "03_yellow.png")
GRN_IMG = os.path.join(RAW, "03_green.png")

# 灯珠中心（用亮度/饱和度检测出来的实测值）
RED_TOP = (510, 259)
RED_MID = (510, 497)
RED_BOT = (511, 727)
YEL_MID = (511, 497)
GRN_BOT = (512, 727)
BULB_R = 99
HOUSING = (36, 31, 30)
GLOW_R = 220

TEXT = "离线"
TEXT_COLOR = (226, 48, 40)
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_PX = 20
FONT_SCALE = 5


def disc_mask(size: int, r: int) -> np.ndarray:
    y, x = np.ogrid[:size, :size]
    c = size / 2
    return ((x - c) ** 2 + (y - c) ** 2) <= r * r


def crop_disc(img: Image.Image, center, r: int) -> Image.Image:
    cx, cy = center
    patch = img.crop((cx - r, cy - r, cx + r, cy + r)).convert("RGBA")
    patch.putalpha(Image.fromarray((disc_mask(2 * r, r) * 255).astype("uint8")))
    return patch


def paste_disc(base: Image.Image, patch: Image.Image, center) -> None:
    cx, cy = center
    r = patch.size[0] // 2
    base.alpha_composite(patch, (cx - r, cy - r))


def strip_glow(img: Image.Image, center, radius: int = GLOW_R) -> tuple:
    """清掉顶灯的红色光晕。

    分两步，因为光晕分布有两种形态：

    1. **灯珠正周围**（半径 < 118）：这一圈原本是亮灯泡的边缘和它的近场光晕，
       换灯后整块都应该变成灯罩色。直接填，不判断颜色 —— 否则灯珠边缘那圈
       偏暗的红（比如 (53,29,29)）会留下来，在深色背景上正好是一道红弧。
    2. **外圈光晕**（118 < 半径 < 220）：只替换偏红的像素，避免把分隔线一起填掉。
       灯罩色是 (36,31,30)（r-g=5），所以 r-g>10 就一定是红光晕。

    半径 220 小于中灯距离 238，不会碰到别的灯珠。
    """
    arr = np.asarray(img.convert("RGB")).astype(int).copy()
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    h, w, _ = arr.shape
    yy, xx = np.ogrid[:h, :w]
    cx, cy = center
    dist2 = (xx - cx) ** 2 + (yy - cy) ** 2

    inner = dist2 <= 118 ** 2
    arr[inner] = HOUSING

    reddish = (r > g + 10) & (r > 20) & (r < 130)
    outer = (dist2 > 118 ** 2) & (dist2 <= radius ** 2)
    m = reddish & outer
    n = int(m.sum())
    arr[m] = HOUSING
    return Image.fromarray(arr.astype("uint8")), n + int(inner.sum())


def render_text_mask(text: str) -> Image.Image:
    """小尺寸渲染再 NEAREST 放大 —— 得到块状像素字，和像素风灯罩协调。"""
    font = ImageFont.truetype(FONT_PATH, FONT_PX)
    tmp = Image.new("L", (240, 48), 0)
    d = ImageDraw.Draw(tmp)
    bb = d.textbbox((0, 0), text, font=font)
    d.text((120 - (bb[2] - bb[0]) // 2 - bb[0],
            24 - (bb[3] - bb[1]) // 2 - bb[1]), text, font=font, fill=255)
    a = np.asarray(tmp)
    ys, xs = np.nonzero(a > 40)
    glyph = tmp.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    return glyph.resize((glyph.size[0] * FONT_SCALE, glyph.size[1] * FONT_SCALE),
                        Image.NEAREST)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    red = Image.open(RED_IMG).convert("RGB")
    yel = Image.open(YEL_IMG).convert("RGB")
    grn = Image.open(GRN_IMG).convert("RGB")

    dark = crop_disc(red, RED_MID, BULB_R)
    yellow = crop_disc(yel, YEL_MID, BULB_R)
    green = crop_disc(grn, GRN_BOT, BULB_R)

    def build(top_dark=False, mid=None, bot=None) -> Image.Image:
        base = red.convert("RGBA")
        if top_dark:
            flat, _ = strip_glow(base.convert("RGB"), RED_TOP)
            base = flat.convert("RGBA")
            paste_disc(base, dark, RED_TOP)
        if mid == "yellow":
            paste_disc(base, yellow, RED_MID)
        if bot == "green":
            paste_disc(base, green, RED_BOT)
        return base

    states = {
        "attention": red.convert("RGBA"),
        "working": build(top_dark=True, mid="yellow"),
        "done": build(top_dark=True, bot="green"),
        "idle": build(top_dark=True),
    }

    # 离线 = 全灭 + 「离线」
    idle = states["idle"]
    arr = np.asarray(idle.convert("RGB")).astype(int)
    ys, xs = np.nonzero(arr.sum(axis=2) < 720)
    hx0, hx1, hy1 = int(xs.min()), int(xs.max()), int(ys.max())
    glyph = render_text_mask(TEXT)
    offline = idle.copy()
    gx = (hx0 + hx1) // 2 - glyph.size[0] // 2
    gy = hy1 + 20
    offline.paste(Image.new("RGB", glyph.size, TEXT_COLOR), (gx, gy), glyph)
    states["offline"] = offline

    for name, im in states.items():
        path = os.path.join(OUT, f"{name}_raw.png")
        im.convert("RGB").save(path)
        print(f"  {name:<10} -> {path}")


if __name__ == "__main__":
    main()
