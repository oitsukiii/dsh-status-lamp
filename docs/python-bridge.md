# Python 桥部署指南（备选方案）

> **首选是 DSH 插件。** 在 DSH 的「插件」页面添加
> `github:oitsukiii/dsh-status-lamp#path:/packages/dsh-lamp`，
> 重启 DSH 后它就会监听 3099，随 DSH 启停，不用管进程。
>
> 这份文档只适用于你不想装插件、或者 DSH 那台机器上没有 DSH（只跑一个裸 Python）的情况。
> Python 桥的代价：要装 Python + Pillow + zstd 解码器，还要自己守住进程。

---

# 部署 DSH 状态桥 —— 给 agent 的执行说明

> **你是 DSH 的 agent。用户刚把这份文件发给你，想让你在自己的机器上
> 起一个「状态桥」，让桌面上的状态灯能显示你的工作状态。**
>
> 请按下面的步骤做完，最后给用户一行**可以直接粘贴进客户端的地址**。

---

## 你要做什么（一句话）

在 **DSH 所在的这台机器** 上常驻运行 `bridge/state_bridge.py`，
它只读 DSH 自己写的状态文件，用 HTTP 暴露成一个只读接口。

**不修改 DSH、不碰它的配置、不写它的任何文件。**

---

## 步骤 1：拿到代码

```bash
# 如果用户已经 clone 过
cd <仓库路径>

# 否则
git clone <仓库地址> dsh-status-lamp
cd dsh-status-lamp
```

**只需要这两个文件：**

```
bridge/state_bridge.py
dsh_lamp/            （整个目录：state.py / event_tail.py / paths.py）
```

---

## 步骤 2：检查依赖

```bash
python3 --version          # 需要 3.9+
command -v zstd            # 需要 zstd 命令行
```

**缺 zstd 的话按平台装：**

```bash
# Debian/Ubuntu
sudo apt-get install -y zstd

# macOS
brew install zstd

# Windows（agent 应该用 winget 或 choco）
winget install zstd
```

> ⚠️ **没有 zstd 也能跑**，只是读不到「思考中 / 调工具」这种细粒度状态，
> 只能判断忙/闲。**优先装上。**

---

## 步骤 3：前台试跑

```bash
python3 -u bridge/state_bridge.py --host 0.0.0.0 --port 3099
```

**另开一个终端验证：**

```bash
curl -s http://127.0.0.1:3099/state | python3 -m json.tool
```

**期望看到类似：**

```json
{
  "state": "idle",
  "phase": null,
  "turn": 42,
  "open_turn": false,
  "context_ratio": 0.12
}
```

**`state` 的取值：**

```
working    DSH 正在干活（思考 / 输出 / 调工具）
tool       正在执行工具（另有 tool 字段给出工具名）
question   有等你回答的问题
retrying   正在重试 LLM 请求
done       刚完成一轮
idle       空闲
offline    状态文件太久没更新（DSH 可能没在跑）
```

**如果 `state` 永远是 `offline`：**

```
排查顺序：
1. DSH 真的在这台机器上跑吗（systemctl status dsh-web / ps aux | grep dsh）
2. $DSH_HOME 对不对（默认 ~/.dsh，看环境变量有没有覆盖）
3. 那个目录下有 storages/session_projcache/sessions/session-*.json 吗
```

---

## 步骤 4：确认监听地址

**桥默认绑 `0.0.0.0`，也就是所有网卡。**

**找出这台机器的局域网 IP：**

```bash
# Linux
ip -4 addr show scope global | grep -oP 'inet \K[\d.]+'
# macOS
ipconfig getifaddr en0
# Windows
ipconfig | findstr IPv4
```

**记下来，比如 `192.168.1.50`。**

---

## 步骤 5：放行防火墙（**重要**）

**只放行局域网，不要暴露到公网。**

```bash
# ufw（Debian/Ubuntu）
sudo ufw allow from 192.168.0.0/16 to any port 3099 proto tcp

# firewalld（RHEL/Fedora）
sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=192.168.0.0/16 port port=3099 protocol=tcp accept'
sudo firewall-cmd --reload

# Windows
New-NetFirewallRule -DisplayName "DSH Lamp" -Direction Inbound `
  -Protocol TCP -LocalPort 3099 -RemoteAddress 192.168.0.0/16 -Action Allow
```

> ⚠️ **绝对不要** `allow 3099/tcp` 无条件放行 —— 那等于对公网开放。

---

## 步骤 6：做成常驻服务

### Linux（systemd）

```bash
sudo tee /etc/systemd/system/dsh-status-bridge.service >/dev/null <<'UNIT'
[Unit]
Description=DSH status bridge (read-only)
After=network-online.target

[Service]
Type=simple
User=__USER__
WorkingDirectory=__REPO__
Environment=DSH_HOME=__DSH_HOME__
ExecStart=/usr/bin/python3 -u bridge/state_bridge.py --host 0.0.0.0 --port 3099
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# 用实际路径替换占位符
sudo sed -i "s|__USER__|$USER|; s|__REPO__|$(pwd)|; s|__DSH_HOME__|${DSH_HOME:-$HOME/.dsh}|" \
  /etc/systemd/system/dsh-status-bridge.service

sudo systemctl daemon-reload
sudo systemctl enable --now dsh-status-bridge
systemctl status dsh-status-bridge --no-pager
```

### macOS（launchd）

```bash
PLIST="$HOME/Library/LaunchAgents/com.dsh.statusbridge.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.dsh.statusbridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string><string>-u</string>
    <string>$(pwd)/bridge/state_bridge.py</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>3099</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PL
launchctl load "$PLIST"
```

### Windows（计划任务）

```powershell
$repo = (Get-Location).Path
$py = (Get-Command pythonw.exe).Source
$action = New-ScheduledTaskAction -Execute $py `
  -Argument "$repo\bridge\state_bridge.py --host 0.0.0.0 --port 3099" `
  -WorkingDirectory $repo
Register-ScheduledTask -TaskName "DSH-StatusBridge" -Action $action `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn) -RunLevel Limited -Force
Start-ScheduledTask -TaskName "DSH-StatusBridge"
```

> ⚠️ **Windows 上不要从 SSH 会话里直接 `Start-Process` 起常驻进程** ——
> SSH 断开时会被连带杀掉。用计划任务才活得过会话。

---

## 步骤 7：最后给用户这一行

**格式：**

```
http://<这台机器的局域网IP>:3099/events
```

**例如：**

```
http://192.168.1.50:3099/events
```

**告诉用户：**

1. 打开状态灯，**右键 → 设置…**
2. 数据来源选 **局域网模式**
3. 把上面那行地址粘进去
4. 点 **测试连接**，看到 ✓ 就连上了
5. 点 **保存并重连**

---

## 边界（不要做的事）

```
✗ 不要改 DSH 的任何配置
✗ 不要重启 DSH（会中断用户的会话）
✗ 不要把 3099 暴露到公网（这个端口没有认证）
✗ 不要杀已有的 state_bridge 进程，先看它是不是已经在跑
```

**想换端口？** `--port` 改，同时记得改防火墙规则，并把新地址告诉用户。

**做了哪些改动要告诉用户：**

```
· 新建了 <仓库路径>
· 起了一个 systemd/launchd/计划任务 服务 <名字>
· 放行了 3099（限局域网）
· 桥的地址是 http://<ip>:3099/events
```
