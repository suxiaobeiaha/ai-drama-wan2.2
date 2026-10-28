# -*- coding: utf-8 -*-
"""实验准备:从开源素材提取分镜表 + 即梦单镜测试"""
import json, os, re, sys

sys.path.insert(0, r"E:/ai/story2video")
import pipeline
from pipeline import jimeng_submit, jimeng_poll, jimeng_download, http_json

SRC = r"E:\ai\drama_ref\drama-skills-main\examples\creator-first\EP001\视频提示词.md"
OUT = r"E:\ai\实验\EP001对照"
SHOTS_JSON = os.path.join(OUT, "分镜表.json")


def extract_shots(n=6):
    """解析开源素材的视频提示词,提取前 n 镜:时长 + 可复制提示词"""
    text = open(SRC, encoding="utf-8").read()
    blocks = re.split(r"## MOTION-EP001-", text)[1:]
    shots = []
    for b in blocks[:n]:
        num = b[:3]
        m = re.search(r"时长:(\d+)s", b)
        dur = int(m.group(1)) if m else 4
        p = re.search(r"### 可复制提示词\s*\n> (.+?)\n(?=\n##|\Z)", b, re.S)
        prompt = p.group(1).strip() if p else ""
        shots.append({"id": f"SHOT-EP001-{num}", "duration": dur, "prompt": prompt})
    return shots


def main():
    os.makedirs(OUT, exist_ok=True)
    shots = extract_shots(6)
    json.dump(shots, open(SHOTS_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"分镜表已提取: {len(shots)} 镜 -> {SHOTS_JSON}")
    for s in shots:
        print(f"  {s['id']} {s['duration']}s | {s['prompt'][:60]}...")

    # 即梦单镜测试(第1镜)
    s = shots[0]
    print(f"\n=== 即梦单镜测试: {s['id']} ({s['duration']}s) ===", flush=True)
    task_id = jimeng_submit(s["prompt"], 12345, duration=s["duration"])
    print(f"任务已提交: {task_id[:12]}...", flush=True)
    url = jimeng_poll(task_id)
    test_out = os.path.join(OUT, f"测试_{s['id']}.mp4")
    jimeng_download(url, test_out)
    size = os.path.getsize(test_out)
    print(f"单镜测试成功! {test_out} ({size/1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
