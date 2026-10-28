# AI Drama Studio · 小说 → AI 动画短片流水线

> 输入一篇小说,全自动产出带情绪旁白的动画短片 mp4。
> 剧本、导演、资产、视频、配音、成片六大环节全自动,视频引擎可插拔。

## 能力一览

| 环节 | 实现 |
|---|---|
| 编剧 | 三方向自评选优 → 两阶段编剧 → 27B 创作级改写(对白/情绪/紧迫感) |
| 导演 | 导演规则体系:开场定场/对话正反打/情绪特写/景别交替/过渡空镜 |
| 资产 | SDXL 高清定妆图(1024×1536)+ 质量闸门 + 制片人审查流程 |
| 视频 | 双引擎:本地 Wan 2.2 5B(零成本)/ 即梦 Seedance API(商业级画质) |
| 配音 | edge-tts 神经音色 + 情绪标签自动变调([低沉][激昂]→语速/音调) |
| 成片 | 自动混音拼接,mp4 直发社交平台 |

## 架构

```
小说 → [Lumen 14B] 方向/梗概/草稿 → [qwen3.6 27B] 创作级改写
     → [SDXL] 角色/场景定妆图 → [双引擎] 逐镜生成(I2V角色锁定)
     → [edge-tts] 情绪旁白 → [ffmpeg] 拼接成片
```

- **双引擎**:settings 中 `视频引擎` 一键切换 comfyui(本地)/ jimeng(即梦 API)
- **故事库**:每故事独立目录,断点续跑,全流程无人值守
- **GPU 排他**:LLM 与视频生成自动交接(ROCm 双进程会死锁)

## 快速开始

1. 环境:Windows + AMD RX 9000 系列(原生 ROCm)/ 或任意支持 ComfyUI 的 NVIDIA 卡
2. 依赖:`pip install -r requirements.txt`(见文末)
3. 配置:复制 `settings.example.json` 为 `settings.json`,填入即梦 API Key(可选,只用本地引擎可不填)
4. 开跑:

```bash
python pipeline.py new 我的故事 E:\path\to\story.txt   # 新建故事
python pipeline.py all                                  # 全自动出片
```

## 文档

- `docs/` — 需求文档、项目计划、决策记录(D1-D11)、复盘(24 个坑)、提示词模板、阶段总结
- `knowledgeBase/` — 项目知识库(总览/架构/决策/复盘/提示词体系/操作手册/引擎对比/外部资源)

## 实测数据

- 本地 Wan 5B:1280×544×121 帧(5 秒)/镜 ≈ 17 分钟,零成本
- 即梦 FAST:同规格 ≈ 2 分钟,约 2 元/镜
- 静态图(SDXL)已达主流 AI 绘画水平;视频画质与商业模型存在代差(详见 knowledgeBase/06)

## 致谢

分镜方法论参考 [zenstory-ai/drama-skills](https://github.com/zenstory-ai/drama-skills)(MIT)的工业级分镜 SOP 与《本子不记债》等开源示例。

## License

MIT

---

## requirements.txt

```
imageio
imageio-ffmpeg
edge-tts
pillow
```
(ComfyUI 与 torch 按官方便携包安装;Ollama 按官网安装)
