#!/usr/bin/env python3
"""把「没亮的灯泡」从纯黑改成深灰。

原来是纯黑 (0,0,0)，和灯罩边框的暗色几乎融在一起，看不出那里是个灯泡。
改成偏冷的深灰，既不像亮着，又能看出灯泡的形状。

做法：按行投影找出「大片纯黑」的团块（就是没亮的灯泡），
      用质心 + 半径把它圈出来，只在这个圆里改色。
      边框、阴影那些零散的纯黑不受影响。
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image

BULB_GREY = (58, 58, 62)   # 偏冷的深灰，跟暖色调灯罩拉开一点
MIN_BLOB_ROWS = 20         # 连续这么高的黑区才算灯泡
THRESHOLD = 128            # alpha 阈值


def recolor(im: Image.Image) -> tuple[Image.Image, int]:
    a = np.array(im.convert("RGBA"))
    h, w, _ = a.shape
    rgb = a[:, :, :3]
    black = (rgb[:, :, 0] == 0) & (rgb[:, :, 1] == 0) & (rgb[:, :, 2] == 0) & (a[:, :, 3] > THRESHOLD)

    rows = black.sum(axis=1)
    # 找连续的行区间（每行黑像素足够多才算灯泡的一部分）
    blobs = []
    run_start = None
    for y in range(h):
        heavy = rows[y] > w * 0.10
        if heavy and run_start is None:
            run_start = y
        elif not heavy and run_start is not None:
            if y - run_start >= MIN_BLOB_ROWS:
                blobs.append((run_start, y - 1))
            run_start = None
    if run_start is not None and h - run_start >= MIN_BLOB_ROWS:
        blobs.append((run_start, h - 1))

    changed = 0
    for y0, y1 in blobs:
        strip = black[y0:y1 + 1]
        ys, xs = np.nonzero(strip)
        if len(ys) == 0:
            continue
        cy = y0 + ys.mean()
        cx = xs.mean()
        radius = (len(ys) / np.pi) ** 0.5 * 1.06   # 按面积估半径，留一点余量

        yy, xx = np.mgrid[0:h, 0:w]
        inside = ((yy - cy) ** 2 + (xx - cx) ** 2) <= radius ** 2
        target = black & inside
        n = int(target.sum())
        if n:
            a[target, 0] = BULB_GREY[0]
            a[target, 1] = BULB_GREY[1]
            a[target, 2] = BULB_GREY[2]
            changed += n
        print(f"    团块 y={y0}..{y1}  中心=({cx:.0f},{cy:.0f})  半径={radius:.0f}  改色 {n} 像素")

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
        print(f"    合计 {changed} 像素 → {BULB_GREY}")


if __name__ == "__main__":
    main()
