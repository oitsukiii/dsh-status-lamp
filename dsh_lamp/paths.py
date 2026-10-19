"""跨平台路径解析。"""

from __future__ import annotations

import os
import sys


def dsh_home() -> str:
    """DSH 的数据目录。三平台一致：~/.dsh，可用 DSH_HOME 覆盖。"""
    return os.environ.get("DSH_HOME") or os.path.join(
        os.path.expanduser("~"), ".dsh")


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
