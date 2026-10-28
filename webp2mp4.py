# -*- coding: utf-8 -*-
"""把 ComfyUI output 文件夹里的 webp 动图全部转成 mp4(微信可播放)。
流式逐帧转换,大动图(720p×121帧)不会爆内存。"""
import glob, os, sys
import imageio.v2 as imageio

folder = r"E:\ai\ComfyUI_windows_portable\ComfyUI\output"
files = glob.glob(os.path.join(folder, "*.webp"))
if not files:
    print("output 文件夹里没有 webp 文件")
    sys.exit(0)

ok = 0
for w in files:
    out = w[:-5] + ".mp4"
    if os.path.exists(out):
        print("已存在,跳过:", os.path.basename(out))
        continue
    try:
        reader = imageio.get_reader(w)
        n = reader.count_frames()
    except Exception as e:
        print("读取失败:", os.path.basename(w), e)
        continue
    if n is None or n <= 1:
        print("不是动图,跳过:", os.path.basename(w))
        continue
    try:
        writer = imageio.get_writer(out, fps=24, codec="libx264", quality=8)
        for frame in reader:
            writer.append_data(frame[:, :, :3])  # 去掉透明通道
        writer.close()
    except Exception as e:
        print("转换失败:", os.path.basename(w), e)
        try:
            os.remove(out)
        except OSError:
            pass
        continue
    finally:
        reader.close()
    print("转换成功:", os.path.basename(out), f"({n} 帧)")
    ok += 1

print(f"\n完成!共转换 {ok} 个文件,都在 output 文件夹里,直接发微信就能动了")
