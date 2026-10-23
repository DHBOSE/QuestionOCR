# -*- coding: utf-8 -*-
r"""
paddle_recognizer.py —— 百度 AI Studio PaddleOCR-VL 识别引擎（任务式 API）

与 OpenAI 兼容的 chat 接口不同，PaddleOCR-VL 是「提交任务 → 轮询 → 下载结果」
的三段式接口：
    1. POST {base_url}            multipart 上传图片，返回 jobId
    2. GET  {base_url}/{jobId}    轮询直到 state == done / failed
    3. GET  resultUrl.jsonUrl     下载 JSONL，取 layoutParsingResults 的
                                   markdown.text（正文）与 markdown.images（插图）

输出契约：返回 {"markdown": str}——整道题的 Markdown（插图已下载到本地，
引用改写为相对任务目录的 figures/w{n}/ 路径并附带 Pandoc 尺寸属性），
由 api.py 按 preset 类结果进入预览管线（选项分行 / 填空还原等后处理一致）。

Access Token 由前端随请求传入（或读环境变量 PADDLEOCR_ACCESS_TOKEN），不落盘。
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from api_recognizer import _cleanup_markdown_formulas
from converter import _image_size_attr, strip_question_number

logger = logging.getLogger(__name__)

DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
KEY_ENV = "PADDLEOCR_ACCESS_TOKEN"

POLL_INTERVAL = 5     # 轮询间隔（秒）
JOB_TIMEOUT = 600     # 单个任务最长等待（秒）
DOWNLOAD_TIMEOUT = 120
SUBMIT_MAX_EDGE = 2000  # 提交前把长边压到该像素以内，减小上传与排队耗时

# 提交任务时的可选开关（与官方示例一致：关闭方向分类/去畸变/图表识别，速度更快）
OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


def _http_json(req: urllib.request.Request, timeout: int, what: str) -> dict:
    """发送请求并解析 JSON 响应；网络 / 鉴权 / 参数错误统一转为中文 RuntimeError。"""
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise  # 交给下方统一翻译
        except (urllib.error.URLError, OSError) as first_err:
            if not urllib.request.getproxies():
                raise
            logger.info("经系统代理访问 PaddleOCR 失败（%s），改用直连重试……", first_err)
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with direct.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        if e.code in (401, 403):
            raise RuntimeError(
                f"PaddleOCR 鉴权失败（HTTP {e.code}），请检查 Access Token 是否正确。{detail}")
        if e.code == 429:
            raise RuntimeError(f"PaddleOCR 请求被限流（HTTP 429），请稍后重试。{detail}")
        raise RuntimeError(f"PaddleOCR {what}失败（HTTP {e.code}）：{detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 PaddleOCR 服务：{e.reason}")
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"PaddleOCR {what}失败（网络错误）：{e}")


def _multipart_body(fields: dict, file_field: str, file_path: str) -> tuple:
    """手工构造 multipart/form-data 请求体，返回 (body_bytes, content_type)。"""
    boundary = f"----s2qw{uuid.uuid4().hex}"
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
            .encode("utf-8")
        )
    filename = os.path.basename(file_path)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        .encode("utf-8")
    )
    parts.append(Path(file_path).read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _shrink_for_submit(image_path: str, log) -> tuple:
    """
    提交前把超大图缩放到长边 ≤ SUBMIT_MAX_EDGE（上传更快、服务端排队更短）。
    返回 (实际提交路径, 是否临时文件)；原图不大时原样返回。
    """
    from PIL import Image
    try:
        img = Image.open(image_path)
        w, h = img.size
        if max(w, h) <= SUBMIT_MAX_EDGE:
            return image_path, False
        scale = SUBMIT_MAX_EDGE / max(w, h)
        img = img.convert("RGB").resize(
            (round(w * scale), round(h * scale)), Image.LANCZOS)
        tmp = Path(image_path).parent / f".submit_{uuid.uuid4().hex[:8]}.jpg"
        img.save(tmp, "JPEG", quality=90)
        log(f"  图片较大（{w}×{h}），已压缩至长边 {SUBMIT_MAX_EDGE}px 再提交")
        return str(tmp), True
    except Exception:
        return image_path, False


def _submit_job(image_path: str, cfg: dict) -> str:
    """上传图片提交识别任务，返回 jobId。"""
    body, content_type = _multipart_body(
        {"model": cfg["model"], "optionalPayload": json.dumps(OPTIONAL_PAYLOAD)},
        "file", image_path,
    )
    req = urllib.request.Request(
        url=cfg["base_url"], data=body, method="POST",
        headers={"Authorization": f"bearer {cfg['api_key']}", "Content-Type": content_type},
    )
    resp = _http_json(req, timeout=180, what="提交任务")
    try:
        return resp["data"]["jobId"]
    except (KeyError, TypeError):
        raise RuntimeError(f"PaddleOCR 提交任务返回格式异常：{str(resp)[:300]}")


def _poll_job(cfg: dict, job_id: str, log) -> str:
    """轮询任务状态直到完成，返回结果 JSONL 的下载地址。
    日志去重：状态或进度变化时才输出，避免多任务并发时刷屏。"""
    url = f"{cfg['base_url']}/{job_id}"
    headers = {"Authorization": f"bearer {cfg['api_key']}"}
    deadline = time.time() + JOB_TIMEOUT
    tag = job_id[-6:]  # 短标签，区分并发任务
    last_msg = None
    while True:
        req = urllib.request.Request(url=url, headers=headers, method="GET")
        data = _http_json(req, timeout=60, what="查询任务").get("data") or {}
        state = data.get("state")
        if state == "done":
            log(f"  [{tag}] 识别完成，正在下载结果……")
            try:
                return data["resultUrl"]["jsonUrl"]
            except (KeyError, TypeError):
                raise RuntimeError(f"PaddleOCR 任务完成但未返回结果地址：{str(data)[:300]}")
        if state == "failed":
            raise RuntimeError(f"PaddleOCR 识别失败：{data.get('errorMsg') or '未知原因'}")
        if time.time() > deadline:
            raise RuntimeError(f"PaddleOCR 识别超时（超过 {JOB_TIMEOUT // 60} 分钟仍未完成）")
        progress = data.get("extractProgress") or {}
        msg = (f"  [{tag}] PaddleOCR 识别中（{state}，"
               f"{progress.get('extractedPages', '?')}/{progress.get('totalPages', '?')} 页）……")
        if msg != last_msg:
            log(msg)
            last_msg = msg
        time.sleep(POLL_INTERVAL)


def _download(url: str, timeout: int = DOWNLOAD_TIMEOUT) -> bytes:
    """下载结果文件 / 插图（结果地址自带签名，无需鉴权头）。"""
    try:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError):
            if not urllib.request.getproxies():
                raise
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with direct.open(url, timeout=timeout) as resp:
                return resp.read()
    except Exception as e:
        raise RuntimeError(f"PaddleOCR 结果下载失败：{e}")


def _rewrite_images(md_text: str, images: dict, out_dir: str, rel_prefix: str, log) -> str:
    """
    下载 markdown.images 中的插图到本地，并把 Markdown 里的引用改写为
    相对任务目录的路径 + Pandoc 尺寸属性（与本地/API 引擎的插图规格一致）。
    PaddleOCR-VL 的插图引用有两种形态，都处理：
      - Markdown：![](imgs/xxx.jpg)
      - HTML：    <div style="text-align: center;"><img src="imgs/xxx.jpg" .../></div>
    images: {markdown 中引用的路径: 下载 URL}
    """
    for ref, url in (images or {}).items():
        fname = os.path.basename(urllib.parse.urlparse(ref).path) or f"fig_{uuid.uuid4().hex[:8]}.jpg"
        save_path = Path(out_dir) / fname
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(_download(url))
        except RuntimeError as e:
            log(f"  ⚠️ 插图下载失败，保留原始引用：{ref}（{e}）")
            continue
        rel = f"{rel_prefix}/{fname}"
        size_attr = _image_size_attr(str(save_path))
        for variant in {ref, fname, ref.lstrip("./")}:
            # HTML 形态：连外包的居中 <div> 一起替换为 Markdown 图片（独占段落）
            md_text = re.sub(
                rf'<div[^>]*>\s*<img[^>]*src="{re.escape(variant)}"[^>]*>?\s*(?:</img>)?\s*</div>',
                f"\n\n![]({rel}){size_attr}\n\n", md_text, flags=re.IGNORECASE)
            md_text = re.sub(
                rf'<img[^>]*src="{re.escape(variant)}"[^>]*>',
                f"![]({rel}){size_attr}", md_text, flags=re.IGNORECASE)
            # Markdown 形态
            md_text = md_text.replace(f"]({variant})", f"]({rel}){size_attr}")
        log(f"  下载插图：{rel}")
    return md_text


def _html_tables_to_markdown(md_text: str, log) -> str:
    """
    把 PaddleOCR-VL 输出的 HTML <table> 转为 Markdown 表格，
    使预览界面（markdown-it 禁用 HTML）也能正常渲染；Pandoc 两种都认。
    解析失败时保留原 HTML 不动。
    """
    def _conv(m):
        tbl = m.group(0)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.DOTALL | re.IGNORECASE)
        parsed = []
        for r in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.DOTALL | re.IGNORECASE)
            cells = [re.sub(r"<[^>]+>", "", c).strip().replace("|", "\\|")
                     .replace("\n", " ") for c in cells]
            if cells:
                parsed.append(cells)
        if not parsed:
            return tbl
        width = max(len(r) for r in parsed)
        parsed = [r + [""] * (width - len(r)) for r in parsed]
        lines = ["| " + " | ".join(parsed[0]) + " |",
                 "|" + " --- |" * width]
        lines += ["| " + " | ".join(r) + " |" for r in parsed[1:]]
        return "\n".join(lines)

    return re.sub(r"<table[^>]*>.*?</table>", _conv, md_text,
                  flags=re.DOTALL | re.IGNORECASE)


def paddle_recognize(image_path: str, image_out_dir: str, cfg: dict, log=logger.info) -> dict:
    """
    用 PaddleOCR-VL 识别单张题目截图，返回 {"markdown": 整题 Markdown}。

    参数：
        image_path:     题目截图路径
        image_out_dir:  插图保存目录（task_dir/figures/w{n}）
        cfg:            resolve_api_config() 返回的配置（provider == "paddleocr"）
        log:            日志回调
    """
    image_path = str(image_path)
    log(f"PaddleOCR 识别（{cfg['model']}）：{os.path.basename(image_path)}")

    job_id = None
    submit_path, is_tmp = _shrink_for_submit(image_path, log)
    try:
        job_id = _submit_job(submit_path, cfg)
    finally:
        if is_tmp:
            try:
                os.remove(submit_path)
            except OSError:
                pass
    log(f"  任务已提交（jobId：{job_id}），等待识别结果……")
    jsonl_url = _poll_job(cfg, job_id, log)

    raw = _download(jsonl_url).decode("utf-8")
    # 结果地址目录前缀 = task_dir/figures/w{n} 相对任务目录的路径
    rel_prefix = os.path.relpath(image_out_dir, Path(image_out_dir).parent.parent).replace("\\", "/")

    md_parts = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results = json.loads(line).get("result", {}).get("layoutParsingResults", [])
        except json.JSONDecodeError:
            continue
        for res in results:
            md = ((res.get("markdown") or {}).get("text") or "").strip()
            if not md:
                continue
            md = _rewrite_images(md, res["markdown"].get("images"),
                                 image_out_dir, rel_prefix, log)
            md = _html_tables_to_markdown(md, log)
            md_parts.append(md)

    if not md_parts:
        raise RuntimeError("PaddleOCR 识别结果为空。")

    markdown = _cleanup_markdown_formulas("\n\n".join(md_parts), log)
    # 去掉题号（与本地/API 引擎一致）：先剥标准形式（9. / 9、 / (9) 等），
    # 再剥 PaddleOCR-VL 特有的「序号紧跟来源标签」形式（如 2[2026 江苏宿迁期中]）
    markdown = strip_question_number(markdown)
    markdown = re.sub(r"^\s*\d{1,3}\s*(?=[\[【])", "", markdown)
    log(f"PaddleOCR 识别完成：{len(md_parts)} 个版面块，正文 {len(markdown)} 字")
    return {"markdown": markdown}
