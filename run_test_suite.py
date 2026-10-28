# -*- coding: utf-8 -*-
"""对比测试集跑题器:逐条跑标准题,输出 mp4 + 抽帧图到各题目录"""
import json, os, sys, time, subprocess, re

sys.path.insert(0, r"E:/ai/story2video")
import pipeline
from pipeline import stop_comfy, start_comfy, stop_ollama, wait_prompt, http_json, COMFY

SUITE = r"E:\ai\对比测试集"
OUT = r"E:\ai\ComfyUI_windows_portable\ComfyUI\output"

PROMPTS = [
    ("题1_熊猫日落冲浪", "A panda standing on a surfboard in the ocean in sunset"),
    ("题2_宇航员喂鸭", "An astronaut feeding ducks on a sunny afternoon, reflection from the water"),
    ("题3_海龟游泳", "Turtle swimming in ocean"),
    ("题4_旋转葡萄", "Close up of grapes on a rotating table"),
    ("题5_骑士晨雾骑行", "A knight riding a horse through a misty forest at dawn, cinematic"),
    ("题6_赵子龙单骑救主", "A silver-armored general on a white horse holding a baby, charging through enemy soldiers, ancient Chinese battlefield, dynamic action"),
    ("题7_黛玉焚稿", "A frail beautiful woman in ancient dress burning manuscript pages in a bronze brazier, tears, candlelight, Chinese classical chamber"),
    ("题8_温酒斩华雄", "A bearded warrior in green robe carrying a guandao blade striding out of a military tent, ancient Chinese war camp"),
    ("题9_男子雨中奔跑", "A man running in the rain, wet hair and clothes, dim street lamps, slow motion, cinematic"),
    ("题10_夜市高空下降", "Aerial camera slowly descending over a crowded night market with many food stalls and people, bustling atmosphere"),
]


def build_workflow(prompt, seed, prefix):
    return {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan", "device": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": ""}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": 1280, "height": 704, "length": 81, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
              "latent_image": ["7", 0], "seed": seed, "steps": 20, "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["8", 0], "vae": ["3", 0], "tile_size": 256, "overlap": 64, "temporal_size": 32, "temporal_overlap": 4}},
        "10": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["9", 0], "filename_prefix": prefix, "fps": 24,
               "lossless": False, "quality": 90, "method": "default"}},
    }


def main():
    stop_ollama()
    state = os.path.join(SUITE, "suite_state.json")
    done = json.load(open(state, encoding="utf-8"))["done"] if os.path.exists(state) else []
    for i, (name, prompt) in enumerate(PROMPTS):
        if name in done:
            print(f"{name}: 已完成,跳过", flush=True)
            continue
        d = os.path.join(SUITE, name)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "提示词.txt"), "w", encoding="utf-8").write(prompt)
        mp4 = os.path.join(d, "本地Wan_结果.mp4")
        if os.path.exists(mp4):
            done.append(name)
            json.dump({"done": done}, open(state, "w", encoding="utf-8"))
            continue
        stop_comfy()
        start_comfy()
        time.sleep(5)
        seed = 7777 + i
        print(f"=== {name} 生成中 (seed={seed}) ===", flush=True)
        t0 = time.time()
        prefix = f"suite_{i:02d}"
        payload = {"prompt": build_workflow(prompt, seed, prefix), "client_id": "suite"}
        r = http_json(f"{COMFY}/prompt", payload, timeout=60)
        if r.get("node_errors"):
            raise RuntimeError(f"{name} 校验失败: {r['node_errors']}")
        webp = wait_prompt(r["prompt_id"], name, timeout_s=2400)
        print(f"{name} 完成 ({time.time()-t0:.0f}s)", flush=True)
        # 抽帧 + 转 mp4
        from PIL import Image
        im = Image.open(webp)
        im.seek(0)
        im.convert("RGB").save(os.path.join(d, "首帧.png"))
        im.seek(im.n_frames // 2)
        im.convert("RGB").save(os.path.join(d, "中间帧.png"))
        import imageio.v2 as imageio
        reader = imageio.get_reader(webp)
        writer = imageio.get_writer(mp4, fps=24, codec="libx264", quality=8)
        for f in reader:
            writer.append_data(f[:, :, :3])
        writer.close()
        done.append(name)
        json.dump({"done": done}, open(state, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{name}: 素材已保存", flush=True)
    print(f"=== 测试集完成: {len(done)}/10 题 ===", flush=True)


if __name__ == "__main__":
    main()
