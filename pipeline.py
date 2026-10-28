# -*- coding: utf-8 -*-
"""
小说 → 视频 自动化流水线
用法:
  python pipeline.py script    只做第一步:LLM 写分镜脚本 -> script.json
  python pipeline.py generate  按脚本逐镜头生成视频(支持断点续跑)
  python pipeline.py concat    拼接所有镜头 -> final_story.mp4
  python pipeline.py all       全流程
"""
import json, os, sys, time, random, re, urllib.request, subprocess

# ---------- 故事库:每个故事一个独立大目录 ----------
STORIES_ROOT = r"E:\ai\stories"
SETTINGS_FILE = r"E:\ai\story2video\settings.json"
OUTPUT_DIR = r"E:\ai\ComfyUI_windows_portable\ComfyUI\output"
COMFY = "http://127.0.0.1:8188"
OLLAMA = "http://localhost:11434"

# 当前故事由 settings.json 的 当前故事 决定
_raw_set = json.load(open(SETTINGS_FILE, encoding="utf-8")) if os.path.exists(SETTINGS_FILE) else {}
_active = _raw_set.get("当前故事", "")
BASE = os.path.join(STORIES_ROOT, _active) if _active else r"E:\ai\story2video"
STORY = os.path.join(BASE, "story.txt")
SCRIPT = os.path.join(BASE, "script.json")
STATE = os.path.join(BASE, "state.json")
ASSETS_DIR = os.path.join(BASE, "assets")
CLIPS_DIR = os.path.join(BASE, "clips")
AUDIO_DIR = os.path.join(BASE, "audio")
# ---------- 全局设置(settings.json 驱动,改完重跑对应阶段即可) ----------
def _load_settings():
    return json.load(open(SETTINGS_FILE, encoding="utf-8")) if os.path.exists(SETTINGS_FILE) else {}

SET = _load_settings()
MODEL_MAIN = SET.get("模型", {}).get("主线模型", "qwen3.6:iq4xs")   # 27B:重写润色/关键段
MODEL_FAST = SET.get("模型", {}).get("快写模型", "lumen:14b")       # 14B:方向/梗概/草稿
SCENES_MIN = SET.get("编剧", {}).get("镜头数下限", 8)
SCENES_MAX = SET.get("编剧", {}).get("镜头数上限", 12)
USE_REWRITE = SET.get("编剧", {}).get("启用27B润色", False)
_VID = SET.get("视频", {})
W, H, FRAMES = _VID.get("宽度", 1280), _VID.get("高度", 544), _VID.get("帧数", 121)
STEPS = _VID.get("步数", 20)
CFG_V = _VID.get("CFG", 5.0)
SEED_BASE = _VID.get("种子基数", 1000)
TTS_ON = SET.get("配音", {}).get("启用旁白", True)
TTS_RATE = SET.get("配音", {}).get("语速", 2)
ENGINE = SET.get("视频引擎", "comfyui")   # comfyui=本地Wan / jimeng=即梦API
_JIMENG = SET.get("即梦", {})
JIMENG_BASE = "https://ark.cn-beijing.volces.com/api/v3"

_CINE = SET.get("画面定调", {})
_STYLE_RAW = str(_CINE.get("风格", ""))
_STYLE_DESC = str(_CINE.get("_风格说明", ""))
_MANGA_KEYWORDS = ("水墨", "二次元", "动漫", "漫画", "CG", "韩漫", "日漫", "国漫", "厚涂")
_IS_MANGA = any(k in _STYLE_RAW for k in _MANGA_KEYWORDS)
if _IS_MANGA:
    # 漫剧风格:摄影机/胶片参数被风格描述覆盖(见提示词模板.md 规则)
    CINE_LINE = (f"{_STYLE_RAW}风格, {_STYLE_DESC}, 8K超高清画质, 24fps, "
                 "无画面畸变、无穿帮、无多余空镜、动作自然")
else:
    CINE_LINE = (f"{_STYLE_RAW}风格, 8K超高清电影级画质, {_CINE.get('摄影机','')}, "
                 f"{_CINE.get('镜头','')}, {_CINE.get('胶片','')}, 24fps院线标准帧率, "
                 "无画面畸变、无穿帮、无多余空镜、动作自然")

# 镜头参数与画面定调来自 settings.json(见上方 _load_settings)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def http_get(url, timeout=600, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_json(url, data=None, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------- Stage 1: LLM 写剧本(两阶段:先列梗概,再逐镜展开,规避量化模型长输出不稳定) ----------
DIRECTIONS_PROMPT = """你是编剧。针对下面【小说】,设计 3 个不同的影视改编方向(侧重点各异,如:轻喜剧向/正剧向/悬念向),并选出最适合短视频动画化的一个。只输出 JSON,字段全部用字符串:
{"directions": ["方向1: 50字内", "方向2: 50字内", "方向3: 50字内"], "best": 1}

【小说】:
"""

PHASE1_PROMPT = """你是编剧。按下面指定的【改编方向】,把【小说】改编为 {n_min}~{n_max} 个关键镜头的梗概列表,并提取主要人物。只输出 JSON,字段全部用字符串:
{{"title": "标题", "style": "统一美术风格(中文,20字内)", "characters": "主角名:外貌描述;其他人物:外貌描述", "scenes": ["镜头1:一句话梗概", "镜头2:一句话梗概"]}}

要求:镜头间情节连贯,覆盖铺垫→发展→高潮→结尾,重要转折要有过渡镜头。梗概每条约30字,共{n_min}~{n_max}条。

【镜头结构硬性要求】:
1. 第1镜必须是远景定场:大军决战氛围(旌旗蔽日/铁骑列阵/战云压城),交代环境后再进入人物
2. 两人对话的剧情以正反打为主:镜头在两人之间交替切换,一人一镜。例外:关键情绪对比时刻用双人同框(如斥候来报:一人镇定一人紧张,同框形成反差张力)
3. 关键情绪转折点安排面部特写镜头
4. 相邻镜头景别必须不同(近景↔远景交替),同一镜头只保留一个核心动作或信息点

【改编方向】:
{direction}

【小说】:
"""

