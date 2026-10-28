# -*- coding: utf-8 -*-
"""从火山方舟 API 恢复已生成的视频(24小时内有效)"""
import json, os, sys, time, urllib.request

sys.path.insert(0, r"E:/ai/story2video")
import pipeline
from pipeline import jimeng_download

OUT = r"E:\ai\实验\EP001对照\恢复"
os.makedirs(OUT, exist_ok=True)

# 任务ID -> 推测的镜头映射(按时间线)
TASKS = {
    "cgt-20261026201848-6ndxr": "SHOT-001_c2",
    "cgt-20261026201847-4x5vg": "SHOT-001_c3",
    "cgt-20261026201847-sr4lt": "SHOT-001_c1",
    "cgt-20261026201451-m9nzd": "SHOT-002_c3",
    "cgt-20261026201450-9w2mz": "SHOT-002_c2",
    "cgt-20261026201449-pndx5": "SHOT-002_c1",
    "cgt-20261026202356-552cz": "SHOT-003_c2",
    "cgt-20261026202355-kwxs5": "SHOT-003_c3",
    "cgt-20261026202725-ngt7v": "SHOT-003_c1",
    "cgt-20261026213039-hpglr": "SHOT-001_重生成c1",
    "cgt-20261026213309-xd9z5": "本子不记债_SHOT-01_c1(运行中)",
}


def get_task(task_id):
    headers = {"Authorization": f"Bearer {pipeline._JIMENG.get('api_key','')}"}
    req = urllib.request.Request(
        f"https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}",
        headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    for tid, name in TASKS.items():
        out = os.path.join(OUT, f"{name}.mp4")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f"{name}: 已存在", flush=True)
            continue
        try:
            d = get_task(tid)
        except Exception as e:
            print(f"{name}: 查询失败 {str(e)[:60]}", flush=True)
            continue
        status = d.get("status", "")
        if status == "succeeded":
            url = d.get("content", {}).get("video_url", "")
            if url:
                jimeng_download(url, out)
                print(f"{name}: 已恢复 ({os.path.getsize(out)/1e6:.1f} MB)", flush=True)
            else:
                print(f"{name}: 成功但无视频地址", flush=True)
        elif status in ("queued", "running"):
            print(f"{name}: 状态 {status},稍后重试", flush=True)
        else:
            print(f"{name}: 状态 {status}", flush=True)


if __name__ == "__main__":
    main()
