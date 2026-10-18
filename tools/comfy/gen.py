# -*- coding: utf-8 -*-
"""Generate an image with Qwen-Image 2.1 on the local ComfyUI.

usage:  python comfy_gen.py --prompt "..." --out out.png [--neg "..."]
        [--w 1024] [--h 1024] [--steps 8] [--cfg 1.0] [--seed 123]
        [--image base.png]      # optional: image-edit mode (single reference)
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8188"
COMFY_OUT = r"C:\ComfyUI\output"

def a(x):
    return str(x).encode("ascii", "backslashreplace").decode("ascii")

def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def build(prompt, negative, w, h, steps, cfg, seed, sampler, scheduler,
          shift, unet, clip_name, vae_name, image_name):
    wf = {}
    wf["1"] = {"class_type": "UNETLoader",
               "inputs": {"unet_name": unet, "weight_dtype": "default"}}
    wf["2"] = {"class_type": "CLIPLoader",
               "inputs": {"clip_name": clip_name, "type": "qwen_image"}}
    wf["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}}

    # Qwen-Image 2.1 用自己的文本编码节点，它同时产出 latent
    enc = {"clip": ["2", 0], "prompt": prompt, "negative_prompt": negative,
           "resolution": max(w, h), "vae": ["3", 0], "images": []}
    if image_name:
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        enc["images"] = [["10", 0]]          # AUTOGROW 列表
    wf["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": enc}

    # shift 对 Qwen-Image 的采样质量有影响
    wf["5"] = {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": ["1", 0], "shift": shift}}

    wf["6"] = {"class_type": "KSampler",
               "inputs": {"model": ["5", 0], "seed": seed, "steps": steps,
                          "cfg": cfg, "sampler_name": sampler,
                          "scheduler": scheduler,
                          "positive": ["4", 0], "negative": ["4", 1],
                          "latent_image": ["4", 2], "denoise": 1.0}}
    wf["7"] = {"class_type": "VAEDecode",
               "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    tag = "dshlamp/" + uuid.uuid4().hex[:8]
    wf["8"] = {"class_type": "SaveImage",
               "inputs": {"images": ["7", 0], "filename_prefix": tag}}
    return wf, tag


def wait(pid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = get("/history/" + pid)
        if pid in h:
            entry = h[pid]
            st = entry.get("status") or {}
            if st.get("completed") or st.get("status_str") == "success":
                return entry
            if st.get("status_str") == "error":
                msgs = st.get("messages") or []
                raise RuntimeError("comfy error: " + a(msgs)[:800])
        time.sleep(1.5)
    raise TimeoutError("generation timed out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--neg", default="text, watermark, signature, blurry, low quality")
    ap.add_argument("--out", required=True)
    ap.add_argument("--w", type=int, default=1024)
    ap.add_argument("--h", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--shift", type=float, default=3.1)
    ap.add_argument("--unet", default=r"QwenImage2.1\qwen_image_2.1_turbo_int8_convrot.safetensors")
    ap.add_argument("--clip", default=r"QwenImage2.1\qwen3vl_8b_fp8_heretic.safetensors")
    ap.add_argument("--vae", default="qwen_image_2.1_vae_bf16.safetensors")
    ap.add_argument("--image", default=None)
    args = ap.parse_args()

    wf, tag = build(args.prompt, args.neg, args.w, args.h, args.steps, args.cfg,
                    args.seed, args.sampler, args.scheduler, args.shift,
                    args.unet, args.clip, args.vae, args.image)

    t0 = time.time()
    res = post("/prompt", {"prompt": wf, "client_id": "dsh-lamp"})
    pid = res.get("prompt_id")
    print("  queued prompt_id=" + a(pid))
    entry = wait(pid)
    dt = time.time() - t0
    outs = entry.get("outputs") or {}
    files = []
    for node_out in outs.values():
        for im in (node_out.get("images") or []):
            files.append(im)
    if not files:
        print("  NO OUTPUT"); sys.exit(2)
    im = files[0]
    src = os.path.join(COMFY_OUT, im.get("subfolder", ""), im["filename"])
    print("  done in %.1fs -> %s" % (dt, a(src)))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(src, "rb") as fh:
        data = fh.read()
    with open(args.out, "wb") as fh:
        fh.write(data)
    print("  saved %s (%d bytes)" % (a(os.path.abspath(args.out)), len(data)))


if __name__ == "__main__":
    main()