PHASE2_PROMPT = """你是 AI 视频提示词专家。为下面这个镜头写视频生成参数,只输出 JSON:
{{"prompt_en": "英文画面提示词", "narration_zh": "中文旁白"}}

【旁白规范】(narration_zh 必须遵守):
1. 开头带情绪标签:如 [低沉] / [激昂] / [平静] / [惊讶] / [温柔] / [俏皮]
2. 20字内,口播风格:删连接词、短句、可有呼吸停顿(…)
3. 结尾或中途留悬念钩子

【分镜公式】(prompt_en 必须按此结构写,60词内):
运镜方式 + 景别 + 主体 + 核心动作 + 限制条件
- 运镜备选:固定机位(轻微呼吸感)/ 缓慢匀速向前推进 / 缓慢匀速向后拉开 / 水平横移 / 垂直摇摄 / 360°缓慢环绕 / 斯坦尼康跟拍
- 景别备选:特写(面部或手部)/ 近景(胸部以上)/ 中景(腰部以上)/ 全景(全身+环境)/ 远景(大环境)
- 限制条件:无画面畸变、无穿帮、无多余空镜、动作自然

【画面定调】(prompt_en 末尾必须原样附加这一行):
{cinema_line}

【连贯性铁律】:
1. 人物外观必须严格使用【人物档案】里的描述,与前后镜头完全一致
2. 画面内容必须承接【已完成镜头】的结尾状态,不得凭空跳场景(除非是明确的转场镜头,转场要有过渡画面)
3. 旁白必须与上一镜头的旁白逻辑衔接

【人物档案】:
{characters}

【已完成镜头】(画面+旁白):
{prev_scenes}

【当前镜头梗概】:
{summary}

小说背景(风格参考):{story}
统一风格:{style}
"""


def llm_chat(content, retries=8, model=None, think=None, num_predict=None):
    model = model or MODEL_FAST
    for i in range(retries):
        try:
            body = {
                "model": model, "stream": False, "format": "json",
                "keep_alive": "30m",   # 常驻加载:反复加载/卸载会把 ROCm 状态磨坏(2026-10-16 实测)
                # num_ctx 必须显式设!默认 2048,提示词一长就挤爆生成空间(截断)或直接 400
                "options": {"temperature": 0.6 + 0.05 * i, "top_p": 0.9, "num_ctx": 8192},
                "messages": [{"role": "user", "content": content}],
            }
            if think is not None:
                body["think"] = think
            if num_predict is not None:
                body["options"]["num_predict"] = num_predict
            resp = http_json(f"{OLLAMA}/api/chat", body)
            c = resp["message"]["content"].strip()
            if not c:
                print(f"  第{i+1}次返回空,重试...", flush=True)
                time.sleep(10)
                continue
            return c
        except Exception as e:
            print(f"  第{i+1}次出错 {e},重试...", flush=True)
            time.sleep(10)
    raise RuntimeError("LLM 多次返回空")


def extract_json(content):
    """从模型输出中提取 JSON:直接解析 -> 剥代码围栏 -> 正则首尾截取 -> 截断修补"""
    content = content.strip()
    for attempt in (content, content.strip("`").removeprefix("json").strip()):
        try:
            return json.loads(attempt)
        except Exception:
            pass
    import re
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
        # 截断修补:逐次补上缺失的右括号重试
        for tail in (']}]', ']}"}]', ']}', '}]', '}]}', '}'):
            try:
                return json.loads(m.group(0) + tail)
            except Exception:
                pass
    raise RuntimeError(f"无法从模型输出提取 JSON: {content[:400]!r}")


PLAN_FILE = os.path.join(BASE, "script_plan.json")


