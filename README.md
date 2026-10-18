# DSH Status Lamp

一个浮在所有窗口之上的小圆灯，实时显示 **DSH 正在干什么**。

```
🟢 工作中   🟠 重试中   ⚪ 空闲   ⚫ 离线
```

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

数据来自 `$DSH_HOME/storages/session_projcache/sessions/session-*.json` 里的
`record.rows.turnBoundary.val`：

| 字段 | 含义 |
|---|---|
| `openTurnStartSeq` | **非 null 就表示有一个 turn 还没结束** —— 这是「忙」的权威判据 |
| `lastStepBoundary.kind` | `start` / `end`，当前 step 是否在进行 |
| `llmRetry` | 非空 ⇒ 正在重试 |
| `subagent` | 非空 ⇒ 子代理在跑 |
| `contextPressure` | 上下文占用量 / 窗口大小 |

优先级：

```
文件超过 20 秒没更新  ⇒ offline
llmRetry 非空          ⇒ retrying
openTurnStartSeq 非空  ⇒ working     ← 包含模型推理期间
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

- 只有四态。**「等你批准」和「等你输入」暂时分辨不出来** —— DSH 的状态记账里
  没有稳定信号，硬猜不如不猜。
- 桥绑 `0.0.0.0`，局域网内任何设备都能读。内容只有状态元数据，没有对话内容。
- 素材还没做，现在是纯色圆点。后续会换成鲸鱼娘。

## License

MIT
