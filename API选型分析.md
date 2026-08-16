# 远程 API 识别选型分析

> 2026-08-11 ｜ 目标：找到能替代/补充本地 Pix2Text 管线的远程识别方案，
> 要求：中文题目 OCR + 公式识别（输出 LaTeX）+ 题目插图定位（裁剪）。

## 一、本地管线需要被替代的 4 项能力

| 能力 | 本地实现 | API 侧对应要求 |
|---|---|---|
| 中文文字 OCR | Pix2Text (CnOCR) | 多模态大模型的图像文字理解 |
| 公式识别 → LaTeX | pix2tex + UniMERNet | 模型直接输出 LaTeX（$...$） |
| 插图区域裁剪 | 版面分析 FIGURE 块 | 模型输出插图 bbox（grounding 能力） |
| 题目结构重组 | converter.py | 提示词约定输出格式（JSON） |

关键分水岭：**能否返回插图边界框（bbox）**。没有 bbox 的模型只能把插图写成占位符，无法裁剪图片嵌入 Word。

## 二、候选 API 对比

| 服务 / 模型 | 接口形式 | 中文 OCR | 公式 LaTeX | 插图 bbox | 备注 |
|---|---|---|---|---|---|
| **阿里 通义千问 qwen-vl-max / qwen-vl-plus** | DashScope（OpenAI 兼容） | ★★★★★ | ★★★★ | ✅ 支持 grounding（bbox 输出稳定） | 中文试卷场景最强，国内直连，**首选** |
| 阿里 Qwen2.5-VL-72B-Instruct | DashScope | ★★★★★ | ★★★★ | ✅ | 同系列开源权重版本，效果略强于 VL-Max 的部分基准 |
| **智谱 GLM-4V / GLM-4.5V** | 智谱开放平台（OpenAI 兼容） | ★★★★ | ★★★★ | ✅（4.5V 起支持 grounding） | 国内直连，价格便宜，备选 |
| **字节 豆包 doubao-vision-pro** | 火山方舟（OpenAI 兼容） | ★★★★ | ★★★★ | ⚠️ bbox 支持不如 Qwen 稳定 | 中文强，响应快 |
| **Google Gemini 2.5 Flash / Pro** | Google AI（有 OpenAI 兼容端点） | ★★★★ | ★★★★★ | ✅（官方支持 bbox，归一化 0-1000） | 公式识别强；需可访问 Google 的网络 |
| OpenAI GPT-4o / GPT-4.1 | OpenAI | ★★★★ | ★★★★ | ❌（不能可靠输出像素级 bbox） | 插图只能占位，不推荐单独使用 |
| **Mathpix** | 专用 OCR REST API | ★★★（中文一般） | ★★★★★（业界最强） | ✅（返回行级/区域坐标） | 专为公式 OCR 设计，可直接返回 docx；按页付费、英文界面、中文排版弱；适合作为"公式专用"补充 |
| DeepSeek | 官方 API 暂无公网可用的图像理解接口 | — | — | — | 截至 2026-08 其开放 API 不支持图片输入，不可用 |

## 三、结论

1. **首选：qwen-vl-max（DashScope）**——中文题目 OCR 与公式识别均强，原生支持 bbox 定位插图，接口 OpenAI 兼容，国内网络直连。
2. **备选：GLM-4.5V / 豆包 vision / Gemini 2.5 Flash**——同一套 OpenAI 兼容协议，只改 base_url + model 即可切换。
3. **不推荐：GPT-4o**（无 bbox 能力）、**DeepSeek**（无图像接口）。
4. **Mathpix** 适合对公式准确率要求极端高的场景，可作为第三引擎另行接入（非 OpenAI 协议）。

## 四、本项目实现方式

- 后端新增 `api_recognizer.py`：统一走 **OpenAI 兼容 Chat Completions 协议**，
  通过提示词让模型输出 JSON（正文 Markdown + 插图 bbox 千分比坐标），
  bbox 由后端用 PIL 在本地裁剪——**任何 OpenAI 兼容的视觉模型都能即插即用**。
- 前端：每张题目截图可独立选择「本地模型」或「API 识别」；
  API 密钥只保存在浏览器 localStorage，随请求发送，不写入服务端文件。