def stage_script():
    start_ollama()
    story = open(STORY, encoding="utf-8").read()
    print(f"故事长度: {len(story)} 字符", flush=True)

    # 阶段0:3 个改编方向 + 自评挑选(策略一)
    direction = ""
    dir_file = os.path.join(BASE, "directions.json")
    if os.path.exists(dir_file):
        dd = json.load(open(dir_file, encoding="utf-8"))
        direction = dd["directions"][dd.get("best", 1) - 1]
        print("阶段0: 复用已有改编方向:", direction, flush=True)
    else:
        print("阶段0: 设计 3 个改编方向...", flush=True)
        for attempt in range(6):
            try:
                dd = extract_json(llm_chat(DIRECTIONS_PROMPT + story, retries=3))
                if isinstance(dd.get("directions"), list) and len(dd["directions"]) >= 3:
                    break
                print(f"  第{attempt+1}次方向 schema 不合格,重试...", flush=True)
                dd = None
            except Exception as e:
                print(f"  第{attempt+1}次失败: {e}", flush=True)
                dd = None
            time.sleep(10)
        if not dd:
            raise RuntimeError("改编方向生成失败")
        import re
        m = re.search(r"\d+", str(dd.get("best", "1")))
        best = int(m.group(0)) if m else 1
        dd["best"] = best if 1 <= best <= len(dd["directions"]) else 1
        direction = dd["directions"][dd["best"] - 1]
        json.dump(dd, open(dir_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("  方向1:", dd["directions"][0], flush=True)
        print("  方向2:", dd["directions"][1], flush=True)
        print("  方向3:", dd["directions"][2], flush=True)
        print(f"  ★ 自评最优: 方向{dd['best']} — {direction}", flush=True)

    # 阶段1:分镜梗概(支持断点:已有 plan 就复用;schema 不合格会重试)
    if os.path.exists(PLAN_FILE):
        plan = json.load(open(PLAN_FILE, encoding="utf-8"))
        print("阶段1: 复用已有分镜梗概", flush=True)
    else:
        print("阶段1: 分镜梗概...", flush=True)
        plan = None
        for attempt in range(8):
            try:
                p = extract_json(llm_chat(PHASE1_PROMPT.format(direction=direction, n_min=SCENES_MIN, n_max=SCENES_MAX) + story, retries=3))
                raw = p.get("scenes", [])
                if isinstance(raw, list) and raw:
                    # 扁平字符串镜头 -> 结构化 {index, summary_zh}
                    p["scenes"] = [{"index": i + 1, "summary_zh": str(s)} for i, s in enumerate(raw[:SCENES_MAX])]
                    p["global_style"] = str(p.get("style", "动漫风格"))
                    plan = p
                    break
                print(f"  第{attempt+1}次 schema 不合格(缺 scenes),重试...", flush=True)
            except Exception as e:
                print(f"  第{attempt+1}次失败: {e}", flush=True)
            time.sleep(10)
        if not plan:
            raise RuntimeError("分镜规划失败(8 次尝试)")
        json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    title = plan.get("title", "未命名")
    style = plan.get("global_style", "cinematic")
    print(f"  《{title}》 {len(plan['scenes'])} 个镜头, 风格: {style}", flush=True)

    # 人物档案(扁平字符串)
    characters = str(plan.get("characters", "") or "(未提供)")
    print(f"  人物档案: {characters}", flush=True)

    # 阶段2:逐镜展开(链式上下文;支持断点:已有 script.json 里完成的镜头跳过)
    print("阶段2: 逐镜展开...", flush=True)
    out_scenes = []
    if os.path.exists(SCRIPT):
        out_scenes = json.load(open(SCRIPT, encoding="utf-8")).get("scenes", [])
    done_idx = {s["index"] for s in out_scenes}
    for sc in plan["scenes"]:
        idx = sc["index"]
        if idx in done_idx:
            print(f"  镜头{idx}: 已存在,跳过", flush=True)
            continue
        prev_text = "\n".join(
            f"- 镜头{s['index']}: 画面({s['prompt_en']}) 旁白({s['narration_zh']})"
            for s in sorted(out_scenes, key=lambda x: x["index"])[-2:]) or "(无,这是第一个镜头)"
        msg = PHASE2_PROMPT.format(
            characters=characters, prev_scenes=prev_text,
            summary=sc.get("summary_zh", ""),
            story=story[:1200], style=style, cinema_line=CINE_LINE)
        detail = None
        for retry_i in range(4):
            d = extract_json(llm_chat(msg))
            nar = (d.get("narration_zh") or d.get("novel_background")
                   or d.get("narration") or "")
            # 防御:提示词过长/旁白过长 = 模型复述原文,重试
            if d.get("prompt_en") and nar and len(d["prompt_en"]) <= 600 and len(nar) <= 80:
                detail = d
                break
            print(f"  镜头{idx} 草稿异常(提示词{len(d.get('prompt_en',''))}字/旁白{len(nar)}字),重试...", flush=True)
            msg += "\n\n注意:禁止复述或翻译原文,只输出一个镜头的提示词和旁白。"
        if not detail:
            raise RuntimeError(f"镜头{idx}展开失败(4次尝试)")
        out_scenes.append({"index": idx, "prompt_en": detail["prompt_en"], "narration_zh": nar})
        out_scenes.sort(key=lambda s: s["index"])
        json.dump({"title": title, "global_style": style, "characters": characters, "scenes": out_scenes},
                  open(SCRIPT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  镜头{idx}: {detail['prompt_en'][:60]}... | {nar}", flush=True)

    print(f"剧本完成: {len(out_scenes)} 个镜头 -> {SCRIPT}", flush=True)


# ---------- 即梦引擎(火山方舟 Ark API) ----------
def _jm_headers():
    return {"Authorization": f"Bearer {_JIMENG.get('api_key', '')}", "Content-Type": "application/json"}


def jimeng_submit(prompt, seed, duration=None):
    body = {
        "model": _JIMENG.get("model", "doubao-seedance-2-0-260128"),
        "content": [{"type": "text", "text": prompt}],
        "parameters": {
            "resolution": _JIMENG.get("resolution", "720p"),
            "duration": duration or _JIMENG.get("duration", 5),
            "ratio": _JIMENG.get("ratio", "adaptive"),
            "watermark": False,
            "seed": seed,
        },
    }
    req = urllib.request.Request(f"{JIMENG_BASE}/contents/generations/tasks",
                                 json.dumps(body).encode(), _jm_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    return d["id"]


def jimeng_poll(task_id, timeout_s=1200):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(10)
        req = urllib.request.Request(f"{JIMENG_BASE}/contents/generations/tasks/{task_id}",
                                     headers=_jm_headers())
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        status = d.get("status", "")
        if status == "succeeded":
            video_url = d.get("content", {}).get("video_url", "")
            if video_url:
                return video_url
            raise RuntimeError(f"即梦任务成功但无视频地址: {str(d)[:200]}")
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"即梦任务失败: {str(d)[:300]}")
        print(f"  即梦任务 {task_id[:8]} 状态: {status}", flush=True)
    raise TimeoutError("即梦任务超时")


def jimeng_download(url, path):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)


def _generate_jimeng(script, state):
    """即梦引擎:逐镜提交/轮询/下载,产物与本地引擎同构(clips/clip_XX.mp4)"""
    os.makedirs(CLIPS_DIR, exist_ok=True)
    for scene in script["scenes"]:
        idx = scene["index"]
        if idx in state["done"]:
            print(f"镜头{idx}: 已完成,跳过", flush=True)
            continue
        clip = os.path.join(CLIPS_DIR, f"clip_{idx:02d}.mp4")
        if os.path.exists(clip) and os.path.getsize(clip) > 0:
            state["done"].append(idx)
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            continue
        prompt = scene["prompt_en"] + ", " + CINE_LINE
        seed = SEED_BASE + idx * 37
        print(f"=== 镜头{idx}/{len(script['scenes'])} 即梦生成中 (seed={seed}) ===", flush=True)
        t0 = time.time()
        try:
            task_id = jimeng_submit(prompt, seed)
            url = jimeng_poll(task_id)
            jimeng_download(url, clip)
        except Exception as e:
            print(f"镜头{idx} 失败: {str(e)[:150]},稍后重试即可续跑", flush=True)
            raise
        state["done"].append(idx)
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"镜头{idx} 完成 ({time.time()-t0:.0f}s): {clip}", flush=True)
    print(f"即梦生成完成: {state['done']}", flush=True)


# ---------- Stage 2: 逐镜头生成视频 ----------
def build_workflow(scene, seed, start_image=None, w=None, h=None, frames=None, prefix=None):
    prompt = scene["prompt_en"] + ", " + CINE_LINE
    w = w or W
    h = h or H
    frames = frames or scene.get("frames") or FRAMES   # 过渡镜用自身短时长
    prefix = prefix or f"scene_{scene['index']:02d}"
    wf = {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp16.safetensors", "type": "wan", "device": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 0], "text": ""}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": w, "height": h, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
              "latent_image": ["7", 0], "seed": seed, "steps": STEPS, "cfg": CFG_V, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["8", 0], "vae": ["3", 0], "tile_size": 256, "overlap": 64, "temporal_size": 32, "temporal_overlap": 4}},
        "10": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["9", 0], "filename_prefix": prefix, "fps": 24,
               "lossless": False, "quality": 90, "method": "default"}},
    }
    if start_image:
        # 图生视频:角色参考图锁定形象
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
        wf["7"]["inputs"]["start_image"] = ["11", 0]
    return wf


