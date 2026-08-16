# -*- coding: utf-8 -*-
r"""
api_recognizer.py —— 远程多模态 API 识别引擎（本地模型的可替代方案）

原理：
1. 把题目截图以 base64 形式发给 OpenAI 兼容协议的视觉大模型
   （qwen-vl-max / GLM-4.5V / 豆包 vision / Gemini 等均可即插即用）
2. 通过提示词要求模型输出 JSON：
   - text：题目正文 Markdown，公式用 $...$ / $$...$$ LaTeX 表示
   - figures：题目中插图区域的 bbox（相对整图宽高的千分比坐标）
3. 后端在本地按 bbox 用 PIL 裁剪插图，输出与本地引擎一致的
   {"elements": [...]} 结构，后续 converter / docx_builder 完全复用。

注意：API Key 由前端随请求传入（或读取环境变量），本模块不落盘保存。
"""

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

# 复用本地引擎的 LaTeX 清理与 Pandoc 校验，保证两种引擎的公式质量一致
from recognizer import cleanup_latex, pandoc_accepts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 服务商预设（全部为 OpenAI 兼容 Chat Completions 协议）
# 前端 GET /api-providers 读取该列表渲染下拉框
# ---------------------------------------------------------------------------
PROVIDERS = {
    "qwen": {
        "name": "通义千问 Qwen-VL（阿里 DashScope，推荐）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "zhipu": {
        "name": "智谱 GLM-4V",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-plus",
        "key_env": "ZHIPU_API_KEY",
    },
    "doubao": {
        "name": "豆包 Vision（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-vision-pro-32k-241028",
        "key_env": "ARK_API_KEY",
    },
    "gemini": {
        "name": "Gemini（Google，需可访问 Google 网络）",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "key_env": "GEMINI_API_KEY",
    },
    "custom": {
        "name": "自定义（任意 OpenAI 兼容端点）",
        "base_url": "",
        "model": "",
        "key_env": "",
    },
}

REQUEST_TIMEOUT = 180  # 单次 API 请求超时（秒）

# 识别提示词：要求输出固定结构的 JSON，bbox 用千分比坐标（与 Qwen 约定一致，
# 其他模型也能理解该约定）
PROMPT = """你是一名题目识别助手。请识别这张题目截图，只输出一个 JSON 对象，格式如下：
{
  "text": "题目正文的 Markdown 文本",
  "figures": [{"location": "插图在图中的大致方位", "bbox": [x1, y1, x2, y2], "description": "插图简述"}]
}

要求：
1. text 包含题干与所有选项的完整文字，保持原文，不要增删内容（题目序号、出处标签等也原样保留，由后端统一处理）；
2. 所有数学/物理公式用 LaTeX 表示：行内公式用 $...$ 包裹，独立成行的公式用 $$...$$ 包裹；
3. 填空题留空处的下划线必须保留，用一串半角下划线 ______ 表示该空；
4. 插图下方或旁边的图注（如"甲""乙""图1"）若有，各自作为独立的一行放在 text 末尾；
5. figures 只收录题目中的插图/图形（如几何图形、坐标系、电路图、表格图片），严禁把纯文字区域当作插图；
6. 先填写 location（如"图片右侧中部"），再根据该方位仔细确定 bbox：bbox 是该插图区域相对整张图片宽高的千分比整数坐标（0-1000），顺序为左上 x、左上 y、右下 x、右下 y，必须完整包住插图且尽量不包含正文文字；没有插图时输出空数组；
7. 只输出 JSON 本身，不要输出 markdown 代码围栏，不要输出任何解释。"""


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
def resolve_api_config(provider: str, api_key: str = "", base_url: str = "", model: str = "", workspace: str = "") -> dict:
    """
    合并前端传入配置与服务商预设，返回最终请求配置。
    api_key 为空时尝试读取该服务商对应的环境变量。
    workspace 为 DashScope 业务空间 ID（可选，仅 qwen 有效，
    只有调用部署在特定业务空间里的模型时才需要）。
    缺少密钥或地址时抛出 ValueError（由上层转为友好错误提示）。
    """
    preset = PROVIDERS.get(provider, PROVIDERS["custom"])

    cfg = {
        "provider": provider,
        "api_key": api_key or (os.environ.get(preset["key_env"], "") if preset["key_env"] else ""),
        "base_url": (base_url or preset["base_url"]).rstrip("/"),
        "model": model or preset["model"],
        "workspace": workspace.strip(),
    }
    if not cfg["api_key"]:
        raise ValueError(
            f"未提供 API Key（服务商：{preset['name']}）。"
            f"请在前端「API 设置」中填写，或设置环境变量 {preset['key_env'] or '（对应服务商的密钥变量）'}。"
        )
    if not cfg["base_url"]:
        raise ValueError("未提供 API 地址（base_url），请在前端「API 设置」中填写。")
    if not cfg["model"]:
        raise ValueError("未提供模型名称，请在前端「API 设置」中填写。")
    return cfg


