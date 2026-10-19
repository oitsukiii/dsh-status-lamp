#!/usr/bin/env python3
"""把「没亮的灯泡」从纯黑改成深灰。

灯罩是暖调的深灰 (36,31,30)，原来未亮的灯泡是纯黑，两者几乎融在一起。

⚠️ 第一版只改精确的 (0,0,0)，结果灯泡边缘有一圈「深灰但不是纯黑」的
抗锯齿像素（(8,7,7)、(2,2,2) 之类）留了下来 —— 视觉上就像一颗被啃掉边的圆，
也就是「灯泡变形」。所以这里改成按【圆形范围 + 暗度】判定：
在灯泡圆内、且足够暗的像素，全部统一成同一个灰。

灯泡位置是固定的三个圆心，半径按实测：
    中心 x=70   y=59 / 141 / 222   半径 33
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image

BULB_GREY = (58, 58, 62)    # 偏冷深灰，和暖调灯罩拉开层次
BULB_CENTERS = [(70, 59), (70, 141), (70, 222)]
BULB_RADIUS = 33
DARK_MAX = 70               # 三通道最大值低于它算「灯泡的暗部」
THRESHOLD = 128             # alpha 阈值


def recolor(im: Image.Image) -> tuple[Image.Image, int]:
    a = np.array(im.convert("RGBA"))
    h, w, _ = a.shape
    rgb = a[:, :, :3].astype(np.int16)
    dark = (rgb.max(axis=2) < DARK_MAX) & (a[:, :, 3] > THRESHOLD)

    changed = 0
    for cx, cy in BULB_CENTERS:
        y0, y1 = max(0, cy - BULB_RADIUS - 1), min(h, cy + BULB_RADIUS + 2)
        x0, x1 = max(0, cx - BULB_RADIUS - 1), min(w, cx + BULB_RADIUS + 2)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        inside = ((yy - cy) ** 2 + (xx - cx) ** 2) <= BULB_RADIUS ** 2
        target = inside & dark[y0:y1, x0:x1]
        n = int(target.sum())
        if n:
            region = a[y0:y1, x0:x1]
            region[target, 0] = BULB_GREY[0]
            region[target, 1] = BULB_GREY[1]
            region[target, 2] = BULB_GREY[2]
            changed += n
        print(f"    中心({cx},{cy})  半径{BULB_RADIUS}  改色 {n} 像素")

    return Image.fromarray(a), changed


def main() -> None:
    names = sys.argv[1:] or ["idle", "working", "done", "attention", "offline"]
    for n in names:
        path = f"assets/states/{n}.png"
        try:
            im = Image.open(path)
        except OSError:
            print(f"  跳过 {path}")
            continue
        print(f"  {n}:")
        out, changed = recolor(im)
        out.save(path)
        print(f"    合计 {changed}")


if __name__ == "__main__":
    main()