def wait_prompt(prompt_id, scene_index, timeout_s=2400):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(15)
        try:
            hist = http_json(f"{COMFY}/history/{prompt_id}", timeout=30)
        except Exception:
            continue
        if prompt_id not in hist:
            continue
        entry = hist[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = status.get("messages", [])
            err = next((m[1].get("exception_message", "") for m in msgs if m[0] == "execution_error"), "unknown")
            raise RuntimeError(f"镜头{scene_index}生成失败: {err[:200]}")
        if "outputs" in entry:
            for node_id, outs in entry["outputs"].items():
                if "images" in outs and outs["images"]:
                    return os.path.join(OUTPUT_DIR, outs["images"][0]["filename"])
    raise TimeoutError(f"镜头{scene_index}超时")


def stage_generate():
    stop_ollama()
    script = json.load(open(SCRIPT, encoding="utf-8"))
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"done": []}
    if ENGINE == "jimeng":
        _generate_jimeng(script, state)
        return
    os.makedirs(BASE, exist_ok=True)
    # 角色参考图:优先用 assets 里已确认的 char_ref.png;否则第一镜完成后从首帧提取
    char_ref = None
    first_index = min(s["index"] for s in script["scenes"])
    confirmed_ref = os.path.join(ASSETS_DIR, "char_ref.png")
    if os.path.exists(confirmed_ref):
        import shutil
        shutil.copy(confirmed_ref, r"E:\ai\ComfyUI_windows_portable\ComfyUI\input\char_ref.png")
        char_ref = "char_ref.png"
        print(f"使用已确认的角色图: {confirmed_ref}", flush=True)

    for scene in script["scenes"]:
        idx = scene["index"]
        if idx in state["done"]:
            print(f"镜头{idx}: 已完成,跳过")
            continue
        # 关键:每个镜头前重启 ComfyUI。
        # ROCm 下模型常驻后第二个及以后的生成会产出单帧坏结果,全新进程每次首生成都是好的。
        stop_comfy()
        start_comfy()
        time.sleep(5)
        seed = SEED_BASE + idx * 37
        mode = "图生视频" if char_ref else "文生视频"
        print(f"\n=== 镜头{idx}/{len(script['scenes'])} 生成中 ({mode}, seed={seed}, 预计8-10分钟) ===")
        t0 = time.time()
        payload = {"prompt": build_workflow(scene, seed, start_image=char_ref), "client_id": "story2video"}
        r = http_json(f"{COMFY}/prompt", payload, timeout=60)
        if "node_errors" in r and r["node_errors"]:
            raise RuntimeError(f"工作流校验失败: {r['node_errors']}")
        webp = wait_prompt(r["prompt_id"], idx, timeout_s=3600)
        state["done"].append(idx)
        json.dump(state, open(STATE, "w", encoding="utf-8"))
        print(f"镜头{idx} 完成 ({time.time()-t0:.0f}s): {webp}")
        if idx == first_index and not char_ref:
            # 提取首帧作为角色参考图
            try:
                from PIL import Image
                im = Image.open(webp)
                im.seek(0)
                im.convert("RGB").save(r"E:\ai\ComfyUI_windows_portable\ComfyUI\input\char_ref.png")
                char_ref = "char_ref.png"
                print(f"  已提取角色参考图: {char_ref}")
            except Exception as e:
                print(f"  角色参考图提取失败(继续文生视频): {e}")
    print(f"\n全部镜头完成: {state['done']}")


