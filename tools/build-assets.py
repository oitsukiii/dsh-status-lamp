#!/usr/bin/env python3
"""把 ComfyUI 生成的原始图整理成客户端用的透明 PNG。

流程：裁内容框 → 抠白底（只抠与边缘连通的部分）→ 等比缩放 → 统一画布。

用法：
    python3 tools/build-assets.py            # 读 assets/raw，写 assets/states
"""

from __future__ import annotations

import os
from collections import deque

import numpy as np
from PIL import Image

RAW = "assets/raw"
OUT = "assets/states"

# (源文件, 目标状态名, 说明)
MAP = [
    ("01_off.png", "idle", "全灭"),
    ("03_yellow.png", "working", "黄灯"),
    ("03_green.png", "done", "绿灯"),
    ("02_red_try.png", "attention", "红灯"),
    ("03_offline.png", "offline", "红叉"),
]

TARGET_H = 320


def content_bbox(im: Image.Image):
    a = np.asarray(im.convert("RGB")).astype(int)
    mask = a.sum(axis=2) < 720          # 比"接近白"更暗 = 有内容
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def make_transparent(im: Image.Image) -> Image.Image:
    """从四条边向内洪水填充，把与边缘连通的白色变透明。"""
    rgb = np.asarray(im.convert("RGB")).astype(int)
    h, w, _ = rgb.shape
    whiteish = (rgb[:, :, 0] > 238) & (rgb[:, :, 1] > 238) & (rgb[:, :, 2] > 232)
    seen = np.zeros((h, w), dtype=bool)
    q: deque = deque()

    def push(y: int, x: int) -> None:
        if 0 <= y < h and 0 <= x < w and whiteish[y, x] and not seen[y, x]:
            seen[y, x] = True
            q.append((y, x))

    for x in range(w):
        push(0, x)
        push(h - 1, x)
    for y in range(h):
        push(y, 0)
        push(y, w - 1)
    while q:
        y, x = q.popleft()
        push(y + 1, x)
        push(y - 1, x)
        push(y, x + 1)
        push(y, x - 1)

    out = im.convert("RGBA").copy()
    out.putalpha(Image.fromarray(np.where(seen, 0, 255).astype("uint8")))
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)

    boxes = {}
    for fn, name, _ in MAP:
        boxes[name] = content_bbox(Image.open(os.path.join(RAW, fn)))

    max_ratio = max((b[2] - b[0]) / (b[3] - b[1]) for b in boxes.values())
    target_w = int(round(TARGET_H * max_ratio))
    print(f"  统一画布 {target_w}x{TARGET_H}")

    for fn, name, desc in MAP:
        im = Image.open(os.path.join(RAW, fn)).convert("RGB")
        cropped = im.crop(content_bbox(im))
        scale = TARGET_H / cropped.size[1]
        resized = cropped.resize(
            (max(1, int(cropped.size[0] * scale)), TARGET_H), Image.LANCZOS
        )
        transparent = make_transparent(resized)
        canvas = Image.new("RGBA", (target_w, TARGET_H), (0, 0, 0, 0))
        canvas.paste(transparent, ((target_w - transparent.size[0]) // 2, 0),
                     transparent)
        path = os.path.join(OUT, f"{name}.png")
        canvas.save(path)
        print(f"  {desc:<6} -> {path}")


if __name__ == "__main__":
    main()
