"""跨平台路径解析。"""

from __future__ import annotations

import os
import sys


# 运行时可覆盖（用户在设置里指定了非默认目录时）
_override: "str | None" = None


def set_dsh_home(path: "str | None") -> None:
    """由客户端在启动/改配置时调用。传 None 或空字符串恢复默认。"""
    global _override
    _override = path or None


def default_dsh_home() -> str:
    """未经用户覆盖的默认目录（设置窗口用它做「留空 = 默认」的判断）。"""
    return os.environ.get("DSH_HOME") or os.path.join(
        os.path.expanduser("~"), ".dsh")


def dsh_home() -> str:
    """DSH 的数据目录。优先级：运行时覆盖 > DSH_HOME > 默认。

    状态快照和事件日志都在这个目录下面，所以只需要用户选一次。
    """
    return _override or default_dsh_home()


def config_dir() -> str:
    """本项目的配置目录。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dsh-status-lamp")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"),
                            "Library", "Application Support", "dsh-status-lamp")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "dsh-status-lamp")


def config_file() -> str:
    return os.path.join(config_dir(), "config.json")


def projcache_dir() -> str:
    return os.path.join(dsh_home(), "storages", "session_projcache", "sessions")


def sessions_dir() -> str:
    return os.path.join(dsh_home(), "sessions")