# ---------- Stage 2b: 27B 重写润色(Lumen 草稿 -> 27B 精修) ----------
REWRITE_PROMPT = """你是顶尖短剧编剧。为下面这个镜头做创作级改写(不是简单润色),只输出 JSON:
{{"prompt_en": "英文画面提示词", "narration_zh": "中文旁白"}}

【改写目标】(每个镜头都必须显著超越原稿):
1. prompt_en:英文 60 词内,严格按分镜公式:运镜 + 景别 + 主体 + 核心动作 + 人物表情 + 限制条件。必须有明确的人物表情(如 furrowed brows 紧锁眉头 / eyes sharp as blades 目光如刀)和肢体动作

【导演规则】(prompt_en 必须遵守,制片人定稿):
1. **景别与内容必须严格遵循【本镜头梗概】的设定,禁止擅自把远景改成特写或改变画面焦点**(2026-10-17 制片人驳回教训)
2. 对话镜头以正反打为主:本镜头只拍一个说话人,与相邻镜头的人交替。例外:关键情绪对比时刻用双人同框(two-shot),凸显两人反应的对比反差
3. 关键情绪转折点用面部特写(extreme close-up on face / hands)
4. 相邻镜头景别必须不同:近景(情绪)与远景(环境)交替,禁止连续同景别
5. 一镜一动作:每个镜头只保留一个核心动作或信息点,删除与前后镜头重复的内容
2. narration_zh:30~40 字,必须包含以下至少两项:人物对白(如"他沉声道:死守铁线河")、人物表情动作描写(如"拇指摩挲扳指,目光沉如铁")、战争紧迫感要素(敌军距离/天亮时限/伤亡数字/突发危机)。开头带情绪标签,情绪要多样化,优先 [低沉] [激昂] [悲壮] [紧张],避免连续两个镜头同一种情绪
3. 战争紧迫感:每镜都要有"正在逼近的威胁"的具体压迫感,不要空泛的"生死未卜"
4. 运镜要变化:相邻镜头避免同一种运镜方式
5. 人物外貌严格保持【人物档案】,剧情承接【已完成镜头】的结尾状态

【人物档案】:
{characters}

【本镜头梗概】(景别与内容焦点必须遵守,见导演规则第1条):
{summary}

【已完成镜头】(画面+旁白):
{prev_scenes}

【原镜头】:
画面提示词: {prompt_en}
旁白: {narration_zh}
统一风格: {style}

【小说背景】:
{story}
"""