# ---------------------------------------------------------------------------
# 请求与响应解析
# ---------------------------------------------------------------------------
def _image_to_data_url(image_path: str) -> str:
    """读取图片并编码为 data URL（供 chat completions 的 image_url 使用）。"""
    suffix = Path(image_path).suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _call_chat_completions(cfg: dict, image_path: str):
    """
    调用 OpenAI 兼容的 /chat/completions 接口。
    返回 (模型输出文本, token 用量 dict)；用量取自响应的 usage 字段，
    服务商未返回时为 None。网络 / 鉴权 / 参数错误统一转为带中文说明的 RuntimeError。
    """
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _image_to_data_url(image_path)}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "temperature": 0.1,  # 识别任务尽量确定性输出
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    # DashScope 业务空间（可选）：填了才带该请求头
    if cfg.get("workspace") and cfg.get("provider") == "qwen":
        headers["X-DashScope-WorkSpace"] = cfg["workspace"]
    req = urllib.request.Request(
        url=f"{cfg['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        if e.code in (401, 403):
            raise RuntimeError(f"API 鉴权失败（HTTP {e.code}），请检查 API Key 是否正确。{detail}")
        if e.code == 429:
            raise RuntimeError(f"API 请求被限流（HTTP 429），请稍后重试。{detail}")
        raise RuntimeError(f"API 请求失败（HTTP {e.code}）：{detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 API 服务（{cfg['base_url']}）：{e.reason}")
    except TimeoutError:
        raise RuntimeError(f"API 请求超时（>{REQUEST_TIMEOUT}s），请检查网络或更换服务商。")

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"API 返回格式异常：{str(body)[:500]}")
    # token 用量（OpenAI 兼容协议的可选字段，qwen / 智谱 / 豆包 / gemini 均会返回）
    usage = body.get("usage") if isinstance(body, dict) else None
    return content, usage


def _parse_json_response(content: str) -> dict:
    """
    从模型输出中稳健地提取 JSON 对象。
    兼容模型擅自包裹 markdown 代码围栏或在 JSON 前后加解释文字的情况。
    """
    text = content.strip()
    # 去掉 ```json ... ``` 代码围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 截取第一个 { 到最后一个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"模型未返回有效 JSON：{content[:300]}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# LaTeX 后处理（复用本地引擎的清理与校验）
# ---------------------------------------------------------------------------
def _cleanup_markdown_formulas(md_text: str, log) -> str:
    """
    对 Markdown 中的每个 $...$ / $$...$$ 公式段做清理与 Pandoc 校验。
    校验失败的公式保留原文并记录日志（API 模式下无逐公式精确坐标，
    无法像本地引擎那样裁剪单个公式图片兜底）。
    """
    def _fix(m):
        latex = cleanup_latex(m.group(2))
        display = m.group(1) == "$$"
        if latex and pandoc_accepts(latex, display):
            return f"{m.group(1)}{latex}{m.group(1)}"
        log(f"  警告：公式可能无法在 Word 中正确渲染，已保留原文：{latex[:80]}")
        return m.group(0)

    # 先匹配 $$...$$（独立公式），再匹配 $...$（行内公式）
    md_text = re.sub(r"(\$\$)(.+?)\$\$", _fix, md_text, flags=re.DOTALL)
    md_text = re.sub(r"(\$)([^$\n]+?)\$", _fix, md_text)
    return md_text.strip()


