# -*- coding: utf-8 -*-
"""引擎对照实验跑批器:组1=纯本地Wan, 组2=纯即梦Seedance
用法: python exp_run.py jimeng   (即梦组,约40分钟)
      python exp_run.py local    (本地组,约5小时,可断点)
素材:《四个号,四个粉》EP001 前6镜,提示词完全一致(控制变量)
"""
import json, os, sys, time, subprocess

sys.path.insert(0, r"E:/ai/story2video")
import pipeline
from pipeline import stop_comfy, start_comfy, stop_ollama, wait_prompt, http_json, COMFY, jimeng_submit, jimeng_poll, jimeng_download

BASE = r"E:\ai\实验\EP001对照"
SHOTS = json.load(open(os.path.join(BASE, "分镜表.json"), encoding="utf-8"))
STATE = os.path.join(BASE, "exp_state.json")
CAND = 3   # 每镜候选条数
SEEDS = {"jimeng": [111, 222, 333], "local": [555, 666, 777]}


def load_state():
    return json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}


def save_state(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def build_local_workflow(prompt, seed, prefix, frames=81):
    """本地 Wan 工作流:竖屏 9:16,不含任何风格后缀(控制变量,提示词原样)"""
    return {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan", "device": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": ""}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": 768, "height": 1344, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
              "latent_image": ["7", 0], "seed": seed, "steps": 20, "cfg": 5.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["8", 0], "vae": ["3", 0], "tile_size": 256, "overlap": 64, "temporal_size": 32, "temporal_overlap": 4}},
        "10": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["9", 0], "filename_prefix": prefix, "fps": 24,
               "lossless": False, "quality": 90, "method": "default"}},
    }


def run_jimeng():
    st = load_state()
    fails = st.get("jimeng_failures", [])
    os.makedirs(os.path.join(BASE, "组2_即梦"), exist_ok=True)
    for shot in SHOTS:
        sid = shot["id"]
        if st.get(f"jm_{sid}_done"):
            print(f"{sid}: 已完成,跳过", flush=True)
            continue
        for c in range(CAND):
            out = os.path.join(BASE, "组2_即梦", f"{sid}_c{c+1}.mp4")
            if os.path.exists(out) and os.path.getsize(out) > 0:
                continue
            # 审核是随机的:同一候选最多换种子重试3次
            got = False
            for attempt in range(3):
                try:
                    seed = SEEDS["jimeng"][c] + attempt * 999
                    task_id = jimeng_submit(shot["prompt"], seed, duration=shot["duration"])
                    url = jimeng_poll(task_id)
                    jimeng_download(url, out)
                    print(f"  {sid}_c{c+1} 完成: {out}", flush=True)
                    got = True
                    break
                except Exception as e:
                    msg = str(e)[:100]
                    print(f"  {sid}_c{c+1} 第{attempt+1}次失败: {msg}", flush=True)
                    time.sleep(5)
            if not got:
                fails.append({"shot": sid, "candidate": c + 1, "reason": "审核拦截或生成失败(重试3次)"})
                st["jimeng_failures"] = fails
                save_state(st)
        st[f"jm_{sid}_done"] = True
        save_state(st)
    print("=== 即梦组完成 ===", flush=True)
    if fails:
        print(f"\n审核拦截记录 {len(fails)} 条:", flush=True)
        for f in fails:
            print(f"  {f['shot']}_c{f['candidate']}: {f['reason']}", flush=True)


def run_local():
    stop_ollama()
    st = load_state()
    os.makedirs(os.path.join(BASE, "组1_本地"), exist_ok=True)
    for shot in SHOTS:
        sid = shot["id"]
        if st.get(f"lc_{sid}_done"):
            print(f"{sid}: 已完成,跳过", flush=True)
            continue
        for c in range(CAND):
            out = os.path.join(BASE, "组1_本地", f"{sid}_c{c+1}.mp4")
            if os.path.exists(out) and os.path.getsize(out) > 0:
                continue
            stop_comfy()
            start_comfy()
            time.sleep(5)
            prefix = f"exp_{sid}_{c+1}"
            print(f"=== {sid} 本地 候选{c+1} 生成中 ===", flush=True)
            t0 = time.time()
            payload = {"prompt": build_local_workflow(shot["prompt"], SEEDS["local"][c], prefix),
                       "client_id": "exp_local"}
            r = http_json(f"{COMFY}/prompt", payload, timeout=60)
            if r.get("node_errors"):
                raise RuntimeError(f"{sid} 校验失败: {r['node_errors']}")
            webp = wait_prompt(r["prompt_id"], f"{sid}_c{c+1}", timeout_s=2400)
            import imageio.v2 as imageio
            reader = imageio.get_reader(webp)
            writer = imageio.get_writer(out, fps=24, codec="libx264", quality=8)
            for f in reader:
                writer.append_data(f[:, :, :3])
            writer.close()
            print(f"{sid}_c{c+1} 完成 ({time.time()-t0:.0f}s)", flush=True)
        st[f"lc_{sid}_done"] = True
        save_state(st)
    print("=== 本地组完成 ===", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "jimeng"
    if mode == "jimeng":
        run_jimeng()
    elif mode == "local":
        run_local()
    else:
        print("用法: python exp_run.py jimeng|local")
