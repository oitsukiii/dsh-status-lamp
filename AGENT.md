# 给 agent：安装「DSH 状态灯」的桥接插件

**目标**：在这台机器上把 `dsh-lamp-bridge` 插件装好，让它监听 `3099`，然后把一个
可以填进状态灯客户端的地址告诉用户。

读完这份就能做完，不需要猜。

---

## 1. 先确认现状

```bash
curl -s --max-time 3 http://127.0.0.1:3099/state
```

- **返回 JSON 且有 `state` 字段** ⇒ 已经装好了，直接跳到第 4 步
- 连接失败 ⇒ 继续第 2 步

还要确认这个 JSON 里的 `source` 字段：`"plugin"` 表示是插件在跑，`"python"` 表示是老的
Python 桥在跑。**如果用户要的是插件，`source` 不是 `plugin` 就说明还没换过来。**

---

## 2. 装插件

### 方式 A：DSH 界面（有 Web UI 时最省事）

让用户在 **插件** 页面添加这个地址：

```
github:oitsukiii/dsh-status-lamp#path:/packages/dsh-lamp
```

### 方式 B：命令行改 profile（没有 UI，或 UI 装不上时）

profile 一般在 `$DSH_HOME/profiles/web/`。

**① 把插件放进 vendor 目录**

```bash
PROFILE=/root/.dsh/profiles/web          # 按实际 DSH_HOME 调整
mkdir -p "$PROFILE/vendor"
git clone --depth 1 https://github.com/oitsukiii/dsh-status-lamp /tmp/dsl
cp -r /tmp/dsl/packages/dsh-lamp "$PROFILE/vendor/dsh-lamp-bridge"
rm -rf "$PROFILE/vendor/dsh-lamp-bridge/node_modules"
```

**② 声明成依赖（必须用 `file:`，否则 pnpm 会去 npm registry 找，找不到会导致 DSH 起不来）**

```bash
cd "$PROFILE"
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('package.json')
d = json.loads(p.read_text())
d.setdefault('dsh', {}).setdefault('profile', {}).setdefault('bundles', [])
if 'dsh-lamp-bridge' not in d['dsh']['profile']['bundles']:
    d['dsh']['profile']['bundles'].append('dsh-lamp-bridge')
d.setdefault('dependencies', {})['dsh-lamp-bridge'] = 'file:./vendor/dsh-lamp-bridge'
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n')
PY
pnpm install
```

**③ 装完检查（这几项缺一项都不会加载，而且 DSH 不会报错）**

```bash
# 插件包必须自己在 package.json 里声明 dsh.bundle.patch
python3 -c "import json;print(json.load(open('$PROFILE/vendor/dsh-lamp-bridge/package.json'))['dsh'])"
# 期望：{'bundle': {'patch': './cordis.patch.yml'}}

# 它自带的 patch 必须用 insert 包裹（裸 - id: 只会「修改已有条目」，新增无效）
cat "$PROFILE/vendor/dsh-lamp-bridge/cordis.patch.yml"
# 期望：
#   - insert:
#       - id: dsh-lamp-bridge
#         name: 'dsh-lamp-bridge'
#         config: { host: '0.0.0.0', port: 3099 }
```

> ⚠️ 这两条是这个插件当初装不上的真正原因。缺了任何一条，DSH 会**静默跳过**，
> 日志里连插件名都不会出现。

---

## 3. 重启 DSH

**动手前必须问用户** —— 重启会中断他正在进行的会话。

```bash
systemctl restart dsh-web      # 或者用户习惯的重启方式
```

---

## 4. 验证并回报

```bash
curl -s http://127.0.0.1:3099/state | python3 -m json.tool
```

应该看到：

```json
{
  "state": "idle",
  "source": "plugin",
  "endpoint": "http://127.0.0.1:3099/events"
}
```

**然后算出用户要填的地址：**

| 状态灯在哪 | 填什么 |
|---|---|
| 和 DSH 同一台机器 | `http://127.0.0.1:3099/events` |
| 另一台机器 | `http://<这台机器的局域网 IP>:3099/events` |

**查局域网 IP：**

```bash
hostname -I | awk '{print $1}'          # Linux
ipconfig                                # Windows
```

**最后把地址原样告诉用户。**

---

## 5. 边界

- **不要把 3099 暴露到公网** —— 这个端口没有任何认证，读到的是你 DSH 的实时状态
- 局域网内网段可达就够了；确实要跨网访问，套 WireGuard 或 SSH 隧道
- 不要改 DSH 的其他配置，只动这里列出的文件
- Python 桥（`bridge/state_bridge.py`）是备选方案，见 `docs/python-bridge.md`；
  **除非用户明确要求，否则用插件**
