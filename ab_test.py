# -*- coding: utf-8 -*-
"""提示词 A/B 对照实验:同一镜头,三种提示词策略,固定种子"""
import json, os, sys, time, urllib.request, subprocess, re

sys.path.insert(0, r"E:/ai/story2video")
import pipeline
from pipeline import stop_comfy, start_comfy, wait_prompt, http_json, COMFY

OUT = r"E:\ai\ComfyUI_windows_portable\ComfyUI\output"
CMP = r"E:\ai\ab_compare"
os.makedirs(CMP, exist_ok=True)

STYLE = "anime style, 3d rendered characters, medieval European knight era"

VARIANTS = [
    ("A_复杂现状", 1280, 544, 121,
     "Rapid dolly-in to medium shot. Young scout gallops frantically, face pale with terror, "
     "pointing a trembling finger toward the darkening northern horizon. Sweat beads on his brow, "
     "eyes wide with panic, signaling imminent cavalry charge. No distortion, no continuity errors. "
     + pipeline.CINE_LINE),
    ("B_简化", 1280, 704, 81,
     "A young scout gallops frantically on horseback at dusk, face pale with terror, pointing toward "
     "the northern horizon, " + STYLE + ", medium shot"),
    ("C_极简", 1280, 704, 81,
     "terrified scout on horseback, dusk battlefield, " + STYLE),
]


def build(prompt, w, h, frames, seed, prefix):
    return {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan", "device": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": ""}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": w, "height": h, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
              "latent_image": ["7", 0], "seed": seed, "steps": 20, "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["8", 0], "vae": ["3", 0], "tile_size": 256, "overlap": 64, "temporal_size": 32, "temporal_overlap": 4}},
        "10": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["9", 0], "filename_prefix": prefix, "fps": 24,
               "lossless": False, "quality": 90, "method": "default"}},
    }


def main():
    pipeline.stop_ollama()
    for i, (name, w, h, frames, prompt) in enumerate(VARIANTS):
        prefix = f"ab_{chr(97+i)}"
        png0 = os.path.join(CMP, f"{name}_帧0.png")
        pngm = os.path.join(CMP, f"{name}_帧中.png")
        if os.path.exists(png0) and os.path.exists(pngm):
            print(f"{name}: 已存在,跳过", flush=True)
            continue
        stop_comfy()
        start_comfy()
        time.sleep(5)
        seed = 7777
        print(f"=== {name} 生成中 ({w}x{h}x{frames}帧, seed={seed}) ===", flush=True)
        t0 = time.time()
        payload = {"prompt": build(prompt, w, h, frames, seed, prefix), "client_id": "abtest"}
        r = http_json(f"{COMFY}/prompt", payload, timeout=60)
        if r.get("node_errors"):
            raise RuntimeError(f"校验失败: {r['node_errors']}")
        webp = wait_prompt(r["prompt_id"], name, timeout_s=2400)
        print(f"{name} 完成 ({time.time()-t0:.0f}s): {webp}", flush=True)
        # 抽帧对比图
        from PIL import Image
        im = Image.open(webp)
        n = im.n_frames
        im.seek(0)
        im.convert("RGB").save(png0)
        im.seek(n // 2)
        im.convert("RGB").save(pngm)
        # 转 mp4 方便并排看
        import imageio.v2 as imageio
        reader = imageio.get_reader(webp)
        writer = imageio.get_writer(os.path.join(CMP, f"{name}.mp4"), fps=24, codec="libx264", quality=8)
        for f in reader:
            writer.append_data(f[:, :, :3])
        writer.close()
        print(f"{name}: 对比素材已保存", flush=True)
    print("=== A/B 实验全部完成 ===", flush=True)


if __name__ == "__main__":
    main()
