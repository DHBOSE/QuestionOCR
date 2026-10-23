# -*- coding: utf-8 -*-
r"""
answer_gen.py —— 答案与解析生成（DeepSeek 等 OpenAI 兼容大模型）

与识别引擎（api_recognizer，多模态看图）完全解耦：
识别完成后，把每题的「原题图片 + 识别文字（Markdown，供参考）」发给模型生成「答案 + 解析」；
模型以原题图片为准作答。没有原图的场景（如 Word 直转）自动退化为纯文字请求；
若所用模型不支持图片输入（返回图片/多模态相关错误），自动回退为纯文字重试一次。

约定：
- 生成结果以固定标记块追加到题目末尾，供导出学生版时剥离（见 converter.strip_answers）；
- API Key 由前端随请求传入（或读环境变量 DEEPSEEK_API_KEY），不落盘。
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from api_recognizer import post_chat_completions, _image_to_data_url

logger = logging.getLogger(__name__)

# DeepSeek 官方端点（OpenAI 兼容协议）；base_url / model 前端均可改
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
KEY_ENV = "DEEPSEEK_API_KEY"

MAX_WORKERS = 3  # 与识别一致的并发度

ANSWER_PROMPT = """你是一位严谨的教师。请为这道题目给出答案与简要解析。

输出格式（严格遵守，只输出这两段）：
答案：<直接给出答案；选择题给选项字母，填空题给所填内容，计算题给结果>
解析：<简要说明解题思路与依据，2-4 句；涉及公式时用 LaTeX，行内 $...$，独立成行 $$...$$>

要求：
1. 不要复述题目，不要输出"答案/解析"两段以外的任何内容；
2. 优先依据原题图片作答；附带的识别文字（Markdown）仅供参考，若与图片不符以图片为准；
3. 题目无解或信息严重不足时，答案写"无法确定"，解析简述原因。

识别的题目文字（Markdown，供参考）：
{question}"""


def resolve_ds_config(api_key: str = "", base_url: str = "", model: str = "") -> dict:
    """
    合并前端传入的 DeepSeek 配置与默认值。
    缺少密钥时抛 ValueError（由上层转为友好提示）。
    """
    cfg = {
        "provider": "deepseek",  # 仅供用量统计与日志标识
        "api_key": (api_key or "").strip() or os.environ.get(KEY_ENV, ""),
        "base_url": (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL,
        "model": (model or "").strip() or DEFAULT_MODEL,
    }
    if not cfg["api_key"]:
        raise ValueError(
            "未提供 DeepSeek API Key。请在前端「API 设置」的「答案生成（DeepSeek）」"
            f"区块填写，或设置环境变量 {KEY_ENV}。"
        )
    return cfg


def _parse_answer(content: str) -> str | None:
    """
    规整模型输出：要求模型输出「答案：…」「解析：…」两段。
    返回规整后的 Markdown 文本；输出完全为空时返回 None。
    """
    text = content.strip()
    if not text:
        return None
    # 去掉模型擅自加的代码围栏
    fence = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.DOTALL)
    if fence and "答案" not in text[: text.find("```")]:
        text = fence.group(1).strip()
    return text


def generate_answers(questions: list, cfg: dict, log=logger.info,
                     question_imgs: list | None = None) -> dict:
    """
    为每道题生成答案与解析（并行，最多 MAX_WORKERS 路）。
    question_imgs: 与 questions 对齐的原图路径列表（每项为路径 list，可为空）——
                   有原图时以多模态形式连同识别文字一起发给模型，模型以图片为准。
    返回：
        {
          "answers": ["答案与解析文本" 或 None（失败）, ...],   # 与 questions 等长
          "failed":  [第 N 题（从 1 计）, ...],
          "usage":   {"calls", "prompt_tokens", "completion_tokens", "total_tokens"},
          "usages":  [单次用量 dict, ...]   # 供 api.py 累计到本地统计
        }
    """
    total = len(questions)
    answers = [None] * total
    failed = []
    usages = []
    if not question_imgs or len(question_imgs) != total:
        question_imgs = [[] for _ in range(total)]

    # 报错文案中出现这些关键词时，认为模型不支持图片输入，回退纯文字重试一次
    NO_VISION_HINTS = ("image_url", "image", "multimodal", "vision", "图片",
                       "not support", "unsupported", "does not support")

    def one(idx: int, qmd: str, imgs: list):
        # 有原图：多模态消息（图片 + 识别文字供参考）；无原图（Word 直转等）：纯文字
        prompt_text = ANSWER_PROMPT.format(question=qmd)
        content = prompt_text
        if imgs:
            content = [
                {"type": "image_url", "image_url": {"url": _image_to_data_url(p)}}
                for p in imgs
            ]
            content.append({"type": "text", "text": prompt_text})
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.3,  # 允许少量表述多样性，但保持答案确定性
        }
        try:
            text, usage = post_chat_completions(cfg, payload)
        except Exception as e:
            # 所用模型不支持图片输入时，回退为纯文字重试一次
            if imgs and any(h in str(e).lower() for h in NO_VISION_HINTS):
                log(f"  ⚠️ 第 {idx + 1} 题：{cfg['model']} 不支持图片输入，改用纯文字重试")
                payload["messages"] = [{"role": "user", "content": prompt_text}]
                text, usage = post_chat_completions(cfg, payload)
            else:
                raise
        return idx, _parse_answer(text), usage

    log(f"开始生成答案（{cfg['provider']} / {cfg['model']}，共 {total} 题，"
        f"{sum(1 for im in question_imgs if im)} 题带原图，"
        f"最多 {min(MAX_WORKERS, total)} 路并发）……")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(one, i, q, question_imgs[i]): i for i, q in enumerate(questions)
        }
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                idx, answer, usage = fut.result()
                if answer:
                    answers[idx] = answer
                    log(f"  ✅ 第 {idx + 1} 题答案已生成")
                else:
                    failed.append(idx + 1)
                    log(f"  ❌ 第 {idx + 1} 题答案为空")
                if usage:
                    usages.append({
                        "provider": cfg["provider"],
                        "model": cfg["model"],
                        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                        "completion_tokens": int(usage.get("completion_tokens") or 0),
                        "total_tokens": int(usage.get("total_tokens") or 0),
                    })
            except Exception as e:  # 单题失败不中断整体
                failed.append(i + 1)
                log(f"  ❌ 第 {i + 1} 题答案生成失败：{e}")

    usage_sum = None
    if usages:
        usage_sum = {
            "calls": len(usages),
            "prompt_tokens": sum(u["prompt_tokens"] for u in usages),
            "completion_tokens": sum(u["completion_tokens"] for u in usages),
            "total_tokens": sum(u["total_tokens"] for u in usages),
        }
    failed.sort()
    return {"answers": answers, "failed": failed, "usage": usage_sum, "usages": usages}
