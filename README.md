# DSH Status Lamp

一个浮在所有窗口之上的小圆灯，实时显示 **DSH 正在干什么**。

| 颜色 | 状态 | 判据 |
|---|---|---|
| 🟡 黄 | **等你回答** | `userQuestions.questions.active` 非空 |
| 🔴 红 | **重试中** | `llmRetry` 非空，或最后事件是 `llm/retry` |
| 🔵 蓝 | **调工具中** | 最后事件是 `tool/call`（标题带工具名，如 `调工具 · bash`）|
| 🟢 绿 | **思考中** | 最后事件是 `step/start`，且 turn 还开着 |
| 🟦 青 | **刚完成** | 最后事件是 `turn/end`，8 秒内 |
| ⚪ 灰白 | **空闲** | `openTurnStartSeq` 为 null |
| ⚫ 暗灰 | **离线** | 状态文件超过 20 秒没更新，或桥连不上 |

## 为什么要单独做这个

DSH 自带的状态看不见：Web UI 里只有一个转圈，切到别的窗口就完全没感觉。
社区的桌宠插件大多是**页面内**的，离开浏览器标签页就消失。

这个项目做两件事：

1. **从 DSH 落盘的状态里读到真实情况** —— 包括「模型正在推理」，这是让 agent
   自己上报时永远拿不到的状态（它在思考时无法调用工具）。
2. **把状态送到你眼前** —— 一个置顶、无边框、只有灯没有窗口的小圆点。

零 token 成本，不经过 agent，不修改 DSH。

## 架构

```
DSH（NAS）──落盘──> session_projcache/*.json
                           │
                    state_bridge.py   （只读，HTTP :3099）
                           │
                    局域网 HTTP GET /state
                           │
                     lamp.py （Windows 上跑的圆灯）
```

**为什么分两半**：DSH 可能跑在 NAS 上，而灯要在你的 Windows 桌面上。
让 agent 主动往客户端推送也能实现同样的效果，但那样拿不到「推理中」这个状态，
而且每次上报都要消耗一次工具调用。

## 快速开始

### 1. NAS 侧（DSH 所在机器）

```sh
python3 bridge/state_bridge.py --port 3099
```

验证：

```sh
curl http://127.0.0.1:3099/state
```

应该返回类似：

```json
{
  "state": "working",
  "age_s": 1.2,
  "session": "2d52b395-368",
  "turn": 714,
  "open_turn": true,
  "last_step": "start",
  "retry": false,
  "subagent": false,
  "context_ratio": 0.331
}
```

### 2. Windows 侧

```bat
pythonw client\lamp.py --url http://<NAS的IP>:3099/state
```

`pythonw` 不带控制台窗口。双击 `client\run-lamp.bat` 也行。

### 3. 验证

```sh
python3 tools/verify-state.py --url http://<NAS的IP>:3099/state
```

## 状态怎么判定的

### 两个数据源

**① `projcache`（快，判忙闲）** —— `$DSH_HOME/storages/session_projcache/sessions/session-*.json`：

`record.rows.turnBoundary.val`：

| 字段 | 含义 |
|---|---|
| `openTurnStartSeq` | **非 null 就表示有一个 turn 还没结束** —— 这是「忙」的权威判据 |
| `lastStepBoundary.kind` | `start` / `end`，当前 step 是否在进行 |
| `llmRetry` | 非空 ⇒ 正在重试 |
| `subagent` | 非空 ⇒ 子代理在跑 |
| `contextPressure` | 上下文占用量 / 窗口大小 |

**② 事件流（细，判「此刻在干什么」）** —— `session.v4.jsonl.zstd`

`sessionStats` 里的 `toolMs` / `llmMs` 是**事后汇总**的（只在 step 结束时更新），
所以判断不了「此刻」。真正即时的是事件流：

```
最后事件 = step/start     ⇒ LLM 正在推理/生成（这一步还没出结果）
最后事件 = tool/call      ⇒ 工具正在执行，data.name 就是工具名
最后事件 = turn/end       ⇒ 这一轮结束了
```

**这个文件是 35 MB / 解压后 128 MB**，但它是**多帧 zstd** —— 每个帧能独立解压，
所以 `bridge/event_tail.py` 只 seek 到末尾 400 KB，找到帧头，解最后 ~9 KB
就能拿到最近的事件，耗时几毫秒。

**优先级：**

```
文件超过 20 秒没更新   ⇒ offline
有等答问题            ⇒ question
llmRetry / retry 事件  ⇒ retrying
最后事件 = tool/call   ⇒ tool        （带工具名）
turn 还开着            ⇒ thinking
最后事件 = turn/end    ⇒ done（8 秒内）
其余                   ⇒ idle
```

**已验证**：四个空闲会话全部是 `openTurnStartSeq: null` + `kind: "end"`，
正在跑的会话是 `openTurnStartSeq: <数字>` + `kind: "start"`，判据一致。

## 界面

| 操作 | 效果 |
|---|---|
| 左键拖动 | 移动位置（松手后记住，存在 `%APPDATA%\dsh-status-lamp.pos`）|
| 右键 | 菜单：立刻刷新 / 回到右下角 / 始终置顶 / 退出 |
| 双击 | 立刻刷新 |

**透明背景**靠 Windows 的 `-transparentcolor` 色键实现，所以灯之外的部分
完全不挡视线，也不用整窗半透明。

## 目录

```
bridge/state_bridge.py     状态桥（NAS 侧，纯标准库）
client/lamp.py             圆灯（Windows 侧，纯标准库）
client/run-lamp.bat        双击启动
tools/verify-state.py      连通性与状态合理性检查
deploy/                    可选：systemd 单元
```

## 已知限制

- **「等待审批」还做不了**：当前 `permissions.approval` 是 `never`（不需要审批），
  没有样本可以观察「审批挂起」时长什么样。等遇到实际场景再补。
- 「思考中」的判据是「最后事件是 `step/start`」—— 推理期间事件流不会写入，
  所以这个判据是对的；但如果某一步的推理极短（<采样间隔），可能一闪而过。
- 桥绑 `0.0.0.0`，局域网内任何设备都能读。内容只有状态元数据，没有对话内容。
- 素材还没做，现在是纯色圆点。后续会换成鲸鱼娘。

## License

MIT
