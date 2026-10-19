"""界面文案 —— 中英双语。

用法：
    from dsh_lamp.i18n import t
    t("settings_title", lang)
    t("saved_ok", lang, s="tool")      # 带占位符

找不到的词条会回退到中文，再回退到 key 本身，不会抛异常。
"""

from __future__ import annotations

LANGS = ("zh", "en")
LANG_NAMES = {"zh": "中文", "en": "English"}

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # 设置窗口
        "settings_title": "DSH 状态灯 — 设置",
        "language": "语言 / Language",
        "data_source": "数据来源",
        "mode_local": "本机模式 —— DSH 装在这台电脑上时选我",
        "mode_lan": "局域网模式 —— DSH 装在局域网内其他电脑上时选我",
        "frame_local": " 本机模式 ",
        "frame_lan": " 局域网模式 ",
        "dsh_dir": "DSH 数据目录",
        "browse": "浏览…",
        "browse_title": "选择 DSH 数据目录（里面有 storages 和 sessions 的那个）",
        "default_hint": "默认是 {path}，里面应该有 storages/ 和 sessions/ 两个子目录。",
        "test_read": "测试读取",
        "test_conn": "测试连接",
        "read_ok": "读取成功",
        "read_fail": "读取失败",
        "conn_ok": "连接成功",
        "conn_fail": "连接失败",
        "bridge_addr": "桥地址",
        "no_bridge": " 还没有桥？ ",
        "copy_hint": "点下面的按钮复制一段指令，发给你的 DSH，"
                     "让它配好服务器端软件并把地址回给你。",
        "copy_btn": "复制配置指令",
        "copied": "已复制，发给你的 DSH 即可",
        "project_url": "项目地址：{url}",
        # 解码器
        "dep_frame": " zstd 解码器 ",
        "dep_installed": "已安装",
        "dep_missing": "未安装",
        "dep_install": "一键安装解码器",
        "dep_reinstall": "重新安装解码器",
        "dep_installing": "正在安装…",
        "dep_done": "装好了——重启软件生效",
        "dep_failed": "安装失败：{msg}",
        "dep_manual": "可以手动执行：pip install pyzstd",
        "why1": "为什么要安装解码器：DSH 的详细日志是使用 zstd 压缩储存的，",
        "why2": "需要装一个 zstd 解码器才能读取“思考中、调用工具”这类状态。",
        # 其他
        "sound": "任务完成时播放提示音",
        "breath": "灯光呼吸效果",
        "save": "保存并重连",
        "cancel": "取消",
        "saved_verifying": "已保存，正在验证连接…",
        "saved_ok": "已保存，连接成功（{s}）",
        "saved_fail": "已保存，但连接失败（{e}）—— 请检查{where}",
        "where_dir": "数据目录",
        "where_addr": "桥地址",
        "save_fail": "保存失败：{e}",
        # 右键菜单
        "menu_refresh": "立刻刷新",
        "menu_reset": "回到右下角",
        "menu_topmost": "始终置顶",
        "menu_settings": "设置…",
        "menu_quit": "退出",
        # 灯语说明
        "lang_btn": "灯语说明",
        "lang_title": "灯语说明",
        "lang_intro": "灯语一览",
        "lang_red": "红", "lang_yellow": "黄", "lang_green": "绿", "lang_off": "全灭",
        "lang_red_h": "需要你介入",
        "lang_red_1": "审批弹窗挂起 —— 等你点「允许」",
        "lang_red_2": "agent 主动提问 —— 等你回答",
        "lang_yellow_h": "正在干活",
        "lang_yellow_1": "调工具 / 推理 / 生成",
        "lang_yellow_2": "重试中",
        "lang_yellow_3": "刚开的新一轮（状态还没落盘）",
        "lang_green_h": "刚完成",
        "lang_green_1": "一轮结束后保持 3 分钟",
        "lang_green_2": "你发下一条消息就变黄",
        "lang_off_h": "空闲",
        "lang_off_1": "没有进行中的任务",
        "lang_off_2": "或读不到数据（DSH 没跑 / 路径不对）",
        "close": "关闭",
    },
    "en": {
        "settings_title": "DSH Status Lamp — Settings",
        "language": "Language / 语言",
        "data_source": "Data source",
        "mode_local": "Local — DSH runs on this computer",
        "mode_lan": "LAN — DSH runs on another machine on the network",
        "frame_local": " Local ",
        "frame_lan": " LAN ",
        "dsh_dir": "DSH data directory",
        "browse": "Browse…",
        "browse_title": "Pick the DSH data directory (the one with storages and sessions)",
        "default_hint": "Default is {path}, which should contain storages/ and sessions/.",
        "test_read": "Test read",
        "test_conn": "Test connect",
        "read_ok": "Read OK",
        "read_fail": "Read failed",
        "conn_ok": "Connected",
        "conn_fail": "Connect failed",
        "bridge_addr": "Bridge address",
        "no_bridge": " No bridge yet? ",
        "copy_hint": "Copy the instruction below and send it to your own DSH. "
                     "It will set the server side up and reply with an address.",
        "copy_btn": "Copy setup prompt",
        "copied": "Copied — send it to your DSH",
        "project_url": "Project: {url}",
        "dep_frame": " zstd decoder ",
        "dep_installed": "Installed",
        "dep_missing": "Not installed",
        "dep_install": "Install decoder",
        "dep_reinstall": "Reinstall decoder",
        "dep_installing": "Installing…",
        "dep_done": "Installed — restart the app",
        "dep_failed": "Install failed: {msg}",
        "dep_manual": "Or run: pip install pyzstd",
        "why1": "Why: DSH stores its detailed log with zstd compression,",
        "why2": "so a decoder is needed to read states like thinking or running a tool.",
        "sound": "Play a sound when a task completes",
        "breath": "Breathe the light",
        "save": "Save and reconnect",
        "cancel": "Cancel",
        "saved_verifying": "Saved, verifying connection…",
        "saved_ok": "Saved, connected ({s})",
        "saved_fail": "Saved, but connection failed ({e}) — check the {where}",
        "where_dir": "data directory",
        "where_addr": "bridge address",
        "save_fail": "Save failed: {e}",
        "menu_refresh": "Refresh now",
        "menu_reset": "Reset position",
        "menu_topmost": "Always on top",
        "menu_settings": "Settings…",
        "menu_quit": "Quit",
        "lang_btn": "Lamp states",
        "lang_title": "What the lamp means",
        "lang_intro": "State reference",
        "lang_red": "Red", "lang_yellow": "Yellow", "lang_green": "Green", "lang_off": "Off",
        "lang_red_h": "Needs you",
        "lang_red_1": "Approval dialog open -- waiting for allow",
        "lang_red_2": "The agent asked you a question",
        "lang_yellow_h": "Working",
        "lang_yellow_1": "Running a tool / reasoning / generating",
        "lang_yellow_2": "Retrying",
        "lang_yellow_3": "A new turn just started, state not written yet",
        "lang_green_h": "Just finished",
        "lang_green_1": "Stays green for three minutes",
        "lang_green_2": "Turns yellow when you send the next message",
        "lang_off_h": "Idle",
        "lang_off_1": "Nothing in progress",
        "lang_off_2": "Or the data cannot be read (DSH not running / wrong path)",
        "close": "Close",
    },
}


def t(key: str, lang: str = "zh", **fmt) -> str:
    """取词条。lang 不支持时退回中文，再退回 key。"""
    table = STRINGS.get(lang) or STRINGS["zh"]
    text = table.get(key) or STRINGS["zh"].get(key) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text
    return text


def normalize(lang: str | None) -> str:
    return lang if lang in LANGS else "zh"
