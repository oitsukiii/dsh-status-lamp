#!/usr/bin/env python3
"""从 packages/dsh-lamp/assets/plugin-icon.svg 生成 client/lamp.ico。

为什么不用现成的光栅化器：本机没有 rsvg-convert / inkscape / cairosvg，
而这个图标只由一个圆角矩形和三个圆组成 —— 直接解析 SVG 的属性比装一堆
依赖省事，而且**保证和 DSH 插件清单里显示的是同一个文件**，不会各改各的。

SVG 改了之后重跑本脚本即可：
    python3 tools/make_icon.py
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "packages" / "dsh-lamp" / "assets" / "plugin-icon.svg"
OUT = ROOT / "client" / "lamp.ico"

# ICO 里塞这些尺寸：Windows 会按场合挑（任务栏 32、Alt+Tab 48、大图标 256…）
SIZES = [16, 32, 48, 64, 128, 256]
# 超采样倍数：先按 1024 画再缩，边缘才不毛糙
SUPERSAMPLE = 16


def _hex_rgba(value: str) -> tuple[int, int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"不支持的填充色: {value}")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)


def _num(el: ET.Element, key: str) -> float:
    raw = el.get(key)
    if raw is None:
        raise ValueError(f"<{el.tag}> 缺少属性 {key}")
    return float(raw)


def render(size: int, view: float) -> Image.Image:
    """按 viewBox 比例把图标画成 size×size 的 RGBA 图。"""
    scale = size / view * SUPERSAMPLE
    canvas = int(round(size * SUPERSAMPLE))
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    tree = ET.parse(SVG)
    root = tree.getroot()
    vb = root.get("viewBox", "0 0 64 64").split()
    vx, vy = float(vb[0]), float(vb[1])

    def sx(x: float) -> float:
        return (x - vx) * scale

    for el in root:
        tag = el.tag.split("}")[-1]
        fill = _hex_rgba(el.get("fill", "#000000"))
        if tag == "rect":
            x, y = sx(_num(el, "x")), sx(_num(el, "y"))
            w, h = _num(el, "width") * scale, _num(el, "height") * scale
            r = _num(el, "rx") * scale if el.get("rx") else 0.0
            d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill)
        elif tag == "circle":
            cx, cy = sx(_num(el, "cx")), sx(_num(el, "cy"))
            r = _num(el, "r") * scale
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        # 其他元素（path/polygon…）本图标没有；若将来加了，这里会静默跳过，
        # 所以留一句提示而不是假装支持。
        elif tag not in ("title", "desc", "style"):
            print(f"  ⚠️ 跳过不支持的 <{tag}>（本脚本只画 rect/circle）")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    tree = ET.parse(SVG)
    vb = tree.getroot().get("viewBox", "0 0 64 64").split()
    view = max(float(vb[2]), float(vb[3]))

    frames = [render(s, view) for s in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"  ✅ {OUT.relative_to(ROOT)}")
    print(f"     来源 {SVG.relative_to(ROOT)}  viewBox={tree.getroot().get('viewBox')}")
    print(f"     尺寸 {SIZES}")


if __name__ == "__main__":
    main()
