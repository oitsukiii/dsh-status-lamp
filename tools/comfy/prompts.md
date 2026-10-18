# 红绿灯素材是怎么生成的

用 WKPC 上的 ComfyUI + Qwen-Image 2.1（turbo int8）。

## 模型

```
UNETLoader   QwenImage2.1\qwen_image_2.1_turbo_int8_convrot.safetensors
CLIPLoader   QwenImage2.1\qwen3vl_8b_fp8_heretic.safetensors   type=qwen_image
VAELoader    qwen_image_2.1_vae_bf16.safetensors
```

## 关键点：用图生图保持风格一致

先出「三灯全灭」的底图，再拿它当参考图生成各个点亮状态。纯文生图每个状态
都会重新设计灯罩，切换时会跳。

`TextEncodeQwenImage21` 的 `images`（AUTOGROW 列表）传 `LoadImage` 的输出即可。

## 参数

```
steps     8       （turbo 蒸馏模型，步数很少）
cfg       1.0     （turbo 不需要 CFG）
sampler   euler
scheduler simple
shift     3.1     （ModelSamplingAuraFlow）
seed      42      （固定，减少随机漂移）
分辨率    768x1024 请求，实际输出 1024x1024
耗时      约 13-14 秒/张（RTX 5060 Ti）
```

## 提示词

**底图（全灭）：**
```
pixel art, a vertical traffic light, tall rectangular dark grey metal housing
with rounded corners, three circular bulbs stacked vertically, all three bulbs
turned OFF and dark, flat 8-bit colors, thick black outlines, sharp pixel
edges, front view, centered, isolated on plain white background, retro game
asset, no text, no labels, no glow
```

**黄灯（工作中）** — `--image lamp_off.png`：
```
the same pixel art traffic light, keep the exact same housing and style, but
now the MIDDLE bulb is glowing bright warm yellow, the top and bottom bulbs
stay dark, yellow light with a soft yellow glow, flat 8-bit colors, thick
black outlines, sharp pixel edges, front view, isolated on plain white
background, no text
```

**绿灯（刚完成）**：把 MIDDLE 换成 BOTTOM，颜色换成 green。

**红灯（需人工介入）**：把 MIDDLE 换成 TOP，颜色换成 red。

**离线（三个红叉）**：
```
the same pixel art traffic light, keep the exact same dark housing and style,
but every bulb is unlit and each of the three round slots shows a bold red X
mark instead, three red crosses, flat 8-bit colors, thick black outlines,
sharp pixel edges, front view, isolated on plain white background, no text
```

## 后处理

生成的是 1024×1024 白底图，还要做三件事（见 `tools/build-assets.py`）：

1. 裁到内容外接框（去掉大片白边）
2. 从四角洪水填充把背景抠成透明（只抠与边缘连通的白色，灯罩内部不动）
3. 等比缩放到统一高度 320px，居中贴到同一张画布上

## 已知瑕疵

灯罩在五个状态之间**有轻微差异**（侧边小凸起、宽度差几像素）—— 这是图生图
不可避免的漂移。在 160px 的显示尺寸下基本看不出来，但如果要做逐帧动画，
最好改成「只合成灯珠」的做法。