def stage_rewrite():
    start_ollama()
    script = json.load(open(SCRIPT, encoding="utf-8"))
    story = open(STORY, encoding="utf-8").read()
    style = script.get("global_style", "cinematic")
    characters = script.get("characters", "")
    # 梗概景别锚点(2026-10-17:27B 改写曾把远景全改成特写,必须锁景别)
    plan_summary = {}
    if os.path.exists(PLAN_FILE):
        try:
            plan_summary = {s["index"]: s.get("summary_zh", "") for s in json.load(open(PLAN_FILE, encoding="utf-8")).get("scenes", [])}
        except Exception:
            pass
    changed = False
    for scene in script["scenes"]:
        idx = scene["index"]
        if scene.get("polished"):
            print(f"镜头{idx}: 已润色,跳过", flush=True)
            continue
        prev_text = "\n".join(
            f"- 镜头{s['index']}: 画面({s['prompt_en'][:80]}) 旁白({s['narration_zh']})"
            for s in sorted(script["scenes"], key=lambda x: x["index"])[:idx]) or "(无,这是第一个镜头)"
        msg = REWRITE_PROMPT.format(
            characters=characters or "(与已完成的镜头保持一致)",
            summary=plan_summary.get(idx, "(未提供,按原镜头内容)"),
            prev_scenes=prev_text,
            prompt_en=scene["prompt_en"], narration_zh=scene["narration_zh"], style=style,
            story=story[:1500])
        detail = None
        for retry_i in range(4):
            try:
                d = extract_json(llm_chat(msg, retries=5, model=MODEL_MAIN,
                                          think=False, num_predict=2500))
                nar = (d.get("narration_zh") or d.get("novel_background") or "")
                if d.get("prompt_en") and nar and len(d["prompt_en"]) <= 700 and len(nar) <= 120:
                    detail = d
                    break
                print(f"  镜头{idx} 改写异常(长度),重试...", flush=True)
            except Exception as e:
                print(f"  镜头{idx} 改写失败({str(e)[:60]}),重试...", flush=True)
                if retry_i == 1:
                    # 连续两次失败 = 模型常驻后状态劣化,重启 Ollama 清状态
                    print("  重启 Ollama 清除劣化状态...", flush=True)
                    stop_ollama()
                    start_ollama()
            time.sleep(8)
        if not detail:
            raise RuntimeError(f"镜头{idx}改写失败(4次尝试)")
        scene["prompt_en"] = detail["prompt_en"]
        scene["narration_zh"] = nar
        scene["polished"] = True
        changed = True
        json.dump(script, open(SCRIPT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"镜头{idx} 改写: {nar[:35]} | {detail['prompt_en'][:45]}...", flush=True)
    if not changed:
        print("所有镜头已润色", flush=True)


# ---------- Stage 2c: 角色图/场景图资产(用户确认后锁定形象) ----------

ASSET_CHARS_PROMPT = """把下面的人物档案转成英文定妆照提示词,只输出 JSON:
{{"chars": [{{"name": "中文名", "prompt": "Character design sheet prompt in English, 30词内,含外貌服装姿态"}}]}}

硬性要求:人物档案里的【每个】角色都必须有一条,一个都不能少。

人物档案:
{characters}
"""

ASSET_LOCATIONS_PROMPT = """从下面镜头的场景中提取 3 个最主要的场景,只输出 JSON:
{{"locations": [{{"name": "中文场景名", "prompt": "Environment design sheet prompt in English, 30词内,宽景无人"}}]}}

镜头梗概:
{summaries}
"""


def stage_assets():
    start_ollama()
    script = json.load(open(SCRIPT, encoding="utf-8"))
    os.makedirs(ASSETS_DIR, exist_ok=True)
    characters = str(script.get("characters", ""))
    summaries = "; ".join(f"镜头{s['index']}:{s.get('summary_zh', s['narration_zh'])}" for s in script["scenes"])

    chars, locs = [], []
    if characters.strip():
        try:
            chars = extract_json(llm_chat(ASSET_CHARS_PROMPT.format(characters=characters), retries=3)).get("chars", [])[:4]
        except Exception as e:
            print(f"角色提示词转换失败: {e}", flush=True)
    try:
        locs = extract_json(llm_chat(ASSET_LOCATIONS_PROMPT.format(summaries=summaries), retries=3)).get("locations", [])[:3]
    except Exception as e:
        print(f"场景提示词转换失败: {e}", flush=True)

    assets = [("char", c["name"], c["prompt"]) for c in chars] + [("scene", l["name"], l["prompt"]) for l in locs]
    # LLM 提示词转换已完成,整进程停掉 Ollama,把干净的 GPU 让给 ComfyUI
    stop_ollama()
    from PIL import Image, ImageStat

    # 图片引擎选择:SDXL + 水墨 LoRA(高质量静态图)优先,Wan 抽帧兜底
    sdxl_ckpt = r"E:\ai\ComfyUI_windows_portable\ComfyUI\models\checkpoints\sd_xl_base_1.0.safetensors"
    sdxl_lora = r"E:\ai\ComfyUI_windows_portable\ComfyUI\models\loras\Chinese_Ink_Painting_Lora_SDXL.safetensors"
    use_sdxl = os.path.exists(sdxl_ckpt)
    print(f"图片引擎: {'SDXL+水墨LoRA' if use_sdxl else 'Wan 抽帧(兜底)'}", flush=True)

    if not use_sdxl:
        # Wan 暖机 + 抽帧路径(兜底)
        stop_comfy()
        start_comfy()
        time.sleep(3)
        warm = {"index": 0, "prompt_en": "a single red apple on white background, studio lighting"}
        wr = http_json(f"{COMFY}/prompt",
                       {"prompt": build_workflow(warm, 7, w=320, h=320, frames=17, prefix="warmup"),
                        "client_id": "story2video"}, timeout=60)
        wait_prompt(wr["prompt_id"], "warmup", timeout_s=900)
        print("GPU 暖机完成", flush=True)

    def build_sdxl_workflow(prompt, seed, prefix):
        use_ink = bool(_CINE.get("使用水墨LoRA", False))
        if use_ink:
            pos = f"Chinese Ink, {prompt}, ink wash painting, traditional media, liujiyou style, masterful composition, high detail"
            neg = "photo, 3d render, cartoon, color painting, text, watermark, low quality, blurry"
        else:
            pos = f"{_STYLE_DESC}, {prompt}, masterpiece, best quality, highly detailed, 8k"
            neg = ("ink wash, black and white, photo, realistic, text, watermark, low quality, blurry, "
                   "bad anatomy, extra heads, two heads, extra limbs, extra arms, deformed face, "
                   "disfigured, bad hands, mutated, duplicate person")
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": pos}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": neg}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1536, "batch_size": 1}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
        }
        if use_ink:
            wf["2"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "Chinese_Ink_Painting_Lora_SDXL.safetensors", "strength_model": 0.85}}
            model_src = ["2", 0]
        else:
            model_src = ["1", 0]
        wf["6"] = {"class_type": "KSampler", "inputs": {"model": model_src, "positive": ["3", 0], "negative": ["4", 0],
                  "latent_image": ["5", 0], "seed": seed, "steps": 30, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}}
        return wf

    for i, (kind, name, prompt) in enumerate(assets):
        safe = re.sub(r"[^\w一-鿿-]", "_", name)
        png = os.path.join(ASSETS_DIR, f"{kind}_{safe}.png")
        # 质量闸门:平坦灰图 = 生成劣化,自动换种子重试
        if os.path.exists(png):
            st = ImageStat.Stat(Image.open(png).convert("RGB"))
            if sum(st.stddev) > 30:
                print(f"{kind}_{safe}: 已存在,跳过", flush=True)
                continue
            print(f"{kind}_{safe}: 已存在但是灰图,重生成", flush=True)
            os.remove(png)
        for attempt in range(3):
            stop_comfy()
            start_comfy()
            time.sleep(5)
            seed = SEED_BASE + (sum(ord(c) for c in name) % 900) + attempt * 97   # 同角色固定种子,保持形象一致
            est = "约1分钟" if use_sdxl else "约3分钟"
            print(f"=== {kind}_{safe} 设计稿生成中 (第{attempt+1}次, {est}) ===", flush=True)
            if use_sdxl:
                payload = {"prompt": build_sdxl_workflow(prompt, seed, f"asset_{i:02d}"), "client_id": "story2video"}
            else:
                fake = {"index": 0, "prompt_en": prompt}
                payload = {"prompt": build_workflow(fake, seed, w=480, h=832, frames=33, prefix=f"asset_{i:02d}"),
                           "client_id": "story2video"}
            r = http_json(f"{COMFY}/prompt", payload, timeout=60)
            if "node_errors" in r and r["node_errors"]:
                raise RuntimeError(f"资产工作流校验失败: {r['node_errors']}")
            webp = wait_prompt(r["prompt_id"], f"{kind}_{safe}", timeout_s=1800)
            im = Image.open(webp)
            im.seek(0)
            im.convert("RGB").save(png)
            st = ImageStat.Stat(Image.open(png).convert("RGB"))
            if sum(st.stddev) > 30:
                print(f"{kind}_{safe}: 已保存 -> {png}", flush=True)
                break
            print(f"{kind}_{safe}: 灰图,换种子重试...", flush=True)
            os.remove(png)
        else:
            raise RuntimeError(f"{kind}_{safe} 三次生成全是灰图")
    print(f"\n资产完成: {len(assets)} 张,位于 {ASSETS_DIR},请确认", flush=True)


# ---------- Stage 2d: 过渡镜头(剧情呼吸感) ----------
TRANSITION_PROMPT = """你是剪辑师。下面是一个短视频的镜头列表,剧情节奏偏紧凑。请在最需要"呼吸感"的 2~3 个位置插入纯环境过渡空镜(无人、无对白,只拍环境氛围,承接前后镜头的气氛)。只输出 JSON:
{{"transitions": [{{"after": 在哪个镜头序号之后插入(数字), "prompt_en": "英文空镜提示词,25词内,环境+氛围+运镜"}}]}}

镜头列表:
{scene_list}
"""


