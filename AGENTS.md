# AGENTS.md — DSH Status Lamp

给后续 agent 会话的项目上下文。

## 这个项目是什么

DSH 的桌面状态灯。两半：

- `bridge/state_bridge.py` —— 跑在 DSH 所在机器（NAS），只读 `projcache`，HTTP 暴露 `/state`
- `client/lamp.py` —— 跑在 Windows，置顶无边框圆点

## 硬性约束（别破坏）

1. **纯标准库**。bridge 和 client 都只用 stdlib，不许加依赖 —— 因为 client 要能在
   一个干净的 Windows 上双击运行。
2. **只读 DSH**。不许写 `$DSH_HOME` 下任何文件，不许调 DSH 的内部 API（那是私有
   Typert RPC，逆向成本高且会随版本变）。
3. **不经过 agent**。状态必须从落盘数据里读，不能让 agent 上报 —— 推理期间
   agent 无法执行工具，「思考中」这个状态只有外部才观测得到。
4. **Windows 控制台是 GBK**。任何脚本的中文输出都会 `UnicodeEncodeError`。
   测试脚本的打印一律走 ASCII 过滤（见 `tools/verify-state.py` 的 `ascii_safe`）。

## 状态判据

`record.rows.turnBoundary.val`：

```
openTurnStartSeq !== null  ⇒ working   （含推理期间）
llmRetry 非空              ⇒ retrying
文件 mtime > 20s 前        ⇒ offline
其余                        ⇒ idle
```

已用 4 个空闲会话 + 1 个活跃会话验证过，一致。

## 待办

- [ ] 「等待批准 / 等待输入」两态 —— 需要先找到可靠信号，别硬猜
- [ ] 素材：当前是纯色圆点，计划换成鲸鱼娘（矢量或 AI 生成）
- [ ] systemd 单元（`deploy/` 里有草稿，还没装）

## 验证

```sh
python3 -m py_compile bridge/state_bridge.py client/lamp.py tools/verify-state.py
python3 tools/verify-state.py --samples 3
```
