# 仓库说明（给在这个仓库里干活的 agent）

这份文件不是安装指南 —— 用户装插件时用的是客户端里那个「复制提示词」，
那段提示词本身是自给自足的。

留在这里的是**改这个插件时需要知道的两件事**，它们都踩过。

## 一、插件要能被 DSH 加载，必须同时满足三条

缺任何一条，DSH 都会**静默跳过**：不报错、日志里连插件名都不出现。

1. **包自己声明 bundle**
   ```json
   "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
   ```
   只把名字写进 profile 的 `dsh.profile.bundles` 是不够的。

2. **patch 用 `insert:` 包裹**
   ```yaml
   - insert:                      # ← 必须
       - id: dsh-lamp-bridge
         name: 'dsh-lamp-bridge'
   ```
   裸的 `- id:` 是一条「修改已有条目」的 patch，对不存在的 id 会被丢弃。

3. **在 profile 的 dependencies 里有 `file:` 声明**
   ```json
   "dsh-lamp-bridge": "file:./vendor/dsh-lamp-bridge"
   ```
   否则 pnpm 同步 `node_modules` 时会把这个「多余的包」删掉。

## 二、客户端不能引 numpy

`client/lamp.py` 只允许依赖 Pillow。呼吸灯的圆形蒙版一度用了 numpy，
在没装它的机器上整个程序起不来（`ModuleNotFoundError`）。
需要数组化的图像操作时，优先找 Pillow 的等价写法。

同样的道理：`sys.stdout` / `sys.stderr` 在窗口模式打包后是 `None`，
任何 `print()` 都会抛异常 —— `_ensure_std_streams()` 就是为此存在的。

## 三、这台机器上 git 要走代理

直连 github.com 实测 **20 秒超时**，走 `127.0.0.1:7897` 是 **2 秒**。

```bash
git config --global http.proxy  http://127.0.0.1:7897
git config --global https.proxy http://127.0.0.1:7897
```

`pnpm add github:...`、`git clone` 也一样受影响 —— 不开代理 clone 这个仓库要三分钟。

代理没起来的时候 git 会直接失败，不是变慢。排查时先 `ss -ltn | grep 7897`。

## 四、验证清单

改完插件代码后：

```bash
cd packages/dsh-lamp && ../../../dsh-*/node_modules/.bin/tsc --noEmit
```

改完客户端后：

```bash
python3 -c "import ast;ast.parse(open('client/lamp.py').read(), feature_version=(3,9))"
```

**第二条是必须的** —— 嵌套 f-string（同引号）只有 Python 3.12+ 能解析，
写的时候在自己机器上没事，到用户那里就是语法错误。
