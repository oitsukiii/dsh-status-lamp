# DSH Status Lamp

一个浮在所有窗口之上的小灯，实时显示 **DSH 正在干什么**。

| 颜色 | 状态 |
|---|---|
| 🟡 黄 | DSH 在干活（思考 / 输出 / 调工具）|
| 🔴 红 | 需要你介入（有等你回答的问题）|
| 🟢 绿 | 刚完成（默认保持 3 分钟）|
| ⚪ 全灭 | 空闲 / 读不到数据 |

## 两种模式

### 本机模式（默认，推荐）

**灯和 DSH 跑在同一台机器上 —— 零配置、零网络。**

```
双击 lamp.py → 它直接读 ~/.dsh 下的状态文件 → 完事
```

不需要起额外的服务、不需要填地址、不需要开端口。

### 局域网模式

**DSH 在另一台机器上（NAS / VPS / 家里的服务器）。**

需要一个「状态桥」把文件变成 HTTP。**不用你手动装 —— 把
[`AGENTS.md`](AGENTS.md) 发给你自己的 DSH，让它帮你部署。**

```
1. 把 AGENTS.md 的内容（或链接）发给你的 DSH
2. agent 会装好桥、开好端口，然后给你一行地址：
      http://192.168.1.50:3099/events
3. 灯 右键 → 设置… → 选「局域网模式」→ 粘贴地址 → 测试连接 → 保存
```

---

## 快速开始

```bash
pip install pillow          # 可选，没有就退化成画圆点
python client/lamp.py
```

**Windows 上把 `.py` 关联到 `pythonw.exe` 双击即可。**

**右键菜单：** 立刻刷新 / 回到右下角 / 始终置顶 / **设置…** / 退出
**左键拖动：** 移动位置（记忆在配置目录）

---

## 状态是怎么判定的

两个数据源，互补：

| 来源 | 新鲜度 | 能判断 |
|---|---|---|
| `session_projcache/*.json` | 慢（1~8 秒）| 忙 / 闲 |
| `sessions/*/session.v4.jsonl.zstd` | 较快（2~3 秒）| 思考 / 调工具 / 完成 |

**判据：**

```
openTurnStartSeq 非空        ⇒ 有一个 turn 没结束 ⇒ 忙
事件流最后一条是 tool/call   ⇒ 正在跑工具（带工具名）
事件流最后一条是 turn/end    ⇒ 刚完成
userQuestions.active 非空    ⇒ 等你回答
llmRetry 非空                ⇒ 正在重试
```

**⚠️ 为什么不能用「让 agent 自己上报」：**

```
agent 在推理时无法调用工具 ⇒ 它报不出「我正在想」
⇒ 这个状态只有从外部观测才拿得到
```

---

## 延迟

```
DSH 落盘              2~4 秒   ← 主要来源，改不动
桥内部检查            200 毫秒
客户端                ~0（SSE 推送，不是轮询）
```

**⇒ 实测从「发消息」到「黄灯亮」约 2~4 秒，取决于是否撞上 DSH 的 flush 周期。**

---

## 目录

```
dsh_lamp/          核心库（两种模式共用）
  state.py         从落盘数据推导状态
  event_tail.py    读事件流尾部（不解压整个文件）
  paths.py         跨平台路径
  backends.py      FileBackend（本机）/ SseBackend（局域网）
client/lamp.py     显示层
bridge/state_bridge.py   状态桥（仅局域网模式需要）
tools/             验证脚本、素材生成
assets/states/     五张状态图
AGENTS.md          给用户 agent 的部署指南
```

---

## 依赖

```
Python 3.9+
tkinter           （Python 自带）
```

**两个可选项：**

| 依赖 | 作用 | 没有会怎样 |
|---|---|---|
| `Pillow` | 显示像素风素材 | 退化成画一个圆点 |
| zstd 解码器 | 读事件流，分辨「思考中/调工具」 | 只能判断「忙/闲」 |

**zstd 有三条路，按零依赖程度排序：**

| 方式 | 要求 | 装法 |
|---|---|---|
| **`compression.zstd`** | **Python 3.14+** | **不用装 ✓✓ 标准库自带** |
| `pyzstd` | Python 3.9+ | `pip install pyzstd` |
| `zstd` 命令 | 系统有 | `apt install zstd` / `brew install zstd` |

**⇒ 用 Python 3.14 的话，真的一个依赖都不用装。**
（3.12/3.13 装 pyzstd；Windows 上 winget 搜不到 zstd 命令行）

**设置窗口会显示当前用的是哪种解码器**；如果三种都没有，
会显示一个 **「一键安装解码器」** 按钮 —— 它用当前解释器执行
`pip install pyzstd`，装完重启灯即可（装完会即时更新状态）。

**⚠️ 桥只读事件流【尾部】的几 KB**（多帧 zstd 可以独立解压每帧），
所以哪怕是 37 MB 的日志，读取也只要几毫秒。

---

## License

MIT

## 关于「已读」

**DSH 没有已读信号。** 翻过客户端和事件模型：

```
· 没有 markRead / seen / readAt
· 没有 unread 计数 / 红点
· 没有 visibilitychange / document.hidden —— 连窗口焦点都不上报
· delivery-accepted 是「事件送达前端」，浏览器在后台也收得到，不代表人看了
```

所以只能做近似。当前的做法是：

```
turn/end  ⇒ done（青）
  之后的走向取决于你：
    · 你发下一条消息  ⇒ 新 turn 开始 ⇒ 立刻变黄（thinking）✓ 这就是"已读"
    · 你看了但不回复  ⇒ 保持青色，直到 --done-seconds（默认 300 秒）超时
```

**⇒ 实际效果就是「你回复了，绿灯就灭」。** 想调整保持时长：

```sh
python3 bridge/state_bridge.py --done-seconds 180   # 3 分钟（默认）
python3 bridge/state_bridge.py --done-seconds 600   # 10 分钟
python3 bridge/state_bridge.py --done-seconds 0     # 永不超时
```

**`--done-seconds 0` 的副作用**：绿灯永不过期，「空闲（全灭）」就不再出现。
想要空闲可见就给个有限值 —— 这也是默认用 180 秒的原因。


## 提示音

任务完成（进入 `done`）时播放 `assets/sounds/notify.wav`。

```
· 来源：ComfyUI-Notifications 插件的 notify.mp3，转成单声道 44.1k WAV
  （Windows 的 winsound 只支持 WAV，这样就不用引入任何第三方库）
· 只在状态【变成】done 时响一次，不会每次轮询都响
· 从离线恢复不会误响
```


## 已知限制

- **「等待审批」还做不了**：当前 `permissions.approval` 是 `never`（不需要审批），
  没有样本可以观察「审批挂起」时长什么样。等遇到实际场景再补。
- 「思考中」的判据是「最后事件是 `step/start`」—— 推理期间事件流不会写入，
  所以这个判据是对的；但如果某一步的推理极短（<采样间隔），可能一闪而过。
- 桥绑 `0.0.0.0`，局域网内任何设备都能读。内容只有状态元数据，没有对话内容。
- 素材还没做，现在是纯色圆点。后续会换成鲸鱼娘。