def _crop_figures(src_img: Image.Image, figures: list, out_dir: str, log) -> list:
    """
    按千分比 bbox 裁剪题目插图并保存，返回 image 元素列表。
    非法或越界的 bbox 会被跳过并记录日志。
    """
    elements = []
    w, h = src_img.size
    for idx, fig in enumerate(figures, start=1):
        bbox = fig.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            log(f"  跳过第 {idx} 个插图：bbox 格式非法（{bbox}）")
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in bbox)
        except (TypeError, ValueError):
            log(f"  跳过第 {idx} 个插图：bbox 含非数字（{bbox}）")
            continue
        # 千分比坐标 → 像素坐标（允许少量越界，裁剪时收敛到图内）
        px1 = max(0, int(x1 / 1000 * w) - 4)
        py1 = max(0, int(y1 / 1000 * h) - 4)
        px2 = min(w, int(x2 / 1000 * w) + 4)
        py2 = min(h, int(y2 / 1000 * h) + 4)
        if px2 - px1 < 10 or py2 - py1 < 10:
            log(f"  跳过第 {idx} 个插图：区域过小或坐标越界（{bbox}）")
            continue
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        save_path = Path(out_dir) / f"figure_{idx}.png"
        src_img.crop((px1, py1, px2, py2)).save(save_path)
        elements.append({"type": "image", "text": "", "image_path": str(save_path),
                         "box": [px1, py1, px2, py2]})
        log(f"  裁剪出插图：figure_{idx}.png")
    return elements


# ---------------------------------------------------------------------------
# 主识别流程（与 recognizer.recognize_question 输出契约一致）
# ---------------------------------------------------------------------------
def api_recognize_question(
    image_path: str,
    image_out_dir: str,
    api_config: dict,
    log=logger.info,
    skip_figures: bool = False,
) -> dict:
    """
    用远程多模态 API 识别单张题目截图。

    参数：
        image_path:    题目截图路径
        image_out_dir: 裁剪插图保存目录
        api_config:    resolve_api_config() 返回的配置 dict
        log:           日志回调
        skip_figures:  True 时忽略模型返回的插图 bbox（混合模式下插图由本地
                       版面分析裁剪，见 recognizer.detect_figure_crops）

    返回：
        {"elements": [{"type": "text" | "image", ...}, ...]}
    """
    image_path = str(image_path)
    log(f"API 识别（{api_config['provider']} / {api_config['model']}）：{os.path.basename(image_path)}")

    content, usage = _call_chat_completions(api_config, image_path)
    try:
        data = _parse_json_response(content)
    except (RuntimeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"API 返回内容解析失败：{e}")

    elements = []

    # 正文文字（公式已清理校验）
    md_text = (data.get("text") or "").strip()
    if md_text:
        md_text = _cleanup_markdown_formulas(md_text, log)
        elements.append({"type": "text", "text": md_text, "image_path": None})
    else:
        log("  警告：API 未返回正文文字")

    # 插图裁剪（混合模式下跳过，插图由本地版面分析负责）
    figures = data.get("figures") or []
    if skip_figures:
        if figures:
            log("  混合模式：忽略 API 返回的插图坐标，改用本地版面分析裁剪")
    elif isinstance(figures, list) and figures:
        src_img = Image.open(image_path).convert("RGB")
        elements.extend(_crop_figures(src_img, figures, image_out_dir, log))

    if not elements:
        raise RuntimeError("API 识别结果为空（无文字也无插图）。")

    image_count = sum(1 for e in elements if e["type"] == "image")
    log(f"API 识别完成：{len(elements)} 个元素（含 {image_count} 张插图）")
    result = {"elements": elements}
    if usage:
        # token 用量随结果返回，由 api.py 汇总并累计到本地统计文件
        result["usage"] = {
            "provider": api_config["provider"],
            "model": api_config["model"],
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    return result
