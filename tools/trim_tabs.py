#!/usr/bin/env python3
"""去掉灯罩左右两侧的小凸起。

原图（ComfyUI 生成的基准图）灯罩侧面有 6 个小凸起（每侧 3 个，伸出约 7px）。
这些凸起在整张图缩放后变成一圈毛边，视觉上很脏。

做法：
  1. 逐行求出不透明像素的左右边界
  2. 用 ±20 行的【局部中位数】当作「主体轮廓」
  3. 某行的边界比主体轮廓突出超过 2px ⇒ 把多出来的像素抹成透明

窗口取 ±20 行是因为凸起只有 6~10 行高，中位数不会被它带偏；
主体本身的圆角变化是渐进的，中位数能跟得上，不会被误裁。
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image

THRESHOLD = 128     # alpha 大于它算不透明
WINDOW = 20         # 局部中位数的半窗口（行）
TOLERANCE = 0       # 不留余量：主体的直边行边界正好等于中位数


def trim_tabs(im: Image.Image) -> tuple[Image.Image, int]:
    """按【全局】主体边界裁剪，抹掉左右凸起。

    原实现用 ±20 行的局部中位数，但凸起本身有 23 行高，窗口里一半都是凸起，
    中位数被带偏 ⇒ 裁不干净。改成：
      · 取所有行的左右边界【全局中位数】当作主体轮廓
      · 圆角行的边界在主体轮廓内 ⇒ 不受影响
      · 凸起行的边界超出 ⇒ 被裁掉
    """
    a = np.array(im.convert("RGBA"))
    alpha = a[:, :, 3]
    h, w = alpha.shape

    left = np.full(h, -1, dtype=int)
    right = np.full(h, -1, dtype=int)
    for y in range(h):
        nz = np.nonzero(alpha[y] > THRESHOLD)[0]
        if len(nz):
            left[y], right[y] = int(nz[0]), int(nz[-1])
    valid = left >= 0

    body_left = int(np.median(left[valid]))
    body_right = int(np.median(right[valid]))

    removed = 0
    for y in range(h):
        if not valid[y]:
            continue
        if left[y] < body_left - TOLERANCE:
            a[y, : body_left] = 0
            removed += body_left - left[y]
        if right[y] > body_right + TOLERANCE:
            a[y, body_right + 1:] = 0
            removed += right[y] - body_right

    return Image.fromarray(a), removed


def main() -> None:
    names = sys.argv[1:] or ["idle", "working", "done", "attention", "offline"]
    for n in names:
        path = f"assets/states/{n}.png"
        try:
            im = Image.open(path)
        except OSError:
            print(f"  跳过 {path}（不存在）")
            continue
        out, removed = trim_tabs(im)
        out.save(path)
        print(f"  {n:<10} 裁掉 {removed} 个像素 → {path}")


if __name__ == "__main__":
    main()
