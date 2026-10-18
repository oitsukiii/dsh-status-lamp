#!/usr/bin/env python3
"""验证状态桥返回的状态是否合理。

用法：
    python3 tools/verify-state.py                      # 查本机
    python3 tools/verify-state.py --url http://HOST:3099/state

它会连续采样若干秒，检查：
  * 能否连通
  * JSON 字段齐全
  * state 在允许的取值里
  * age_s 是否在合理范围（状态是否新鲜）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

ALLOWED = {"working", "retrying", "idle", "offline"}


def ascii_safe(value: object) -> str:
    """Windows 控制台是 GBK，任何非 ASCII 输出都会炸 —— 统一过滤。"""
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def fetch(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:3099/state")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    failures: list[str] = []
    print(f"  url = {ascii_safe(args.url)}")

    for i in range(args.samples):
        try:
            data = fetch(args.url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  sample {i + 1}: UNREACHABLE ({ascii_safe(exc.__class__.__name__)})")
            failures.append("unreachable")
            time.sleep(args.interval)
            continue

        state = data.get("state")
        ok = state in ALLOWED
        if not ok:
            failures.append(f"bad-state:{state}")

        age = data.get("age_s")
        fresh = isinstance(age, (int, float)) and age <= 25
        if not fresh:
            failures.append(f"stale:{age}")

        print(
            "  sample {n}: {state:<9} turn={turn} ctx={ctx} age={age}s {mark}".format(
                n=i + 1,
                state=ascii_safe(state),
                turn=ascii_safe(data.get("turn")),
                ctx=ascii_safe(data.get("context_ratio")),
                age=ascii_safe(age),
                mark="" if (ok and fresh) else "<-- suspicious",
            )
        )
        time.sleep(args.interval)

    print()
    if failures:
        print("  RESULT: FAILED -> " + ascii_safe(sorted(set(failures))))
        return 1
    print("  RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