def stage_transitions():
    start_ollama()
    script = json.load(open(SCRIPT, encoding="utf-8"))
    scenes = script["scenes"]
    if any(s.get("transition") for s in scenes):
        print("过渡镜头已插入,跳过", flush=True)
        return
    scene_list = "\n".join(f"镜头{s['index']}: {s['narration_zh'][:40]} | {s['prompt_en'][:60]}" for s in scenes)
    tr = None
    for attempt in range(4):
        try:
            tr = extract_json(llm_chat(TRANSITION_PROMPT.format(scene_list=scene_list), retries=3))
            if isinstance(tr.get("transitions"), list) and tr["transitions"]:
                break
        except Exception as e:
            print(f"  第{attempt+1}次失败: {str(e)[:60]}", flush=True)
        time.sleep(8)
    if not tr or not tr.get("transitions"):
        print("过渡镜头生成失败,跳过(不影响主线)", flush=True)
        return
    # 插入 + 重排序号(过渡镜 49 帧≈2秒,无旁白)
    for t in tr["transitions"][:3]:
        after = int(t.get("after", 0))
        pos = next((i for i, s in enumerate(scenes) if s["index"] == after), len(scenes) - 1)
        scenes.insert(pos + 1, {
            "index": 0, "prompt_en": t["prompt_en"], "narration_zh": "",
            "frames": 49, "transition": True, "polished": True})
        print(f"  在镜头{after}后插入过渡镜: {t['prompt_en'][:50]}...", flush=True)
    for i, s in enumerate(scenes):
        s["index"] = i + 1
    json.dump(script, open(SCRIPT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"过渡镜头完成,共 {len(scenes)} 个镜头 -> {SCRIPT}", flush=True)


# ---------- Stage 3a: 旁白配音 (edge-tts 微软神经音色 + 情绪标签) ----------
EMOTION_MAP = {
    # (rate, pitch) — edge-tts 要求显式正负号,pitch 不支持半音(st),用 Hz 近似
    "低沉": ("-15%", "-20Hz"), "悲壮": ("-15%", "-20Hz"), "沉重": ("-15%", "-20Hz"), "沧桑": ("-10%", "-10Hz"),
    "激昂": ("+10%", "+10Hz"), "愤怒": ("+10%", "+10Hz"), "紧张": ("+5%", "+0Hz"), "坚定": ("+5%", "+0Hz"),
    "惊讶": ("+8%", "+10Hz"), "疑问": ("+8%", "+0Hz"), "俏皮": ("+15%", "+20Hz"), "温柔": ("-5%", "-5Hz"),
    "平静": ("+5%", "+0Hz"), "叙述": ("+5%", "+0Hz"), "悲凉": ("-20%", "-20Hz"),
}

EMOTION_TAG_PROMPT = """给下面这句旁白做两件事:1)清理掉"25字""20字内"等字数说明和指令杂质,只保留口播台词;2)标注情绪。只输出 JSON:
{{"emotion": "低沉/激昂/平静/惊讶/温柔/俏皮/悲壮/疑问", "text": "清理后的旁白"}}

旁白:{narration}
"""


def stage_audio():
    import imageio_ffmpeg
    import edge_tts
    if not TTS_ON:
        print("旁白已禁用(settings.json 配音.启用旁白=false),跳过", flush=True)
        return
    stop_comfy()   # 释放 GPU 给 Ollama(情绪标签推断需要 LLM)
    start_ollama()
    voice = SET.get("配音", {}).get("音色", "zh-CN-XiaoxiaoNeural")
    offset = int(SET.get("配音", {}).get("语速偏移", 0))
    script = json.load(open(SCRIPT, encoding="utf-8"))
    os.makedirs(AUDIO_DIR, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for scene in script["scenes"]:
        idx = scene["index"]
        m4a = os.path.join(AUDIO_DIR, f"audio_{idx:02d}.m4a")
        if os.path.exists(m4a):
            print(f"旁白{idx}: 已存在,跳过", flush=True)
            continue
        nar = scene["narration_zh"]
        if not nar.strip():
            print(f"镜头{idx}: 过渡空镜,无旁白", flush=True)
            continue
        # 解析情绪标签 [xxx] 或【xxx】,缺失则 LLM 推断
        m = re.match(r"^\s*[\[【](.+?)[\]】]\s*(.*)$", nar)
        if m:
            emotion, text = m.group(1), m.group(2)
        else:
            emotion = "平静"
            text = nar
            try:
                r = extract_json(llm_chat(EMOTION_TAG_PROMPT.format(narration=nar), retries=3))
                emotion = r.get("emotion", "平静")
                text = r.get("text", nar)
            except Exception:
                pass
        base_rate, pitch = EMOTION_MAP.get(emotion, ("+5%", "+0Hz"))
        rate_val = int(base_rate[:-1]) + offset
        rate = f"{'+' if rate_val >= 0 else ''}{rate_val}%"
        mp3 = os.path.join(AUDIO_DIR, f"audio_{idx:02d}.mp3")
        try:
            import asyncio
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(mp3))
        except Exception as e:
            print(f"旁白{idx}: edge-tts 失败({e}),跳过", flush=True)
            continue
        subprocess.run([ffmpeg, "-y", "-i", mp3, "-c:a", "aac", "-b:a", "96k", m4a], capture_output=True)
        print(f"旁白{idx}[{emotion} {rate}]: {text[:25]}", flush=True)


# ---------- Stage 3: 转mp4 + 混音 + 拼接 ----------
def stage_concat():
    import imageio.v2 as imageio
    import imageio_ffmpeg
    import subprocess, glob
    script = json.load(open(SCRIPT, encoding="utf-8"))
    os.makedirs(CLIPS_DIR, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    clips = []
    for scene in sorted(script["scenes"], key=lambda s: s["index"]):
        idx = scene["index"]
        webp = glob.glob(os.path.join(OUTPUT_DIR, f"scene_{idx:02d}_*.webp"))
        if not webp:
            print(f"镜头{idx}: 没有找到 webp,跳过")
            continue
        webp = sorted(webp)[-1]
        mp4 = os.path.join(CLIPS_DIR, f"clip_{idx:02d}.mp4")
        if os.path.exists(mp4) and os.path.getsize(mp4) == 0:
            os.remove(mp4)   # 清理历史失败留下的空文件
        if not os.path.exists(mp4):
            # imageio 流式逐帧转换(ffmpeg 的 webp 解复用器不认动图;mimread 有 256MB 上限)
            reader = imageio.get_reader(webp)
            writer = imageio.get_writer(mp4, fps=24, codec="libx264", quality=8)
            n = 0
            for frame in reader:
                writer.append_data(frame[:, :, :3])
                n += 1
            writer.close()
            reader.close()
            print(f"镜头{idx}: webp -> mp4 ({n}帧)")
        m4a = os.path.join(AUDIO_DIR, f"audio_{idx:02d}.m4a")
        if os.path.exists(m4a):
            mixed = os.path.join(CLIPS_DIR, f"clipav_{idx:02d}.mp4")
            if not os.path.exists(mixed):
                subprocess.run([ffmpeg, "-y", "-i", mp4, "-i", m4a,
                                "-c:v", "copy", "-c:a", "aac", "-shortest", mixed],
                               capture_output=True, check=True)
                print(f"镜头{idx}: 混入旁白")
            clips.append(mixed)
        else:
            clips.append(mp4)
    if len(clips) < 2:
        print("镜头不足,无法拼接")
        return
    listfile = os.path.join(BASE, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.replace(chr(39), '')}'\n")
    final = os.path.join(BASE, "final_story.mp4")
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", final], check=True)
    print(f"\n=== 成片完成: {final} (共{len(clips)}个镜头,含旁白) ===")


# ---------- 服务器生命周期管理(LLM 和视频生成不能同时占 GPU) ----------
OLLAMA_EXE = r"C:\Users\<你的Windows用户名>\AppData\Local\Programs\Ollama\ollama.exe"
OLLAMA_ENV = {
    "OLLAMA_MODELS": r"F:\ollama",
    "ROCBLAS_USE_HIPBLASLT": "0",
    "ROCBLAS_TENSILE_LIBPATH": r"C:\Users\<你的Windows用户名>\AppData\Local\Programs\Ollama\lib\ollama\rocm_v7_1\rocblas\library",
}


def ollama_running():
    try:
        urllib.request.urlopen(f"{OLLAMA}/api/version", timeout=3)
        return True
    except Exception:
        return False


def stop_ollama():
    """整进程停掉 Ollama:模型卸载后 ROCm 残留会挂死新起的 ComfyUI,必须杀干净"""
    if not ollama_running():
        return
    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
    time.sleep(3)
    print("Ollama 已停止,GPU 让给 ComfyUI", flush=True)


def start_ollama():
    if ollama_running():
        print("Ollama 已在运行", flush=True)
        return
    env = dict(os.environ)
    env.update(OLLAMA_ENV)
    subprocess.Popen([OLLAMA_EXE, "serve"], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(30):
        time.sleep(2)
        if ollama_running():
            print("Ollama 已启动(带 ROCm 修复环境变量)", flush=True)
            return
    raise RuntimeError("Ollama 启动失败")


def comfy_running():
    try:
        urllib.request.urlopen(f"{COMFY}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def stop_comfy():
    """杀掉占用 8188 端口的 ComfyUI 进程,释放 GPU 给 Ollama"""
    if not comfy_running():
        return
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if ":8188" in line and "LISTENING" in line:
            pid = line.split()[-1]
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            print(f"ComfyUI 已停止 (PID {pid}),GPU 让给 LLM")
    time.sleep(3)


def start_comfy():
    """启动 ComfyUI 并等待就绪"""
    if comfy_running():
        print("ComfyUI 已在运行")
        return
    subprocess.Popen(
        [r"E:\ai\ComfyUI_windows_portable\python_embeded\python.exe",
         r"E:\ai\ComfyUI_windows_portable\ComfyUI\main.py",
         "--listen", "127.0.0.1", "--port", "8188"],
        cwd=r"E:\ai\ComfyUI_windows_portable",
        creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(90):
        time.sleep(2)
        if comfy_running():
            print("ComfyUI 已启动")
            return
    raise RuntimeError("ComfyUI 启动失败")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "new":
        # python pipeline.py new <故事名> [源故事txt路径]
        name = sys.argv[2] if len(sys.argv) > 2 else input("故事名: ")
        d = os.path.join(STORIES_ROOT, name)
        os.makedirs(os.path.join(d, "assets"), exist_ok=True)
        os.makedirs(os.path.join(d, "clips"), exist_ok=True)
        os.makedirs(os.path.join(d, "audio"), exist_ok=True)
        src = sys.argv[3] if len(sys.argv) > 3 else r"E:\ai\story.txt"
        import shutil
        shutil.copy(src, os.path.join(d, "story.txt"))
        SET["当前故事"] = name
        json.dump(SET, open(SETTINGS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"故事 '{name}' 已创建并切换为当前故事 -> {d}")
        sys.exit(0)
    if cmd == "list":
        if os.path.exists(STORIES_ROOT):
            for n in sorted(os.listdir(STORIES_ROOT)):
                mark = " <- 当前" if n == _active else ""
                print(n + mark)
        sys.exit(0)
    if cmd in ("script", "all"):
        stop_comfy()
        stage_script()
    if cmd in ("rewrite", "all") and USE_REWRITE:
        stop_comfy()
        stage_rewrite()
    if cmd in ("assets", "all"):
        stop_comfy()
        stage_assets()
    if cmd in ("transitions", "all"):
        stop_comfy()
        stage_transitions()
    if cmd in ("generate", "all"):
        stage_generate()
    if cmd in ("audio", "all"):
        stage_audio()
    if cmd in ("concat", "all"):
        stage_concat()
